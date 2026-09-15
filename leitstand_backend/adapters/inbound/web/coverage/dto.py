"""Wire DTOs for /api/v1/coverage."""

from __future__ import annotations

from uuid import UUID

from pydantic import ConfigDict
from pydantic import Field as PField

from leitstand_backend.ports.inbound.mission_management import CoveragePlanningFields


class CoveragePreviewQuery(CoveragePlanningFields):
    """The inputs a coverage stage is planned from, as query parameters.

    The same names as a coverage stage input on create_mission, so a preview the operator
    approved is stored by sending the values again.
    """

    model_config = ConfigDict(allow_inf_nan=False)

    field_id: UUID = PField(description="Field to cover, from list_fields.")
    operation_width_m: float = PField(
        gt=0, description="Working width of the mounted implement, in metres."
    )
