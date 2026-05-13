"""Wire DTOs for /api/v1/robots."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from leitstand_backend.domain.model.robot import Metadata
from leitstand_backend.domain.model.telemetry import Battery, Pose, RobotState


class RobotView(BaseModel):
    id: str
    metadata: Metadata
    online: bool
    last_seen: datetime
    pose: Pose | None = None
    battery: Battery | None = None
    state: RobotState | None = None
