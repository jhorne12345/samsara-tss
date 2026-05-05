"""HTTP routes for job submission, claim, result, and queries."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status

from tss.common.models import (
    Job,
    JobAssignment,
    JobResultRequest,
    JobStatus,
    JobSubmitRequest,
    JobSubmitResponse,
)
from tss.server.dispatcher import Dispatcher
from tss.server.errors import (
    AgentNotIdleError,
    JobNotAssignedToAgentError,
    StaleEpochError,
    UnknownAgentError,
    UnknownJobError,
)

router = APIRouter(prefix="/api", tags=["jobs"])


def _disp(request: Request) -> Dispatcher:
    return request.app.state.dispatcher  # type: ignore[no-any-return]


@router.post("/jobs", response_model=JobSubmitResponse, status_code=status.HTTP_201_CREATED)
async def submit_job(req: JobSubmitRequest, request: Request) -> JobSubmitResponse:
    d = _disp(request)
    job = await d.submit_job(
        product=req.product,
        duration_seconds=req.duration_seconds,
        expected_exit_code=req.expected_exit_code,
        crash_at_pct=req.crash_at_pct,
        slow_multiplier=req.slow_multiplier,
        max_attempts=req.max_attempts,
    )
    return JobSubmitResponse(job_id=job.id)


@router.get("/jobs", response_model=list[Job])
async def list_jobs(
    request: Request,
    status_filter: JobStatus | None = None,
    product: str | None = None,
) -> list[Job]:
    d = _disp(request)
    # snapshot_fleet only exposes queue + running; for filtered listing we
    # walk the store directly so callers can see completed / failed jobs too.
    all_jobs = list(d.store)
    if status_filter is not None:
        all_jobs = [j for j in all_jobs if j.status == status_filter]
    if product is not None:
        all_jobs = [j for j in all_jobs if j.product == product]
    return all_jobs


@router.get("/agents/{agent_id}/jobs/next", status_code=status.HTTP_200_OK)
async def claim_next_job(agent_id: UUID, request: Request) -> Response:
    d = _disp(request)
    try:
        assignment = await d.claim_next_job(agent_id)
    except UnknownAgentError as e:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(e)) from None
    except AgentNotIdleError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from None
    if assignment is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return Response(
        content=assignment.model_dump_json(),
        media_type="application/json",
        status_code=status.HTTP_200_OK,
    )


@router.post(
    "/agents/{agent_id}/jobs/{job_id}/result",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def report_result(
    agent_id: UUID,
    job_id: UUID,
    req: JobResultRequest,
    request: Request,
) -> Response:
    d = _disp(request)
    try:
        await d.report_result(
            agent_id=agent_id,
            job_id=job_id,
            epoch=req.epoch,
            exit_code=req.exit_code,
            duration_actual=req.duration_actual,
            error_message=req.error_message,
        )
    except UnknownAgentError as e:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(e)) from None
    except UnknownJobError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from None
    except StaleEpochError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from None
    except JobNotAssignedToAgentError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# JobAssignment is exported here so the OpenAPI schema includes it explicitly.
__all__ = ["JobAssignment", "router"]
