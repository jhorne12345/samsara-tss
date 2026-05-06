"""Hand-rolled Prometheus text-format /metrics endpoint.

Zero new dependencies. Format reference:
https://prometheus.io/docs/instrumenting/exposition_formats/#text-based-format
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from tss.server.dispatcher import Dispatcher

router = APIRouter(tags=["metrics"])


def _disp(request: Request) -> Dispatcher:
    return request.app.state.dispatcher  # type: ignore[no-any-return]


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    d = _disp(request)
    snap = await d.snapshot_fleet()
    s = snap.stats

    lines = [
        "# HELP tss_jobs_queued Number of jobs currently in the queue.",
        "# TYPE tss_jobs_queued gauge",
        f"tss_jobs_queued {s.queue_depth}",
        "# HELP tss_jobs_running Number of jobs currently running on agents.",
        "# TYPE tss_jobs_running gauge",
        f"tss_jobs_running {s.jobs_running}",
        "# HELP tss_jobs_completed_total Total jobs that reached COMPLETED.",
        "# TYPE tss_jobs_completed_total counter",
        f"tss_jobs_completed_total {s.jobs_completed}",
        "# HELP tss_jobs_failed_total Total jobs that reached FAILED.",
        "# TYPE tss_jobs_failed_total counter",
        f"tss_jobs_failed_total {s.jobs_failed}",
        "# HELP tss_agents_total Number of registered agents by status.",
        "# TYPE tss_agents_total gauge",
        f'tss_agents_total{{status="idle"}} {s.idle}',
        f'tss_agents_total{{status="busy"}} {s.busy}',
        f'tss_agents_total{{status="offline"}} {s.offline}',
    ]
    body = "\n".join(lines) + "\n"
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")
