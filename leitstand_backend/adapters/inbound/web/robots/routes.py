"""Robot list + detail endpoints under /api/v1/robots."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from leitstand_backend.adapters.inbound.web.robots.dto import RobotView
from leitstand_backend.adapters.inbound.web.robots.mappers import to_robot_view
from leitstand_backend.infrastructure.deps import get_fleet_view_use_case
from leitstand_backend.ports.inbound.fleet_view import FleetViewUseCase

router = APIRouter(prefix="/api/v1/robots", tags=["robots"])


@router.get("", response_model=list[RobotView])
async def list_robots(
    uc: FleetViewUseCase = Depends(get_fleet_view_use_case),
) -> list[RobotView]:
    return [to_robot_view(s) for s in await uc.list_robots()]


@router.get("/{robot_id}", response_model=RobotView)
async def get_robot(
    robot_id: str,
    uc: FleetViewUseCase = Depends(get_fleet_view_use_case),
) -> RobotView:
    overview = await uc.get_robot(robot_id)
    if overview is None:
        raise HTTPException(404, f"robot {robot_id!r} not found")
    return to_robot_view(overview)
