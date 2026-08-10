"""Postgres-backed SiteRepository."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from geojson_pydantic import Polygon
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from leitstand_backend.adapters.outbound.persistence.postgres.models import SiteRow
from leitstand_backend.domain.errors import SiteInUse
from leitstand_backend.domain.model.mission.mission_lifecycle import TERMINAL_STATES
from leitstand_backend.domain.model.site import Site
from leitstand_backend.ports.outbound.site_repository import SiteRepository

_TERMINAL_STATUSES = tuple(status.value for status in TERMINAL_STATES)


class PostgresSiteRepositoryAdapter(SiteRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self) -> list[Site]:
        stmt = select(SiteRow).order_by(SiteRow.name.asc())
        result = await self._session.execute(stmt)
        return [_to_domain(row) for row in result.scalars()]

    async def get(self, site_id: UUID) -> Site | None:
        row = await self._session.get(SiteRow, site_id)
        return _to_domain(row) if row else None

    async def create(
        self,
        name: str,
        anchor_lat: float,
        anchor_lon: float,
        anchor_heading_deg: float,
        nav2_map_ref: str,
        outline: Polygon | None,
        description: str | None,
    ) -> Site:
        now = datetime.now(timezone.utc)
        stmt = (
            insert(SiteRow)
            .values(
                site_id=uuid4(),
                name=name,
                anchor_lat=anchor_lat,
                anchor_lon=anchor_lon,
                anchor_heading_deg=anchor_heading_deg,
                nav2_map_ref=nav2_map_ref,
                outline=_outline_to_json(outline),
                description=description,
                created_at=now,
                updated_at=now,
            )
            .returning(SiteRow)
        )
        row = (await self._session.execute(stmt)).scalar_one()
        return _to_domain(row)

    async def update(
        self,
        site_id: UUID,
        name: str | None = None,
        anchor_lat: float | None = None,
        anchor_lon: float | None = None,
        anchor_heading_deg: float | None = None,
        nav2_map_ref: str | None = None,
        outline: Polygon | None = None,
        description: str | None = None,
    ) -> Site | None:
        values: dict = {"updated_at": datetime.now(timezone.utc)}
        if name is not None:
            values["name"] = name
        if anchor_lat is not None:
            values["anchor_lat"] = anchor_lat
        if anchor_lon is not None:
            values["anchor_lon"] = anchor_lon
        if anchor_heading_deg is not None:
            values["anchor_heading_deg"] = anchor_heading_deg
        if nav2_map_ref is not None:
            values["nav2_map_ref"] = nav2_map_ref
        if outline is not None:
            values["outline"] = _outline_to_json(outline)
        if description is not None:
            values["description"] = description
        stmt = (
            update(SiteRow)
            .where(SiteRow.site_id == site_id)
            .values(**values)
            .returning(SiteRow)
            .execution_options(populate_existing=True)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain(row) if row else None

    async def delete(self, site_id: UUID) -> bool:
        blocking = await self._find_blocking_missions(site_id)
        if blocking:
            raise SiteInUse(site_id, blocking)
        stmt = delete(SiteRow).where(SiteRow.site_id == site_id)
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    async def _find_blocking_missions(self, site_id: UUID) -> list[UUID]:
        """Return mission_ids of non-terminal missions referencing this site_id.

        Reads the normalized ``mission_site_refs`` index (maintained on mission
        save / reset, pruned on terminal), which already captures site refs nested
        inside ``on_cancel`` cleanup stages. The terminal filter is belt-and-braces:
        terminal missions are pruned from the index.
        """
        stmt = text("""
            SELECT m.mission_id::text
            FROM mission_site_refs r
            JOIN missions m ON m.mission_id = r.mission_id AND m.update_id = 0
            WHERE r.site_id = :site_id
              AND NOT (m.status = ANY(:terminal_statuses))
        """).bindparams(
            site_id=str(site_id),
            terminal_statuses=list(_TERMINAL_STATUSES),
        )
        result = await self._session.execute(stmt)
        return [UUID(row[0]) for row in result]


def _to_domain(row: SiteRow) -> Site:
    outline = Polygon.model_validate(row.outline) if row.outline is not None else None
    return Site(
        site_id=row.site_id,
        name=row.name,
        anchor_lat=float(row.anchor_lat),
        anchor_lon=float(row.anchor_lon),
        anchor_heading_deg=float(row.anchor_heading_deg),
        nav2_map_ref=row.nav2_map_ref,
        outline=outline,
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _outline_to_json(outline: Polygon | None) -> dict | None:
    if outline is None:
        return None
    return json.loads(outline.model_dump_json())
