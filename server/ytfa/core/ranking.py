"""1차 필터 + 브리핑 (docs/05-구현가이드.md Phase 2, step 12; docs/01 §4.5).

**LLM을 전혀 안 쓴다(FR-B1).** 채널 가중치·카테고리 선호·제목 유사도로
새 영상 중 후보 상위 N건(기본 5)만 추리고, **그 후보에 대해서만** 자막을
읽고(sources/transcript.py, FR-U5) llm/summarize.py로 요약한 뒤, 그 중
상위 M건(기본 3)을 이유와 함께 추천한다(FR-B2, FR-B3). 새 영상이 몇백
건이든 LLM 호출은 항상 `candidate_limit`개 이하다 — 이게 이 모듈이
보장해야 할 전부다.

**"제목 유사도"는 아직 임베딩이 아니다.** FR-B1 원문은 "제목 임베딩과
관심사 임베딩의 유사도"지만 임베딩(`rag/embedder.py`)은 Phase 8에나
나온다(지금은 `rag.level=0`, FTS5도 아직 색인 트리거가 없다 — Phase 3
step 15에서 붙는다). 그래서 여기서는 `watched` 표시된 영상들의 제목과
순수 토큰 겹침(Jaccard)만 본다 — 설정도 외부 의존성도 없이 바로
동작하고, 시청 이력이 없으면 자연스럽게 0으로 죽는다(신규 사용자는
채널 가중치·카테고리 선호만으로 시작). Phase 8에서 `_title_similarity`
내부만 코사인 유사도로 바꾸면 되고, 호출부는 안 바뀐다.

**카테고리 선호도도 설정값을 따로 안 둔다** — `watched` 비율로 암묵적
추론한다(많이 본 카테고리일수록 선호, 이력 없으면 중립 0.5). 이것도
스스로 적응한다는 게 핵심이다(FR-B4).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from sqlite3 import Connection

from ytfa.llm.cost import LLMClient
from ytfa.llm.summarize import summarize_videos
from ytfa.sources.transcript import TranscriptProvider, fetch_and_store_transcript

DEFAULT_CANDIDATE_LIMIT = 5
DEFAULT_BRIEFING_LIMIT = 3
DEFAULT_SINCE_HOURS = 48  # "새 영상"의 기준 — 폴링 주기(기본 1시간)보다 넉넉히

# 세 신호를 0~1로 정규화한 뒤 이 비율로 합산한다. 합이 1일 필요는 없다 —
# 상대적 비중만 중요하다.
WEIGHT_CHANNEL = 0.4
WEIGHT_CATEGORY = 0.3
WEIGHT_TITLE_SIM = 0.3

_TOKEN_RE = re.compile(r"[\w가-힣]+")


@dataclass
class RankedVideo:
    video_id: str
    title: str
    channel_id: str
    score: float
    reasons: list[str] = field(default_factory=list)


def _tokenize(title: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(title.lower()) if len(t) > 1}


def _candidate_videos(conn: Connection, since_hours: int, include_shorts: bool) -> list[dict]:
    """최근 `since_hours`시간 내 발행된, 아직 안 본 영상.

    `video_states` 행이 아직 하나도 안 생기는 초기 상태(Phase 3 REST가
    붙기 전)도 정상 동작하도록 `LEFT JOIN`으로 상태 없음(=새 영상)을
    허용한다.

    `published_at`을 `datetime()`으로 감싸는 이유: 우리가 저장한 ISO
    문자열과 sqlite의 `datetime('now', ...)` 출력 형식이 달라서, 안
    감싸면 같은 날짜의 과거 시각도 "범위 안"으로 잘못 걸린다(실측 버그,
    Phase 5에서 core/stats.py 만들다가 발견 — cost.py에도 있었다).
    """
    kind_filter = "" if include_shorts else "AND v.kind != 'short'"
    rows = conn.execute(
        f"""SELECT v.id, v.title, v.channel_id
            FROM videos v
            LEFT JOIN video_states vs ON vs.video_id = v.id
            WHERE datetime(v.published_at) >= datetime('now', ?)
            AND (vs.state IS NULL OR vs.state = 'new')
            {kind_filter}
            ORDER BY v.published_at DESC""",
        (f"-{since_hours} hours",),
    ).fetchall()
    return [{"id": r[0], "title": r[1], "channel_id": r[2]} for r in rows]


def _channel_weights(conn: Connection) -> dict[str, float]:
    return {r[0]: r[1] for r in conn.execute("SELECT id, weight FROM channels").fetchall()}


def _channel_categories(conn: Connection) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for channel_id, category_id in conn.execute(
        "SELECT channel_id, category_id FROM channel_categories"
    ).fetchall():
        mapping.setdefault(channel_id, []).append(category_id)
    return mapping


def _category_preference_scores(conn: Connection) -> dict[str, float]:
    """카테고리별 `watched` 비율. 이력이 없는 카테고리는 중립값 0.5."""
    rows = conn.execute(
        """SELECT cc.category_id,
                  SUM(CASE WHEN vs.state = 'watched' THEN 1 ELSE 0 END),
                  COUNT(*)
           FROM channel_categories cc
           JOIN videos v ON v.channel_id = cc.channel_id
           LEFT JOIN video_states vs ON vs.video_id = v.id
           GROUP BY cc.category_id"""
    ).fetchall()
    # 라플라스 스무딩: 관측(watched)이 없으면 정확히 중립값 0.5, 쌓일수록
    # 실제 비율로 수렴한다. `watched / total`을 그대로 쓰면 total이
    # 0이 아닌 한(거의 항상 그렇다) "시청 0건"이 그대로 0.0(비선호)으로
    # 읽혀 문서에 적은 중립 취지와 어긋난다.
    return {cat_id: (watched + 1) / (total + 2) for cat_id, watched, total in rows}


def _watched_title_tokens(conn: Connection) -> list[set[str]]:
    rows = conn.execute(
        """SELECT v.title FROM videos v
           JOIN video_states vs ON vs.video_id = v.id
           WHERE vs.state = 'watched'"""
    ).fetchall()
    return [_tokenize(r[0]) for r in rows]


def _title_similarity(candidate_tokens: set[str], watched_token_sets: list[set[str]]) -> float:
    """시청 이력 제목들과의 최대 Jaccard 유사도(0~1). 이력 없으면 0."""
    if not candidate_tokens or not watched_token_sets:
        return 0.0
    best = 0.0
    for watched in watched_token_sets:
        if not watched:
            continue
        overlap = len(candidate_tokens & watched) / len(candidate_tokens | watched)
        best = max(best, overlap)
    return best


def rank_candidates(
    conn: Connection,
    limit: int = DEFAULT_CANDIDATE_LIMIT,
    since_hours: int = DEFAULT_SINCE_HOURS,
    include_shorts: bool = False,
) -> list[RankedVideo]:
    """LLM 없이 새 영상 중 상위 `limit`건을 뽑는다(FR-B1)."""
    videos = _candidate_videos(conn, since_hours=since_hours, include_shorts=include_shorts)
    if not videos:
        return []

    channel_weights = _channel_weights(conn)
    channel_categories = _channel_categories(conn)
    category_scores = _category_preference_scores(conn)
    watched_tokens = _watched_title_tokens(conn)

    ranked: list[RankedVideo] = []
    for v in videos:
        channel_w = channel_weights.get(v["channel_id"], 1.0)
        cats = channel_categories.get(v["channel_id"], [])
        category_w = max((category_scores.get(c, 0.5) for c in cats), default=0.5)
        title_sim = _title_similarity(_tokenize(v["title"]), watched_tokens)

        # channel weight는 보통 0~2 범위(기본 1.0)라 절반으로 눌러 0~1에 맞춘다.
        channel_norm = min(channel_w / 2, 1.0)
        score = WEIGHT_CHANNEL * channel_norm + WEIGHT_CATEGORY * category_w + WEIGHT_TITLE_SIM * title_sim

        reasons = []
        if channel_norm > 0.5:
            reasons.append("평소 자주 보는 채널")
        if category_w > 0.5:
            reasons.append("좋아하는 카테고리")
        if title_sim > 0.3:
            reasons.append("예전에 본 영상과 비슷한 제목")

        ranked.append(
            RankedVideo(video_id=v["id"], title=v["title"], channel_id=v["channel_id"], score=score, reasons=reasons)
        )

    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked[:limit]


def build_briefing(
    client: LLMClient,
    conn: Connection,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    briefing_limit: int = DEFAULT_BRIEFING_LIMIT,
    since_hours: int = DEFAULT_SINCE_HOURS,
    include_shorts: bool = False,
    transcript_provider: TranscriptProvider | None = None,
) -> dict:
    """후보를 추리고(LLM 없음) 그것만 요약해서(step 11) 상위 N건을 추천한다.

    LLM 호출 횟수는 항상 `candidate_limit` 이하로 구조적으로 보장된다 —
    후보 목록 자체가 이미 그만큼만 잘려 있기 때문이다. `transcript_provider`는
    테스트에서 실제 유튜브 요청 없이 자막 조회를 흉내 내기 위한 주입 지점.
    """
    candidates = rank_candidates(
        conn, limit=candidate_limit, since_hours=since_hours, include_shorts=include_shorts
    )
    video_ids = [c.video_id for c in candidates]

    # FR-U5: 자막도 "필터를 통과한 후보"에 대해서만 읽는다 — 아직 안 가져온
    # (transcript_status='pending') 후보만 이 시점에 가져온다.
    for video_id in video_ids:
        row = conn.execute("SELECT transcript_status FROM videos WHERE id = ?", (video_id,)).fetchone()
        if row and row[0] == "pending":
            fetch_and_store_transcript(conn, video_id, transcript_provider)

    summarize_result = summarize_videos(client, conn, video_ids)

    picks = []
    for c in candidates[:briefing_limit]:
        row = conn.execute("SELECT summary, verdict FROM videos WHERE id = ?", (c.video_id,)).fetchone()
        summary, verdict_json = row if row else (None, None)
        picks.append(
            {
                "video_id": c.video_id,
                "title": c.title,
                "summary": summary,
                "verdict": json.loads(verdict_json) if verdict_json else None,
                "reasons": c.reasons or ["새로 올라온 영상"],
                "score": round(c.score, 3),
            }
        )

    return {
        "picks": picks,
        "candidates_considered": len(candidates),
        "summarize": summarize_result,
    }
