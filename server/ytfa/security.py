"""토큰 인증 (docs/05-구현가이드.md Phase 3, step 13; docs/03 §2, §5.2).

로컬 서버는 루프백(127.0.0.1)에만 바인딩하지만(FR-S3), 같은 머신의 다른
프로세스가 접근하는 걸 막기 위해 모든 REST 요청에 `X-YTFA-Token`을
요구한다. 토큰은 최초 실행 시 `config.py`가 생성해 `config/token`에
600 권한으로 저장해둔 값이다.

프롬프트 인젝션 격리 봉투(ADR-11, `<untrusted_content>`)는 Phase 6
step 27에서 이 파일에 추가된다 — 지금은 인증만 있다.
"""

from __future__ import annotations

from fastapi import Header, HTTPException

from ytfa.config import load_config


def verify_token(x_ytfa_token: str = Header(default="")) -> None:
    """모든 REST 요청에 `X-YTFA-Token` 인증을 강제한다."""
    cfg = load_config()
    if not cfg.server_token or x_ytfa_token != cfg.server_token:
        raise HTTPException(status_code=401, detail="invalid or missing X-YTFA-Token")
