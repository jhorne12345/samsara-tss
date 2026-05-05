"""Command-line interface for the Test Scheduling Service.

Operator-facing commands (run from outside the dispatcher process):

* ``tss serve`` — start the dispatcher.
* ``tss agent`` — start a single mock agent.
* ``tss chaos`` — start N mock agents with a chosen chaos intensity.
* ``tss submit-job`` — submit a job to a running dispatcher.
* ``tss agents`` — list registered agents (rich table).
* ``tss jobs`` — list jobs (rich table) with optional status filter.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Annotated
from uuid import UUID

import httpx
import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from tss.agent.chaos import PROFILES, profile_for_intensity
from tss.agent.runner import run_one_agent
from tss.common.constants import DEFAULT_HOST, DEFAULT_PORT

app = typer.Typer(
    name="tss",
    no_args_is_help=True,
    add_completion=False,
    help="Samsara Test Scheduling Service — manage HIL testbeds.",
)

console = Console()


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Bind address.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option(help="Port to listen on.")] = DEFAULT_PORT,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Start the TSS dispatcher (FastAPI + watchdog)."""
    _setup_logging(verbose)
    console.print(f"[bold]Starting TSS dispatcher[/] on [cyan]http://{host}:{port}[/]")
    uvicorn.run(
        "tss.server.app:app",
        host=host,
        port=port,
        log_level="info" if not verbose else "debug",
        access_log=verbose,
    )


@app.command()
def agent(
    name: Annotated[str, typer.Option(help="Agent name (must be unique).")],
    caps: Annotated[
        str,
        typer.Option(help="Comma-separated capabilities, e.g. vehicle_gateway,asset_gateway"),
    ],
    dispatcher: Annotated[
        str, typer.Option(help="Dispatcher base URL.")
    ] = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}",
    profile: Annotated[
        str, typer.Option(help="Chaos profile: stable | flaky | crashy | doomed")
    ] = "stable",
    seed: Annotated[int | None, typer.Option(help="RNG seed (for reproducibility).")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Start one mock agent (testbed simulator)."""
    _setup_logging(verbose)
    capabilities = [c.strip() for c in caps.split(",") if c.strip()]
    if not capabilities:
        raise typer.BadParameter("At least one capability is required.")
    if profile not in PROFILES:
        raise typer.BadParameter(f"Unknown profile {profile!r}. Choose from: {', '.join(PROFILES)}")
    chaos = PROFILES[profile]
    console.print(f"[bold]Starting agent[/] [cyan]{name}[/] caps={capabilities} profile={profile}")
    asyncio.run(
        run_one_agent(
            name=name,
            capabilities=capabilities,
            dispatcher_url=dispatcher,
            chaos=chaos,
            seed=seed,
        )
    )


@app.command()
def chaos(
    count: Annotated[int, typer.Option(help="Number of agents to spawn.")] = 10,
    intensity: Annotated[
        str,
        typer.Option(help="Chaos intensity: stable | flaky | crashy | doomed | mixed"),
    ] = "mixed",
    products: Annotated[
        str, typer.Option(help="Comma-separated products to spread across agents.")
    ] = "vehicle_gateway,asset_gateway",
    dispatcher: Annotated[
        str, typer.Option(help="Dispatcher base URL.")
    ] = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}",
    seed: Annotated[int | None, typer.Option(help="Master RNG seed.")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Spawn N mock agents with randomized failure profiles. Ctrl-C to stop all."""
    _setup_logging(verbose)
    valid = {"stable", "flaky", "crashy", "doomed", "mixed"}
    if intensity not in valid:
        raise typer.BadParameter(f"Unknown intensity {intensity!r}. Choose from: {sorted(valid)}")
    product_list = [p.strip() for p in products.split(",") if p.strip()]
    if not product_list:
        raise typer.BadParameter("At least one product is required.")
    rng = random.Random(seed)
    console.print(f"[bold]Spawning {count} agents[/] intensity={intensity} products={product_list}")

    async def run_all() -> None:
        async with asyncio.TaskGroup() as tg:
            for i in range(count):
                # Each agent gets at least one product, sometimes both for variety.
                k = 1 if rng.random() < 0.7 else min(2, len(product_list))
                caps = rng.sample(product_list, k=k)
                profile = profile_for_intensity(intensity, rng)  # type: ignore[arg-type]
                agent_seed = rng.randint(0, 2**31 - 1)
                tg.create_task(
                    run_one_agent(
                        name=f"chaos-{i + 1:02d}",
                        capabilities=caps,
                        dispatcher_url=dispatcher,
                        chaos=profile,
                        seed=agent_seed,
                    )
                )

    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        console.print("[yellow]chaos stopped[/]")


@app.command(name="submit-job")
def submit_job(
    product: Annotated[str, typer.Option(help="Required capability, e.g. vehicle_gateway.")],
    duration: Annotated[float, typer.Option(help="Declared duration in seconds.")] = 5.0,
    crash_at: Annotated[
        float | None,
        typer.Option(help="Force agent to crash at this fraction of duration."),
    ] = None,
    slow: Annotated[float, typer.Option(help="Force this slow-multiplier (1.0 = on time).")] = 1.0,
    max_attempts: Annotated[int, typer.Option(help="Retry budget.")] = 3,
    dispatcher: Annotated[
        str, typer.Option(help="Dispatcher base URL.")
    ] = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}",
) -> None:
    """Submit a single job to the dispatcher."""
    payload = {
        "product": product,
        "duration_seconds": duration,
        "slow_multiplier": slow,
        "max_attempts": max_attempts,
    }
    if crash_at is not None:
        payload["crash_at_pct"] = crash_at
    r = httpx.post(f"{dispatcher}/api/jobs", json=payload, timeout=5.0)
    r.raise_for_status()
    job_id = r.json()["job_id"]
    console.print(
        f"[green]submitted[/] job [cyan]{job_id}[/] product={product} duration={duration}s"
    )


@app.command(name="agents")
def list_agents(
    dispatcher: Annotated[
        str, typer.Option(help="Dispatcher base URL.")
    ] = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}",
) -> None:
    """List registered agents."""
    r = httpx.get(f"{dispatcher}/api/agents", timeout=5.0)
    r.raise_for_status()
    agents = r.json()
    table = Table(title="Agents", show_lines=False)
    table.add_column("name", style="cyan")
    table.add_column("status")
    table.add_column("capabilities", style="magenta")
    table.add_column("epoch")
    table.add_column("current job")
    table.add_column("last heartbeat")
    for a in agents:
        status = a["status"]
        color = {"idle": "green", "busy": "yellow", "offline": "red"}.get(status, "white")
        table.add_row(
            a["name"],
            f"[{color}]{status}[/]",
            ",".join(a["capabilities"]),
            str(a["epoch"]),
            (a["current_job_id"][:8] if a["current_job_id"] else "-"),
            a["last_heartbeat_at"],
        )
    console.print(table)


@app.command(name="jobs")
def list_jobs(
    status_filter: Annotated[
        str | None,
        typer.Option("--status", help="Filter by status: queued | running | completed | failed"),
    ] = None,
    dispatcher: Annotated[
        str, typer.Option(help="Dispatcher base URL.")
    ] = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}",
) -> None:
    """List jobs."""
    params: dict[str, str] = {}
    if status_filter:
        params["status_filter"] = status_filter
    r = httpx.get(f"{dispatcher}/api/jobs", params=params, timeout=5.0)
    r.raise_for_status()
    jobs = r.json()
    table = Table(title=f"Jobs ({len(jobs)})", show_lines=False)
    table.add_column("id", style="dim")
    table.add_column("product", style="cyan")
    table.add_column("status")
    table.add_column("attempts")
    table.add_column("agent")
    table.add_column("created")
    for j in jobs:
        status = j["status"]
        color = {
            "queued": "yellow",
            "running": "blue",
            "completed": "green",
            "failed": "red",
        }.get(status, "white")
        agent_id = j.get("assigned_agent_id")
        table.add_row(
            j["id"][:8],
            j["product"],
            f"[{color}]{status}[/]",
            f"{j['attempt_count']}/{j['max_attempts']}",
            agent_id[:8] if agent_id else "-",
            j["created_at"],
        )
    console.print(table)


def _try_uuid(s: str) -> UUID | None:
    try:
        return UUID(s)
    except ValueError:
        return None


if __name__ == "__main__":
    app()
