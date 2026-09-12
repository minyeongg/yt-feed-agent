"""FastAPI 앱 진입점 (docs/05-구현가이드.md Phase 3, step 13; docs/03 §2.1).

루프백(127.0.0.1)에만 바인딩하고(FR-S3), 모든 요청에 `X-YTFA-Token`
인증을 요구한다(security.py). 워커 스케줄러(worker.py)를 이 프로세스의
lifespan에 내장시킨다 — `python -m ytfa.worker`로 별도 프로세스를 띄우는
건 이 파일이 생기기 전까지의 임시 방편이었다(worker.py 상단 docstring
참고). 둘 다 쓸 수 있게 남겨는 두되, 정상 운영은 이제 이 프로세스 하나다.

실행:
    uv run uvicorn ytfa.main:app --host 127.0.0.1 --port 8787

확인:
    curl -H "X-YTFA-Token: $(cat config/token)" http://127.0.0.1:8787/health
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ytfa.api.routes_briefing import router as briefing_router
from ytfa.api.routes_categories import router as categories_router
from ytfa.api.routes_feed import router as feed_router
from ytfa.api.routes_search import router as search_router
from ytfa.config import load_config
from ytfa.db import get_connection
from ytfa.security import verify_token
from ytfa.worker import create_scheduler

logger = logging.getLogger(__name__)

__version__ = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    scheduler = create_scheduler(cfg)
    scheduler.start()
    app.state.scheduler = scheduler
    logger.info("서버 시작 — worker 스케줄러 내장 실행(%s분 주기)", cfg.polling.interval_min)
    try:
        yield
    finally:
        scheduler.shutdown()


app = FastAPI(title="yt-feed-agent", version=__version__, lifespan=lifespan)

# 콘텐츠 스크립트(content/inject.ts)는 팝업/백그라운드와 달리 유튜브 페이지의
# 오리진(https://www.youtube.com)에서 그대로 fetch를 한다 — manifest의
# host_permissions는 확장 특권 컨텍스트(팝업·백그라운드)에만 CORS를
# 면제해주고 콘텐츠 스크립트는 면제 안 된다. 그래서 서버가 직접 CORS
# 헤더를 내려줘야 한다(실제로 겪은 버그: 팝업은 되는데 유튜브 페이지
# 안에서의 fetch만 프리플라이트에서 막혔다). 토큰 인증이 모든 라우트에
# 걸려있으니(security.py) 오리진을 넓게 열어도 실질적인 방어선은 토큰이다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://www.youtube.com"],
    allow_methods=["GET", "POST"],
    allow_headers=["X-YTFA-Token", "Content-Type"],
)

app.include_router(feed_router)
app.include_router(search_router)
app.include_router(categories_router)
app.include_router(briefing_router)


@app.get("/health", dependencies=[Depends(verify_token)])
def health() -> dict:
    cfg = load_config()
    try:
        with get_connection(cfg) as conn:
            last_poll_at = conn.execute("SELECT MAX(last_polled_at) FROM channels").fetchone()[0]
        db_status = "ok"
    except Exception:
        logger.exception("health check: db 조회 실패")
        db_status = "error"
        last_poll_at = None

    scheduler = getattr(app.state, "scheduler", None)
    worker_status = "running" if scheduler is not None and scheduler.running else "stopped"

    return {
        "status": "ok",
        "version": __version__,
        "db": db_status,
        "worker": worker_status,
        "last_poll_at": last_poll_at,
        # rag.level이 0인 동안(Phase 8 전) 항상 이 값 — 있는 척 안 함.
        "embedder": "not_loaded",
    }
