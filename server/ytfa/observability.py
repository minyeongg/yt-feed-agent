"""JSONL 트레이서 (docs/05-구현가이드.md Phase 7 step 29; docs/03 §5.3).

`data/traces/{run_id}.jsonl` — 한 줄에 이벤트 하나(`llm_call`/`tool_call`/
`compaction`/`run_end`). `jq`로 바로 읽힌다는 게 이 형식을 고른 이유다.

**트레이서 실패가 본 작업을 중단시키면 안 된다** — 그래서 기록 함수는
전부 예외를 삼킨다(로그만 남기고 조용히 넘어간다). 대화 한 번 하는 데
디스크 쓰기 실패로 에이전트가 죽으면 본말전도다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ytfa.config import load_config

logger = logging.getLogger(__name__)


def trace_path(run_id: str) -> Path:
    cfg = load_config()
    trace_dir = cfg.data_dir / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    return trace_dir / f"{run_id}.jsonl"


class Tracer:
    """run(대화/작업 한 번) 하나의 트레이스를 누적 기록한다. `run_id`별로 하나씩 만든다."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.seq = 0

    def _write(self, event: dict[str, Any]) -> None:
        try:
            self.seq += 1
            record = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "run_id": self.run_id,
                "seq": self.seq,
                **event,
            }
            with open(trace_path(self.run_id), "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            logger.exception("트레이스 기록 실패(무시하고 계속) — run_id=%s", self.run_id)

    def llm_call(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        latency_ms: float,
        stop_reason: str | None,
        cost_usd: float,
    ) -> None:
        self._write(
            {
                "type": "llm_call",
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read_tokens,
                "latency_ms": round(latency_ms),
                "stop_reason": stop_reason,
                "cost_usd": cost_usd,
            }
        )

    def tool_call(
        self,
        *,
        tool: str,
        args_summary: dict,
        decision: str,
        latency_ms: float,
        result_size: int | None,
        error: str | None,
    ) -> None:
        self._write(
            {
                "type": "tool_call",
                "tool": tool,
                "args_summary": args_summary,
                "decision": decision,
                "latency_ms": round(latency_ms),
                "result_size": result_size,
                "error": error,
            }
        )

    def compaction(self, *, before_chars: int, after_chars: int, compacted_count: int) -> None:
        self._write(
            {
                "type": "compaction",
                "before_chars": before_chars,
                "after_chars": after_chars,
                "compacted_count": compacted_count,
            }
        )

    def run_end(self, *, steps: int, stopped_reason: str, total_cost_usd: float, total_latency_ms: float) -> None:
        self._write(
            {
                "type": "run_end",
                "steps": steps,
                "stopped_reason": stopped_reason,
                "total_cost_usd": round(total_cost_usd, 6),
                "total_latency_ms": round(total_latency_ms),
            }
        )
