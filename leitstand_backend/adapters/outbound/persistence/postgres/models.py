"""SQLAlchemy declarative models for the Postgres adapter.

Models are kept here (not in domain/) because they are infra-
specific. Domain Pydantic models in domain/ are the
application's view; these are the storage view.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Double,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    desc,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from leitstand_backend.infrastructure.db import Base


class RobotRow(Base):
    __tablename__ = "robots"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    online: Mapped[bool] = mapped_column(Boolean, nullable=False)
    last_pose: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_battery: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class FieldRow(Base):
    __tablename__ = "fields"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    geometry: Mapped[Any] = mapped_column(Geometry("POLYGON", srid=4326), nullable=False)
    area_ha: Mapped[float | None] = mapped_column(
        Numeric(8, 3),
        Computed("ST_Area(geometry::geography) / 10000.0", persisted=True),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class AuditLogRow(Base):
    __tablename__ = "audit_log"

    __table_args__ = (
        Index("audit_log_ts_idx", desc("ts")),
        Index("audit_log_user_idx", "user_id"),
        Index("audit_log_target_idx", "target_id"),
        Index("audit_log_target_type_idx", "target_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Provenance: what performed the action, on what authority, and the link back to the agent
    # turn. Denormalised here rather than joined to the chat tables, which face a shorter retention.
    actor: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'human'"))
    authority: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'direct'"))
    decided_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_call_id: Mapped[UUID | None] = mapped_column(nullable=True)


class MissionRow(Base):
    __tablename__ = "missions"
    __table_args__ = (
        Index("ix_missions_robot_id_status", "robot_id", "status"),
        Index("ix_missions_status_dispatched", "status", "dispatched_at"),
        Index("ix_missions_stages_gin", "stages", postgresql_using="gin"),
    )

    mission_id: Mapped[UUID] = mapped_column(primary_key=True)
    update_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, default=0, server_default=text("0")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'DRAFT'"))
    stages: Mapped[list] = mapped_column(JSONB, nullable=False)
    robot_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    failure_errors: Mapped[list | None] = mapped_column(JSONB, nullable=True)


class MissionStageStateRow(Base):
    """Current per-stage runtime state, one row per (mission, stage).

    The clean read model behind ``GET /missions/{id}/state``: upserted as robot frames
    arrive and resolved at the terminal transition. ``header_id`` (the robot's per-frame
    counter) orders concurrent writes; ``status`` is indexed for cross-mission queries
    (e.g. stages cancelled).
    """

    __tablename__ = "mission_stage_state"
    __table_args__ = (
        Index("ix_mission_stage_state_status", "status"),
        Index("ix_mission_stage_state_mission_index", "mission_id", "stage_index"),
    )

    mission_id: Mapped[UUID] = mapped_column(primary_key=True)
    stage_id: Mapped[UUID] = mapped_column(primary_key=True)
    header_id: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    stage_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    progress: Mapped[float] = mapped_column(Double, nullable=False, server_default=text("0"))
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    source_ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class SiteRow(Base):
    __tablename__ = "sites"

    site_id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    anchor_lat: Mapped[float] = mapped_column(Double, nullable=False)
    anchor_lon: Mapped[float] = mapped_column(Double, nullable=False)
    anchor_heading_deg: Mapped[float] = mapped_column(Double, nullable=False)
    nav2_map_ref: Mapped[str] = mapped_column(Text, nullable=False)
    outline: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class MissionSiteRefRow(Base):
    """Normalized index of which sites a (non-terminal) mission references.

    Rebuilt from a mission's stages on every save, pruned when the mission goes
    terminal, and repopulated on reset. Backs the site-in-use delete guard and
    the site usage view; the FK enforces that a referenced site exists.
    """

    __tablename__ = "mission_site_refs"
    __table_args__ = (Index("ix_mission_site_refs_site_id", "site_id"),)

    mission_id: Mapped[UUID] = mapped_column(primary_key=True)
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT"), primary_key=True
    )


class ToolCallRow(Base):
    """An agent's proposed fleet-changing tool call, tracked from the gate to its outcome."""

    __tablename__ = "tool_calls"
    __table_args__ = (Index("ix_tool_calls_owner_call", "user_id", "provider_tool_call_id"),)

    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    provider_tool_call_id: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    arguments: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
