"""HTTP routes for agent registration, heartbeat, and operator-side queries."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status

from tss.common.models import (
    Agent,
    HeartbeatRequest,
    RegisterRequest,
    RegisterResponse,
)
from tss.server.dispatcher import Dispatcher
from tss.server.errors import StaleEpochError, UnknownAgentError

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _disp(request: Request) -> Dispatcher:
    return request.app.state.dispatcher  # type: ignore[no-any-return]


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_200_OK)
async def register_agent(req: RegisterRequest, request: Request) -> RegisterResponse:
    d = _disp(request)
    agent = await d.register(req.name, req.capabilities)
    return RegisterResponse(
        agent_id=agent.id,
        epoch=agent.epoch,
        heartbeat_interval_s=d.heartbeat_interval_s,
        poll_interval_s=d.poll_interval_s,
    )


@router.post("/{agent_id}/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
async def heartbeat(agent_id: UUID, req: HeartbeatRequest, request: Request) -> Response:
    d = _disp(request)
    try:
        await d.heartbeat(agent_id, req.epoch)
    except UnknownAgentError as e:
        # 410 Gone signals the agent should re-register: server has no record.
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(e)) from None
    except StaleEpochError as e:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(e)) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{agent_id}/kill", status_code=status.HTTP_204_NO_CONTENT)
async def kill_agent(agent_id: UUID, request: Request) -> Response:
    """Demo-only endpoint: simulate immediate disconnect for the dashboard."""
    d = _disp(request)
    try:
        await d.force_kill_agent(agent_id)
    except UnknownAgentError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("", response_model=list[Agent])
async def list_agents(request: Request) -> list[Agent]:
    d = _disp(request)
    snap = await d.snapshot_fleet()
    return snap.agents
