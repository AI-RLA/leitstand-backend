"""Postgres-backed FieldRepository."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

from geojson_pydantic import Polygon
from sqlalchemy import Row, delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from leitstand_backend.adapters.outbound.persistence.postgres.models import FieldRow
from leitstand_backend.domain.model.field import Field
from leitstand_backend.ports.outbound.field_repository import FieldRepository


def _geometry_expr(geometry: Polygon):
    return func.ST_SetSRID(func.ST_GeomFromGeoJSON(geometry.model_dump_json()), 4326)


def _computed_columns():
    """Select what PostGIS derives from the stored geometry, next to the row."""
    # A point on the surface, not the centroid, which lies outside an L-shaped field.
    center = func.ST_PointOnSurface(FieldRow.geometry)
    return (
        func.ST_AsGeoJSON(FieldRow.geometry).label("geojson"),
        func.ST_Y(center).label("center_lat"),
        func.ST_X(center).label("center_lon"),
    )


class PostgresFieldRepositoryAdapter(FieldRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self) -> list[Field]:
        stmt = select(FieldRow, *_computed_columns()).order_by(FieldRow.created_at.desc())
        result = await self._session.execute(stmt)
        return [_to_domain(hit) for hit in result]

    async def get(self, field_id: UUID) -> Field | None:
        stmt = select(FieldRow, *_computed_columns()).where(FieldRow.id == field_id)
        hit = (await self._session.execute(stmt)).one_or_none()
        return None if hit is None else _to_domain(hit)

    async def create(
        self,
        name: str,
        geometry: Polygon,
        notes: str | None,
    ) -> Field:
        now = datetime.now(timezone.utc)
        stmt = (
            insert(FieldRow)
            .values(
                name=name,
                geometry=_geometry_expr(geometry),
                notes=notes,
                created_at=now,
                updated_at=now,
            )
            .returning(FieldRow, *_computed_columns())
        )
        return _to_domain((await self._session.execute(stmt)).one())

    async def update(
        self,
        field_id: UUID,
        name: str | None = None,
        geometry: Polygon | None = None,
        notes: str | None = None,
    ) -> Field | None:
        values: dict = {"updated_at": datetime.now(timezone.utc)}
        if name is not None:
            values["name"] = name
        if geometry is not None:
            values["geometry"] = _geometry_expr(geometry)
        if notes is not None:
            values["notes"] = notes
        stmt = (
            update(FieldRow)
            .where(FieldRow.id == field_id)
            .values(**values)
            .returning(FieldRow, *_computed_columns())
            .execution_options(populate_existing=True)
        )
        hit = (await self._session.execute(stmt)).one_or_none()
        return None if hit is None else _to_domain(hit)

    async def delete(self, field_id: UUID) -> bool:
        stmt = delete(FieldRow).where(FieldRow.id == field_id)
        result = await self._session.execute(stmt)
        return result.rowcount > 0


def _to_domain(hit: Row) -> Field:
    """Read a result row by column name, so the order of the computed columns does not matter."""
    row: FieldRow = hit.FieldRow
    return Field(
        id=row.id,
        name=row.name,
        geometry=Polygon.model_validate(json.loads(hit.geojson)),
        area_ha=float(row.area_ha) if row.area_ha is not None else None,
        center_lat=hit.center_lat,
        center_lon=hit.center_lon,
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
