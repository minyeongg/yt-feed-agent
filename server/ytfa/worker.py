"""백그라운드 워커 — APScheduler로 RSS 폴링(5) + 메타 보강(6) +
L1/L2 인덱싱(Phase 8)을 주기 실행한다.

docs/05-구현가이드.md Phase 1, step 7 / docs/02 ADR-1(백그라운드 폴링은
서버 내장 APScheduler).

**L1/L2 인덱싱을 여기 넣은 이유**: Phase 8을 처음 만들 땐 `xba index`/
`xba index-transcripts`를 수동으로 돌려야 했다 — `set_video_state`가
`"queued_for_index": true`를 돌려주면서도 실제로는 아무 큐도 없었다
(정직하지 않은 응답이었다). 로컬 임베딩이라 비용이 0이고, CPU 강제
고정 이후엔 몇 초~몇십 초면 끝나서(step 38 실측) 폴링 주기 안에
자연스럽게 끼워 넣을 수 있다 — `INDEX_BATCH_LIMIT`으로 한 사이클당
처리량을 제한해서, 대량 백필이 있어도 폴링 자체가 오래 밀리지 않게
한다(그날 컴퓨터가 재부팅됐던 사고를 다시 겪지 않기 위한 안전장치).

`create_scheduler()`는 잡만 등록한 스케줄러를 반환한다 — main.py의 FastAPI
startup/shutdown 이벤트에서 start()/shutdown()을 호출해 서버 프로세스에
내장시키는 게 최종 형태다. 그 전까지는 이 파일을 직접 실행해 독립
프로세스로 띄울 수 있다:

    uv run python -m ytfa.worker
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from ytfa.config import Config, load_config
from ytfa.db import get_connection
from ytfa.rag.embedder import get_embedder
from ytfa.rag.indexer import index_l1, index_l2
from ytfa.sources.rss import poll_all_channels
from ytfa.sources.youtube_api import enrich_pending_videos

logger = logging.getLogger(__name__)

# 한 폴링 사이클당 인덱싱할 최대 건수. 대량 백필(수천 건)이 한 번에
# 걸리면 폴링 주기 자체가 밀린다 — 여러 사이클에 걸쳐 자연히 소진된다.
INDEX_BATCH_LIMIT = 200


def run_poll_cycle(cfg: Config | None = None) -> dict:
    """RSS 폴링 → 메타 보강 → L1/L2 인덱싱을 한 번 실행한다. 잡 함수이자
    수동 호출용 함수."""
    cfg = cfg or load_config()
    with get_connection(cfg) as conn:
        poll_result = poll_all_channels(conn)
        if cfg.youtube_api_key:
            enrich_result = enrich_pending_videos(conn, cfg.youtube_api_key)
        else:
            logger.warning("YOUTUBE_API_KEY 없음 — 메타 보강 건너뜀")
            enrich_result = {"enriched": 0, "quota_units": 0}

        embedder = get_embedder()
        l1_result = index_l1(conn, embedder, limit=INDEX_BATCH_LIMIT)
        l2_result = index_l2(conn, embedder, limit=INDEX_BATCH_LIMIT)

    logger.info(
        "poll cycle done: poll=%s enrich=%s l1=%s l2=%s", poll_result, enrich_result, l1_result, l2_result
    )
    return {
        "poll": poll_result,
        "enrich": enrich_result,
        "l1_indexed": l1_result["indexed"],
        "l2_indexed": l2_result["indexed"],
    }


def create_scheduler(cfg: Config | None = None) -> BackgroundScheduler:
    """`poll_cycle` 잡이 등록된, 아직 start()되지 않은 스케줄러를 만든다."""
    cfg = cfg or load_config()
    # timezone을 명시하지 않고 next_run_time도 naive local time으로 맞춘다.
    # 둘을 섞으면(예: timezone="UTC" + datetime.now()) "지금"이 로컬 UTC
    # 오프셋만큼 어긋나 첫 실행이 몇 시간씩 밀리는 문제가 생긴다.
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_poll_cycle,
        trigger="interval",
        minutes=cfg.polling.interval_min,
        args=[cfg],
        id="poll_cycle",
        next_run_time=datetime.now(),  # 시작하자마자 1회 실행
        coalesce=True,
        max_instances=1,
    )
    return scheduler


def main() -> None:
    """독립 실행 진입점: `uv run python -m ytfa.worker`."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    scheduler = create_scheduler(cfg)
    scheduler.start()
    logger.info("worker started — polling every %s min", cfg.polling.interval_min)
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    main()
