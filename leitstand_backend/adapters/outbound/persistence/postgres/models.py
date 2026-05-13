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
from sqlalchemy import BigInteger, Boolean, Computed, Numeric, Text, text
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
    last_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


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

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
