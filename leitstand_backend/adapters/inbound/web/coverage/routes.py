"""Coverage planning without a mission: preview what a stage would look like."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from leitstand_backend.adapters.inbound.web.coverage.dto import CoveragePreviewQuery
from leitstand_backend.application.coverage_stage_planner import (
    PLANNING_INPUT_ERRORS,
    CoverageInputs,
    CoverageStagePlanner,
)
from leitstand_backend.domain.errors import CoveragePlannerUnavailable, FieldNotFoundError
from leitstand_backend.domain.model.mission.mission import CoverageStage
from leitstand_backend.infrastructure.deps import get_coverage_stage_planner

router = APIRouter(prefix="/api/v1/coverage", tags=["coverage"])


@router.get("/preview", response_model=CoverageStage, operation_id="preview_coverage")
async def preview_coverage(
    q: Annotated[CoveragePreviewQuery, Query()],
    plan: CoverageStagePlanner = Depends(get_coverage_stage_planner),
) -> CoverageStage:
    """Plan a coverage stage for a field and return it without saving anything.

    Use it to show the operator what a plan would look like: its swaths, the ground worked and how
    far the turns leave the field. create_mission with the same inputs stores it.
    """
    try:
        return await plan(CoverageInputs.from_fields(q))
    except FieldNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "field not found")
    except PLANNING_INPUT_ERRORS as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except CoveragePlannerUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
