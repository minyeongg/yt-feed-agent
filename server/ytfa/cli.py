"""xba — CLI 진입점 (docs/05-구현가이드.md 각 Phase의 "확인" 커맨드).

Phase가 끝날 때 검증에 쓰는 최소 커맨드만 그때그때 추가한다. 처음부터
다 만들지 않는다 — MCP 툴을 3개로 시작하는 것(step 20)과 같은 원칙.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from ytfa.core.ranking import DEFAULT_BRIEFING_LIMIT, DEFAULT_CANDIDATE_LIMIT, DEFAULT_SINCE_HOURS, build_briefing
from ytfa.db import get_connection
from ytfa.llm.cost import LLMClient, cost_summary

app = typer.Typer(add_completion=False)
console = Console()


@app.callback()
def _root() -> None:
    """xba — yt-feed-agent CLI."""
    # typer는 커맨드가 하나뿐이면 서브커맨드 이름 없이 바로 실행하려 든다.
    # 이 콜백이 있어야 `xba cost`처럼 서브커맨드 형태가 유지된다(커맨드가
    # 늘어날 걸 이미 알고 있으므로).


@app.command()
def cost(since_days: int = 30) -> None:
    """최근 N일 LLM 비용 요약 (Phase 2 step 8 확인용)."""
    with get_connection() as conn:
        summary = cost_summary(conn, since_days=since_days)

    console.print(f"최근 {since_days}일: [bold]${summary['total_usd']:.6f}[/bold] ({summary['run_count']}회 호출)")
    if summary["by_kind"]:
        table = Table(show_header=True)
        table.add_column("kind")
        table.add_column("cost_usd", justify="right")
        for kind, usd in summary["by_kind"].items():
            table.add_row(kind, f"${usd:.6f}")
        console.print(table)


@app.command()
def brief(
    since_hours: int = DEFAULT_SINCE_HOURS,
    candidates: int = DEFAULT_CANDIDATE_LIMIT,
    picks: int = DEFAULT_BRIEFING_LIMIT,
    include_shorts: bool = False,
) -> None:
    """오늘 볼 만한 영상 추천 (Phase 2 step 12 확인용 — "CLI로 완결되는 도구").

    LLM 없이(core/ranking.py) 후보를 추리고, 그 후보만 요약해서(llm/summarize.py)
    이유와 함께 추천한다.
    """
    client = LLMClient()
    with get_connection() as conn:
        result = build_briefing(
            client, conn, candidate_limit=candidates, briefing_limit=picks,
            since_hours=since_hours, include_shorts=include_shorts,
        )

    console.print(
        f"[bold]최근 {since_hours}시간 새 영상 중 {result['candidates_considered']}건 검토, "
        f"{len(result['picks'])}건 추천[/bold]\n"
    )
    if not result["picks"]:
        console.print("[dim]추천할 새 영상이 없습니다.[/dim]")
        return

    for i, pick in enumerate(result["picks"], 1):
        console.print(f"[bold cyan]{i}. {pick['title']}[/bold cyan]")
        if pick["summary"]:
            console.print(f"   {pick['summary']}")
        console.print(f"   [dim]이유: {', '.join(pick['reasons'])} (score={pick['score']})[/dim]\n")

    console.print(f"[dim]이번 요약 비용: ${result['summarize']['cost_usd']:.6f}[/dim]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
