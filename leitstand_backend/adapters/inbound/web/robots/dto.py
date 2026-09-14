"""Wire DTOs for /api/v1/robots."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from leitstand_backend.adapters.inbound.web.runs.dto import RunSummaryView
from leitstand_backend.domain.model.robot.robot import Metadata
from leitstand_backend.domain.model.robot.robot_factsheet import RobotFactsheet
from leitstand_backend.domain.model.robot.robot_status import RobotStatus
from leitstand_backend.domain.model.robot.telemetry import Battery, Pose


class RobotView(BaseModel):
    id: str
    metadata: Metadata
    online: bool
    last_seen: datetime
    pose: Pose | None = None
    battery: Battery | None = None
    status: RobotStatus
    factsheet: RobotFactsheet | None = None
    # What the robot is doing right now; null when idle.
    current_run: RunSummaryView | None = None
