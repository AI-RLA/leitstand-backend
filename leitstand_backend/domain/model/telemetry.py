"""Telemetry payload models: Pose, Battery, RobotState."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Pose(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)

    ts: datetime
    frame: Literal["wgs84"] = "wgs84"
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    heading_deg: float | None = Field(default=None, ge=0, lt=360)
    horizontal_accuracy_m: float | None = Field(default=None, ge=0)


class Battery(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)

    ts: datetime
    battery_pct: int = Field(ge=0, le=100)
    charging: bool


RobotStatus = Literal["active", "idle", "charging", "alert"]


class RobotState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ts: datetime
    status: RobotStatus
    task: str = ""
