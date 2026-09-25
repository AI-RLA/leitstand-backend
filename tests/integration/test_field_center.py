"""A field's center comes from PostGIS, which the in-memory fake only approximates."""

from __future__ import annotations

from uuid import UUID

import pytest
from geojson_pydantic import Polygon
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from leitstand_backend.adapters.outbound.persistence.postgres.field_repository_adapter import (
    PostgresFieldRepositoryAdapter,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _polygon(ring: list[tuple[float, float]]) -> Polygon:
    return Polygon(type="Polygon", coordinates=[[*ring, ring[0]]])


_SQUARE = _polygon([(8.0, 52.0), (8.002, 52.0), (8.002, 52.002), (8.0, 52.002)])

# An L whose vertex mean and centroid both fall outside it, in the missing corner.
_L_SHAPE = _polygon(
    [
        (8.0, 52.0),
        (8.004, 52.0),
        (8.004, 52.001),
        (8.001, 52.001),
        (8.001, 52.004),
        (8.0, 52.004),
    ]
)


async def _inside(session: AsyncSession, field_id: UUID, lat: float, lon: float) -> bool:
    query = text(
        "SELECT ST_Contains(geometry, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)) "
        "FROM fields WHERE id = :id"
    )
    return (await session.execute(query, {"id": field_id, "lat": lat, "lon": lon})).scalar_one()


async def test_a_square_field_is_centered_in_its_middle(db_session: AsyncSession) -> None:
    field = await PostgresFieldRepositoryAdapter(db_session).create("square", _SQUARE, None)

    assert field.center_lat == pytest.approx(52.001)
    assert field.center_lon == pytest.approx(8.001)


async def test_an_l_shaped_field_is_centered_inside_it(db_session: AsyncSession) -> None:
    field = await PostgresFieldRepositoryAdapter(db_session).create("L", _L_SHAPE, None)

    assert field.center_lat is not None and field.center_lon is not None
    assert await _inside(db_session, field.id, field.center_lat, field.center_lon)


async def test_every_read_carries_the_same_center(db_session: AsyncSession) -> None:
    repo = PostgresFieldRepositoryAdapter(db_session)
    created = await repo.create("square", _SQUARE, None)

    listed = next(f for f in await repo.list() if f.id == created.id)
    fetched = await repo.get(created.id)
    moved = await repo.update(created.id, geometry=_L_SHAPE)

    assert (listed.center_lat, listed.center_lon) == (created.center_lat, created.center_lon)
    assert fetched is not None
    assert (fetched.center_lat, fetched.center_lon) == (created.center_lat, created.center_lon)
    assert moved is not None and moved.center_lat is not None and moved.center_lon is not None
    assert await _inside(db_session, moved.id, moved.center_lat, moved.center_lon)


async def test_a_polygon_with_no_ring_has_no_center(db_session: AsyncSession) -> None:
    empty = Polygon(type="Polygon", coordinates=[])
    field = await PostgresFieldRepositoryAdapter(db_session).create("empty", empty, None)

    assert (field.center_lat, field.center_lon) == (None, None)
