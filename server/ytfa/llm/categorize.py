"""채널 자동 분류 (docs/05-구현가이드.md Phase 2, step 9).

채널명 + 설명 + 최근 영상 제목 10건 → 카테고리 자동 제안(FR-K1, docs/01
§4.2). 카테고리 목록은 고정이 아니므로(FR-K2) 처음엔 텅 비어 있다 — 이
모듈이 채널을 분류해나가면서 카테고리 자체를 함께 만들어간다. 다만
비슷한 카테고리가 난립하지 않도록, 기존 목록에 맞는 게 있으면 그 id를
그대로 재사용하라고 프롬프트에 못박는다.

`category_locked=True`인 채널(사용자가 손댄 채널)은 절대 건드리지
않는다 — 재분류 대상에서 아예 제외한다.
"""

from __future__ import annotations

from sqlite3 import Connection

from pydantic import BaseModel, Field

from ytfa.llm.cost import LLMClient

RECENT_VIDEO_LIMIT = 10
DEFAULT_CATEGORY_ID = "etc"
DEFAULT_CATEGORY_NAME = "기타"

SYSTEM_PROMPT = """유튜브 채널을 카테고리로 분류하는 도우미다. 채널명, 설명, 최근 영상
제목을 보고 이 채널에 맞는 카테고리를 1~2개 고른다.

규칙:
- 기존 카테고리 목록이 주어지면, 그 중에 맞는 게 있으면 반드시 그 id를
  정확히 그대로 재사용한다(is_new=false). 비슷한 이름을 새로 만들지
  않는다 — "dev"가 있는데 "programming"을 새로 만들지 않는다.
- 맞는 카테고리가 정말 없을 때만 새 카테고리를 제안한다(is_new=true).
  id는 소문자 영문 slug, 짧게(예: dev, ai, music, gaming, vlog, news,
  education, finance, cooking). name은 한글 표시명.
- 애매하면 카테고리 1개만 고른다. 명백히 두 영역을 걸치는 채널만 2개.
- "기타"/"etc" 카테고리는 절대 제안하지 않는다 — 분류가 안 되면
  시스템이 자동으로 처리한다.
"""


class CategoryAssignment(BaseModel):
    category_id: str = Field(description="소문자 영문 slug. 기존 목록에 맞는 게 있으면 그 id 그대로.")
    category_name: str = Field(description="한글 표시명, 예: '개발', 'AI'")
    is_new: bool = Field(description="기존 목록에 없는 새 카테고리를 제안하는 것이면 true")


class CategorizeResult(BaseModel):
    categories: list[CategoryAssignment] = Field(description="이 채널에 맞는 카테고리 1~2개")


def ensure_default_category(conn: Connection) -> None:
    """'기타' 카테고리가 항상 존재하도록 보장한다(Category.is_default)."""
    conn.execute(
        "INSERT INTO categories (id, name, ord, is_default) VALUES (?, ?, 0, 1) "
        "ON CONFLICT(id) DO NOTHING",
        (DEFAULT_CATEGORY_ID, DEFAULT_CATEGORY_NAME),
    )
    conn.commit()


def _existing_categories(conn: Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name FROM categories WHERE is_default = 0 ORDER BY ord"
    ).fetchall()
    return [{"id": r[0], "name": r[1]} for r in rows]


def _recent_video_titles(conn: Connection, channel_id: str, limit: int = RECENT_VIDEO_LIMIT) -> list[str]:
    rows = conn.execute(
        "SELECT title FROM videos WHERE channel_id = ? ORDER BY published_at DESC LIMIT ?",
        (channel_id, limit),
    ).fetchall()
    return [r[0] for r in rows]


def _build_prompt(existing: list[dict], title: str, description: str, titles: list[str]) -> str:
    existing_block = (
        "\n".join(f"- {c['id']}: {c['name']}" for c in existing)
        if existing
        else "(아직 없음 — 이 채널이 첫 카테고리를 만든다)"
    )
    titles_block = "\n".join(f"- {t}" for t in titles) or "(없음)"
    return (
        f"기존 카테고리 목록:\n{existing_block}\n\n"
        f"채널명: {title}\n설명: {description[:300]}\n최근 영상 제목:\n{titles_block}\n"
    )


def _normalize_category_id(raw: str) -> str:
    return raw.strip().lower().replace(" ", "_")


def _apply_assignments(conn: Connection, channel_id: str, assignments: list[CategoryAssignment]) -> None:
    known_ids = {c["id"] for c in _existing_categories(conn)}
    for a in assignments:
        cat_id = _normalize_category_id(a.category_id)
        # `is_new`는 모델이 매긴 힌트일 뿐 신뢰할 수 없다(오타·대소문자
        # 차이로 기존 id와 어긋나는 경우가 실제로 있었다) — 존재 여부는
        # 항상 known_ids로 직접 확인하고 없으면 만든다. FK 위반 방지.
        if cat_id not in known_ids:
            conn.execute(
                "INSERT INTO categories (id, name, ord, is_default) VALUES (?, ?, 0, 0) "
                "ON CONFLICT(id) DO NOTHING",
                (cat_id, a.category_name),
            )
            known_ids.add(cat_id)
        conn.execute(
            "INSERT INTO channel_categories (channel_id, category_id, assigned_by, confidence) "
            "VALUES (?, ?, 'auto', NULL) "
            "ON CONFLICT(channel_id, category_id) DO NOTHING",
            (channel_id, cat_id),
        )
    conn.execute("UPDATE channels SET needs_review = 1 WHERE id = ?", (channel_id,))
    conn.commit()


def categorize_channel(
    client: LLMClient, conn: Connection, channel_id: str
) -> tuple[list[CategoryAssignment], float] | None:
    """채널 하나를 분류해 저장한다. `category_locked`면 건드리지 않고 None."""
    row = conn.execute(
        "SELECT title, description, category_locked FROM channels WHERE id = ?",
        (channel_id,),
    ).fetchone()
    if row is None or row[2]:  # 없거나 잠긴 채널
        return None
    title, description, _locked = row

    existing = _existing_categories(conn)
    titles = _recent_video_titles(conn, channel_id)
    user_content = _build_prompt(existing, title, description, titles)

    response, cost = client.parse(
        model=client.cfg.llm.small_model,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
        max_tokens=512,
        output_format=CategorizeResult,
        kind="categorize",
        user_input=f"channel:{channel_id}",
        conn=conn,
    )
    result: CategorizeResult = response.parsed_output
    _apply_assignments(conn, channel_id, result.categories)
    return result.categories, cost


def categorize_all_pending(client: LLMClient, conn: Connection, limit: int | None = None) -> dict:
    """아직 분류되지 않은(잠기지 않은) 채널을 전부 분류한다.

    이미 `channel_categories` 행이 있는 채널은 건드리지 않는다 — 재실행
    해도 비용이 늘지 않는다(step 11과 같은 원칙).
    """
    ensure_default_category(conn)
    rows = conn.execute(
        """SELECT c.id FROM channels c
           WHERE c.category_locked = 0
           AND NOT EXISTS (SELECT 1 FROM channel_categories cc WHERE cc.channel_id = c.id)
           ORDER BY c.added_at"""
    ).fetchall()
    channel_ids = [r[0] for r in rows]
    if limit is not None:
        channel_ids = channel_ids[:limit]

    classified = 0
    total_cost = 0.0
    for channel_id in channel_ids:
        outcome = categorize_channel(client, conn, channel_id)
        if outcome is not None:
            classified += 1
            total_cost += outcome[1]

    return {"classified": classified, "candidates": len(channel_ids), "cost_usd": round(total_cost, 6)}
