"""Postgres-backed FieldRepository."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

from geojson_pydantic import Polygon
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from leitstand_backend.adapters.outbound.persistence.postgres.models import FieldRow
from leitstand_backend.domain.model.field import Field
from leitstand_backend.ports.outbound.field_repository import FieldRepository


def _geometry_expr(geometry: Polygon):
    return func.ST_SetSRID(func.ST_GeomFromGeoJSON(geometry.model_dump_json()), 4326)


def _geojson_expr():
    return func.ST_AsGeoJSON(FieldRow.geometry).label("geojson")


class PostgresFieldRepositoryAdapter(FieldRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self) -> list[Field]:
        stmt = select(FieldRow, _geojson_expr()).order_by(FieldRow.created_at.desc())
        result = await self._session.execute(stmt)
        return [_to_domain(row, geojson) for row, geojson in result]

    async def get(self, field_id: UUID) -> Field | None:
        stmt = select(FieldRow, _geojson_expr()).where(FieldRow.id == field_id)
        hit = (await self._session.execute(stmt)).one_or_none()
        if hit is None:
            return None
        row, geojson = hit
        return _to_domain(row, geojson)

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
            .returning(FieldRow, _geojson_expr())
        )
        row, geojson = (await self._session.execute(stmt)).one()
        return _to_domain(row, geojson)

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
            .returning(FieldRow, _geojson_expr())
            .execution_options(populate_existing=True)
        )
        hit = (await self._session.execute(stmt)).one_or_none()
        if hit is None:
            return None
        row, geojson = hit
        return _to_domain(row, geojson)

    async def delete(self, field_id: UUID) -> bool:
        stmt = delete(FieldRow).where(FieldRow.id == field_id)
        result = await self._session.execute(stmt)
        return result.rowcount > 0


def _to_domain(row: FieldRow, geojson_str: str) -> Field:
    return Field(
        id=row.id,
        name=row.name,
        geometry=Polygon.model_validate(json.loads(geojson_str)),
        area_ha=float(row.area_ha) if row.area_ha is not None else None,
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
