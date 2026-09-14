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
    UniqueConstraint,
    desc,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from leitstand_backend.domain.model.mission.run_lifecycle import ACTIVE_STATES
from leitstand_backend.domain.model.mission.run_status import RunStatus
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
    factsheet_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class FieldRow(Base):
    __tablename__ = "fields"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    geometry: Mapped[Any] = mapped_column(Geometry("POLYGON", srid=4326), nullable=False)
    area_ha: Mapped[float | None] = mapped_column(
        # Six decimals of a hectare resolve a hundredth of a square metre, so a small plot is not
        # quantised into disagreeing with the area a planner measures for it.
        Numeric(12, 6),
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
    """A mission definition. Execution state lives on MissionRunRow; stages on MissionStageRow."""

    __tablename__ = "missions"

    mission_id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_robot_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class MissionStageRow(Base):
    """One stage of a mission definition, flattened: a cleanup stage is a row under its parent.

    Identity, order, kind and site are columns because they are queried and constrained; the
    payload stays a JSONB ``spec`` because it is read whole and never queried.
    """

    __tablename__ = "mission_stages"
    __table_args__ = (
        UniqueConstraint(
            "mission_id",
            "parent_stage_id",
            "sequence",
            name="uq_stage_order",
            deferrable=True,
            initially="DEFERRED",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_mission_stages_mission", "mission_id", "sequence"),
        Index("ix_mission_stages_site", "site_id"),
    )

    stage_id: Mapped[UUID] = mapped_column(primary_key=True)
    mission_id: Mapped[UUID] = mapped_column(
        ForeignKey("missions.mission_id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    site_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT"), nullable=True
    )
    parent_stage_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("mission_stages.stage_id", ondelete="CASCADE"), nullable=True
    )
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False)


# Derived from the domain, so the partial index cannot silently stop covering a state the queries
# already include; enum order keeps the rendered SQL stable.
_ACTIVE_PREDICATE = "status IN ({})".format(
    ", ".join(f"'{s.value}'" for s in RunStatus if s in ACTIVE_STATES)
)


class MissionRunRow(Base):
    """One execution of a mission, with a frozen copy of the stages it was dispatched with."""

    __tablename__ = "mission_runs"
    __table_args__ = (
        Index(
            "uq_run_robot_active",
            "robot_id",
            unique=True,
            postgresql_where=text(_ACTIVE_PREDICATE),
        ),
        Index("ix_run_mission_active", "mission_id", postgresql_where=text(_ACTIVE_PREDICATE)),
        Index("ix_run_mission_created", "mission_id", desc("created_at")),
    )

    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    mission_id: Mapped[UUID] = mapped_column(
        ForeignKey("missions.mission_id", ondelete="RESTRICT"), nullable=False
    )
    robot_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    stages: Mapped[list] = mapped_column(JSONB, nullable=False)
    stages_digest: Mapped[str] = mapped_column(Text, nullable=False)
    site_anchors: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    origin: Mapped[dict] = mapped_column(JSONB, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_errors: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class StageRunRow(Base):
    """Current per-stage runtime state of one run, one row per (run, stage).

    Upserted as robot frames arrive, ordered by the robot's ``header_id``, and resolved at the
    run's terminal transition. The robot's clock (``reported_*``, ``occurred_at``) is only ever
    copied from a frame; ``recorded_at`` is the backend's and orders across runs.
    """

    __tablename__ = "stage_runs"
    __table_args__ = (Index("ix_stage_runs_index", "run_id", "stage_index"),)

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("mission_runs.run_id", ondelete="RESTRICT"), primary_key=True
    )
    stage_id: Mapped[UUID] = mapped_column(primary_key=True)
    header_id: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    stage_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    progress: Mapped[float] = mapped_column(Double, nullable=False, server_default=text("0"))
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reported_started_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    reported_ended_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    status_source: Mapped[str] = mapped_column(Text, nullable=False)
    parent_stage_id: Mapped[UUID | None] = mapped_column(nullable=True)


class MissionRunTransitionRow(Base):
    """One status change of a run, appended with the change itself."""

    __tablename__ = "mission_run_transitions"
    __table_args__ = (Index("ix_run_transitions_run", "run_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("mission_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[str] = mapped_column(Text, nullable=False)
    to_status: Mapped[str] = mapped_column(Text, nullable=False)
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    report_header_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    acknowledged: Mapped[bool | None] = mapped_column(nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


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
