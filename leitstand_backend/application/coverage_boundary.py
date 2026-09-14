"""Refuse a coverage plan whose field changed since it was planned; needs the field repository."""

from __future__ import annotations

from leitstand_backend.domain.errors import StaleCoverageBoundary
from leitstand_backend.domain.model.mission.coverage import boundary_digest
from leitstand_backend.domain.model.mission.mission import Stage
from leitstand_backend.domain.model.mission.stage_rules import coverage_stages
from leitstand_backend.ports.outbound.field_repository import FieldRepository


async def require_current_boundary(fields: FieldRepository, stages: list[Stage]) -> None:
    """Reject a planned path whose field boundary changed since it was planned.

    A mission with no coverage stage names no field and is not checked.
    """
    for stage in coverage_stages(stages):
        provenance = stage.provenance
        field = await fields.get(provenance.field_id)
        if field is None:
            raise StaleCoverageBoundary(provenance.field_id, "has been deleted")
        if boundary_digest(field.geometry) != provenance.boundary_digest:
            raise StaleCoverageBoundary(provenance.field_id, "has been edited")
