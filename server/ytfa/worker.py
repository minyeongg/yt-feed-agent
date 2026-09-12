"""백그라운드 워커 — APScheduler로 RSS 폴링(5) + 메타 보강(6)을 주기 실행한다.

docs/05-구현가이드.md Phase 1, step 7 / docs/02 ADR-1(백그라운드 폴링은
서버 내장 APScheduler).

`create_scheduler()`는 잡만 등록한 스케줄러를 반환한다 — main.py의 FastAPI
startup/shutdown 이벤트에서 start()/shutdown()을 호출해 서버 프로세스에
내장시키는 게 최종 형태다(main.py는 아직 없음). 그 전까지는 이 파일을
직접 실행해 독립 프로세스로 띄울 수 있다:

    uv run python -m ytfa.worker
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from ytfa.config import Config, load_config
from ytfa.db import get_connection
from ytfa.sources.rss import poll_all_channels
from ytfa.sources.youtube_api import enrich_pending_videos

logger = logging.getLogger(__name__)


def run_poll_cycle(cfg: Config | None = None) -> dict:
    """RSS 폴링 → 메타 보강을 한 번 실행한다. 잡 함수이자 수동 호출용 함수."""
    cfg = cfg or load_config()
    with get_connection(cfg) as conn:
        poll_result = poll_all_channels(conn)
        if cfg.youtube_api_key:
            enrich_result = enrich_pending_videos(conn, cfg.youtube_api_key)
        else:
            logger.warning("YOUTUBE_API_KEY 없음 — 메타 보강 건너뜀")
            enrich_result = {"enriched": 0, "quota_units": 0}

    logger.info("poll cycle done: poll=%s enrich=%s", poll_result, enrich_result)
    return {"poll": poll_result, "enrich": enrich_result}


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
