"""Postgres-backed MissionRepository: the definition and its stages as rows."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from leitstand_backend.adapters.outbound.persistence.postgres.models import (
    MissionRow,
    MissionStageRow,
)
from leitstand_backend.domain.model.mission.mission import Mission, Stage, stage_waypoints
from leitstand_backend.domain.model.mission.waypoint import SiteLocalWaypoint
from leitstand_backend.ports.outbound.mission_repository import MissionRepository

# Stage is a discriminated union rather than a class, so it is validated through an
# adapter. Built once: constructing one per row would rebuild the schema on every read.
_STAGE = TypeAdapter(Stage)


class PostgresMissionRepositoryAdapter(MissionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, mission: Mission) -> Mission:
        stmt = (
            pg_insert(MissionRow)
            .values(
                mission_id=mission.mission_id,
                name=mission.name,
                description=mission.description,
                assigned_robot_id=mission.assigned_robot_id,
                archived_at=mission.archived_at,
                created_at=mission.created_at,
                updated_at=mission.updated_at,
            )
            .on_conflict_do_update(
                index_elements=[MissionRow.mission_id],
                set_={
                    "name": mission.name,
                    "description": mission.description,
                    "updated_at": mission.updated_at,
                },
            )
            .returning(MissionRow)
        )
        row = (await self._session.execute(stmt)).scalar_one()
        await self.replace_stages(mission.mission_id, mission.stages)
        return _to_domain(row, mission.stages)

    async def get(self, mission_id: UUID) -> Mission | None:
        row = await self._session.get(MissionRow, mission_id)
        if row is None:
            return None
        return _to_domain(row, await self.get_stages(mission_id))

    async def list(
        self,
        *,
        robot_id: str | None = None,
        name: str | None = None,
        include_archived: bool = False,
    ) -> list[Mission]:
        stmt = select(MissionRow).order_by(MissionRow.created_at.desc())
        if not include_archived:
            stmt = stmt.where(MissionRow.archived_at.is_(None))
        if robot_id is not None:
            stmt = stmt.where(MissionRow.assigned_robot_id == robot_id)
        if name is not None:
            stmt = stmt.where(func.lower(MissionRow.name) == name.lower())
        rows = (await self._session.execute(stmt)).scalars().all()
        stages = await self._stages_for([row.mission_id for row in rows])
        return [_to_domain(row, stages.get(row.mission_id, [])) for row in rows]

    async def replace_stages(self, mission_id: UUID, stages: list[Stage]) -> None:
        values = _flatten(mission_id, stages)
        keep = {v["stage_id"] for v in values}
        # Dropped stages go first so a kept stage can take a dropped one's sequence; the
        # deferred uniqueness lets kept stages swap sequences within the same statement.
        await self._session.execute(
            delete(MissionStageRow).where(
                MissionStageRow.mission_id == mission_id,
                MissionStageRow.stage_id.notin_(keep),
            )
        )
        if not values:
            return
        stmt = pg_insert(MissionStageRow).values(values)
        set_ = {
            column.name: stmt.excluded[column.name]
            for column in MissionStageRow.__table__.columns
            if not column.primary_key
        }
        await self._session.execute(
            stmt.on_conflict_do_update(index_elements=[MissionStageRow.stage_id], set_=set_)
        )

    async def get_stages(self, mission_id: UUID) -> list[Stage]:
        return (await self._stages_for([mission_id])).get(mission_id, [])

    async def _stages_for(self, mission_ids: list[UUID]) -> dict[UUID, list[Stage]]:
        if not mission_ids:
            return {}
        stmt = (
            select(MissionStageRow)
            .where(MissionStageRow.mission_id.in_(mission_ids))
            .order_by(MissionStageRow.mission_id, MissionStageRow.sequence)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        by_mission: dict[UUID, list[MissionStageRow]] = defaultdict(list)
        for row in rows:
            by_mission[row.mission_id].append(row)
        return {mid: _nest(mission_rows) for mid, mission_rows in by_mission.items()}

    async def set_assigned_robot(self, mission_id: UUID, robot_id: str | None) -> None:
        await self._session.execute(
            update(MissionRow)
            .where(MissionRow.mission_id == mission_id)
            .values(assigned_robot_id=robot_id, updated_at=datetime.now(timezone.utc))
        )

    async def archive(self, mission_id: UUID) -> Mission | None:
        return await self._set_archived(mission_id, datetime.now(timezone.utc))

    async def restore(self, mission_id: UUID) -> Mission | None:
        return await self._set_archived(mission_id, None)

    async def _set_archived(self, mission_id: UUID, when: datetime | None) -> Mission | None:
        stmt = (
            update(MissionRow)
            .where(MissionRow.mission_id == mission_id)
            .values(archived_at=when, updated_at=datetime.now(timezone.utc))
            .returning(MissionRow)
            .execution_options(populate_existing=True)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return _to_domain(row, await self.get_stages(mission_id))

    async def get_for_update(self, mission_id: UUID) -> Mission | None:
        stmt = select(MissionRow).where(MissionRow.mission_id == mission_id).with_for_update()
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return _to_domain(row, await self.get_stages(mission_id))

    async def mission_id_for_stage(self, stage_id: UUID) -> UUID | None:
        stmt = select(MissionStageRow.mission_id).where(MissionStageRow.stage_id == stage_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def missions_referencing_site(self, site_id: UUID) -> list[UUID]:
        stmt = (
            select(MissionStageRow.mission_id).where(MissionStageRow.site_id == site_id).distinct()
        )
        return [mid for (mid,) in (await self._session.execute(stmt)).all()]

    async def delete(self, mission_id: UUID) -> None:
        # Stages cascade from the definition.
        await self._session.execute(delete(MissionRow).where(MissionRow.mission_id == mission_id))


def _site_of(stage: Stage) -> UUID | None:
    """The one site a site-local stage is driven in; None for a WGS84 stage.

    The application refuses a stage spanning sites before it is saved, so the first site-local
    waypoint's site is the stage's site.
    """
    for waypoint in stage_waypoints(stage):
        if isinstance(waypoint, SiteLocalWaypoint):
            return waypoint.site_id
    return None


def _flatten(
    mission_id: UUID, stages: list[Stage], parent_stage_id: UUID | None = None
) -> list[dict]:
    """One row per stage at every depth; a cleanup stage points at its parent."""
    rows: list[dict] = []
    for sequence, stage in enumerate(stages):
        rows.append(
            {
                "stage_id": stage.stage_id,
                "mission_id": mission_id,
                "sequence": sequence,
                "kind": stage.kind,
                "site_id": _site_of(stage),
                "parent_stage_id": parent_stage_id,
                "spec": stage.model_dump(mode="json", exclude={"stage_id", "on_cancel"}),
            }
        )
        if stage.on_cancel:
            rows.extend(_flatten(mission_id, stage.on_cancel, stage.stage_id))
    return rows


def _nest(rows: list[MissionStageRow]) -> list[Stage]:
    """Rebuild the nested domain shape from flat rows ordered by sequence."""
    children: dict[UUID | None, list[MissionStageRow]] = defaultdict(list)
    for row in rows:
        children[row.parent_stage_id].append(row)

    def build(parent: UUID | None) -> list[Stage]:
        return [
            _STAGE.validate_python(
                {
                    **row.spec,
                    "stage_id": row.stage_id,
                    "on_cancel": build(row.stage_id) or None,
                }
            )
            for row in sorted(children[parent], key=lambda r: r.sequence)
        ]

    return build(None)


def _to_domain(row: MissionRow, stages: list[Stage]) -> Mission:
    return Mission(
        mission_id=row.mission_id,
        name=row.name,
        description=row.description,
        stages=stages,
        assigned_robot_id=row.assigned_robot_id,
        archived_at=row.archived_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
