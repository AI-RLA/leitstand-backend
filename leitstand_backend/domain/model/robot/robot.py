"""Domain entities for the robot registry."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


class Metadata(BaseModel):
    id: str
    model_config = ConfigDict(extra="allow")


class Robot(BaseModel):
    id: str
    metadata: Metadata
    online: bool
    last_seen: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
