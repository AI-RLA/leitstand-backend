"""Postgres-backed MissionRepository."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from leitstand_backend.adapters.outbound.persistence.postgres.models import (
    MissionRow,
    MissionSiteRefRow,
    MissionStageStateRow,
)
from leitstand_backend.domain.errors import MissionNotFoundError
from leitstand_backend.domain.model.mission.coverage import CoverageProvenance
from leitstand_backend.domain.model.mission.mission import (
    Mission,
    MissionStatus,
    Stage,
    referenced_site_ids,
)
from leitstand_backend.domain.model.mission.mission_lifecycle import EXECUTING_STATES, is_terminal
from leitstand_backend.domain.model.mission.mission_state import MissionError
from leitstand_backend.domain.model.mission.stage_state_record import StageStateRecord
from leitstand_backend.ports.outbound.mission_repository import MissionRecord, MissionRepository

_TERMINAL_STATUSES = (
    MissionStatus.SUCCEEDED.value,
    MissionStatus.FAILED.value,
    MissionStatus.CANCELLED.value,
)
_EXECUTING_STATUSES = tuple(s.value for s in EXECUTING_STATES)

# Stage is a discriminated union rather than a class, so it is validated through an
# adapter. Built once: constructing one per row would rebuild the schema on every read.
_STAGE = TypeAdapter(Stage)


class PostgresMissionRepositoryAdapter(MissionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, mission: Mission) -> Mission:
        stages_json = [s.model_dump(mode="json") for s in mission.stages]
        stmt = (
            pg_insert(MissionRow)
            .values(
                mission_id=mission.mission_id,
                update_id=mission.update_id,
                name=mission.name,
                description=mission.description,
                status=MissionStatus.DRAFT.value,
                stages=stages_json,
                robot_id=None,
                dispatched_at=None,
                created_at=mission.created_at,
                updated_at=mission.updated_at,
            )
            .on_conflict_do_update(
                index_elements=[MissionRow.mission_id, MissionRow.update_id],
                set_={
                    "name": mission.name,
                    "description": mission.description,
                    "stages": stages_json,
                    "updated_at": mission.updated_at,
                },
            )
            .returning(MissionRow)
        )
        row = (await self._session.execute(stmt)).scalar_one()
        await self._sync_site_refs(mission.mission_id, mission.stages)
        return _to_domain(row)

    async def get(self, mission_id: UUID, update_id: int = 0) -> Mission | None:
        row = await self._session.get(MissionRow, (mission_id, update_id))
        return _to_domain(row) if row else None

    async def list(self) -> list[Mission]:
        stmt = select(MissionRow).order_by(MissionRow.created_at.desc())
        result = await self._session.execute(stmt)
        return [_to_domain(row) for row in result.scalars()]

    async def list_by_robot(self, robot_id: str) -> list[Mission]:
        stmt = (
            select(MissionRow)
            .where(MissionRow.robot_id == robot_id)
            .order_by(MissionRow.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return [_to_domain(row) for row in result.scalars()]

    async def list_active_by_robot(self, robot_id: str) -> list[Mission]:
        stmt = (
            select(MissionRow)
            .where(MissionRow.robot_id == robot_id)
            .where(MissionRow.status.notin_(_TERMINAL_STATUSES))
            .order_by(MissionRow.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return [_to_domain(row) for row in result.scalars()]

    async def executing_robot_ids(self) -> set[str]:
        rows = await self._session.execute(
            select(MissionRow.robot_id)
            .where(MissionRow.update_id == 0)
            .where(MissionRow.status.in_(_EXECUTING_STATUSES))
            .where(MissionRow.robot_id.is_not(None))
            .distinct()
        )
        return {r for (r,) in rows.all()}

    async def list_executing_by_robot(self, robot_id: str) -> list[Mission]:
        stmt = (
            select(MissionRow)
            .where(MissionRow.robot_id == robot_id)
            .where(MissionRow.status.in_(_EXECUTING_STATUSES))
            .order_by(MissionRow.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return [_to_domain(row) for row in result.scalars()]

    async def list_active(self) -> list[Mission]:
        stmt = (
            select(MissionRow)
            .where(MissionRow.status.notin_(_TERMINAL_STATUSES))
            .order_by(MissionRow.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return [_to_domain(row) for row in result.scalars()]

    async def get_status(self, mission_id: UUID) -> MissionStatus:
        stmt = select(MissionRow.status).where(
            MissionRow.mission_id == mission_id,
            MissionRow.update_id == 0,
        )
        status_str = (await self._session.execute(stmt)).scalar_one_or_none()
        if status_str is None:
            raise MissionNotFoundError(mission_id)
        return MissionStatus(status_str)

    async def update_status(
        self,
        mission_id: UUID,
        new_status: MissionStatus,
        *,
        errors: list[MissionError] | None = None,
    ) -> Mission | None:
        stmt = (
            update(MissionRow)
            .where(MissionRow.mission_id == mission_id, MissionRow.update_id == 0)
            .where(MissionRow.status.notin_(_TERMINAL_STATUSES))
            .values(status=new_status.value, updated_at=datetime.now(timezone.utc))
        )
        # Persist the failure cause in the same UPDATE as the status so the two cannot diverge.
        if errors is not None:
            stmt = stmt.values(failure_errors=[e.model_dump(mode="json") for e in errors])
        stmt = stmt.returning(MissionRow).execution_options(populate_existing=True)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is not None and is_terminal(new_status):
            # A terminal mission no longer blocks site deletion; drop its refs so
            # the FK does not pin sites referenced only by finished missions.
            await self._session.execute(
                delete(MissionSiteRefRow).where(MissionSiteRefRow.mission_id == mission_id)
            )
        return _to_domain(row) if row else None

    async def set_robot(self, mission_id: UUID, robot_id: str | None) -> None:
        stmt = (
            update(MissionRow)
            .where(MissionRow.mission_id == mission_id, MissionRow.update_id == 0)
            .values(robot_id=robot_id, updated_at=datetime.now(timezone.utc))
        )
        await self._session.execute(stmt)

    async def assign_robot(
        self,
        mission_id: UUID,
        robot_id: str,
        dispatched_at: datetime,
    ) -> None:
        stmt = (
            update(MissionRow)
            .where(MissionRow.mission_id == mission_id, MissionRow.update_id == 0)
            .values(
                robot_id=robot_id,
                dispatched_at=dispatched_at,
                updated_at=dispatched_at,
            )
        )
        await self._session.execute(stmt)

    async def reset_to_draft(self, mission_id: UUID) -> Mission | None:
        _RESETTABLE = (MissionStatus.FAILED.value, MissionStatus.CANCELLED.value)
        stmt = (
            update(MissionRow)
            .where(MissionRow.mission_id == mission_id, MissionRow.update_id == 0)
            .where(MissionRow.status.in_(_RESETTABLE))
            .values(
                status=MissionStatus.DRAFT.value,
                robot_id=None,
                dispatched_at=None,
                failure_errors=None,
                updated_at=datetime.now(timezone.utc),
            )
            .returning(MissionRow)
            .execution_options(populate_existing=True)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            # Reset starts a fresh run, so drop the prior run's resolved per-stage rows and
            # rebuild the site refs that were pruned when the mission went terminal.
            await self._session.execute(
                delete(MissionStageStateRow).where(MissionStageStateRow.mission_id == mission_id)
            )
            await self._sync_site_refs(mission_id, _to_domain(row).stages)
        return _to_domain(row) if row else None

    async def _sync_site_refs(self, mission_id: UUID, stages: list[Stage]) -> None:
        """Replace the mission's site refs with those referenced by ``stages``."""
        await self._session.execute(
            delete(MissionSiteRefRow).where(MissionSiteRefRow.mission_id == mission_id)
        )
        site_ids = referenced_site_ids(stages)
        if site_ids:
            await self._session.execute(
                pg_insert(MissionSiteRefRow).values(
                    [{"mission_id": mission_id, "site_id": sid} for sid in site_ids]
                )
            )

    async def get_assigned_robot(self, mission_id: UUID) -> str | None:
        stmt = select(MissionRow.robot_id).where(
            MissionRow.mission_id == mission_id,
            MissionRow.update_id == 0,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def save_coverage_provenance(
        self,
        mission_id: UUID,
        provenance: CoverageProvenance,
    ) -> None:
        await self._session.execute(
            update(MissionRow)
            .where(MissionRow.mission_id == mission_id, MissionRow.update_id == 0)
            .values(coverage=provenance.model_dump(mode="json"))
        )

    async def upsert_stage_states(
        self,
        mission_id: UUID,
        records: list[StageStateRecord],
    ) -> None:
        if records:
            await self._write_stage_rows(mission_id, records, versioned=True)

    async def overwrite_stage_states(
        self,
        mission_id: UUID,
        records: list[StageStateRecord],
    ) -> None:
        if records:
            await self._write_stage_rows(mission_id, records, versioned=False)

    async def _write_stage_rows(
        self,
        mission_id: UUID,
        records: list[StageStateRecord],
        *,
        versioned: bool,
    ) -> None:
        """Upsert the given stage rows, keyed ``(mission_id, stage_id)``.

        When ``versioned``, a row advances only on a strictly higher ``header_id``; otherwise
        the write is unconditional. Both assume the caller holds the mission row lock.
        """
        now = datetime.now(timezone.utc)
        values = [
            {
                "mission_id": mission_id,
                "stage_id": record.stage_id,
                "header_id": record.header_id,
                "stage_index": record.stage_index,
                "status": record.status.value,
                "progress": record.progress,
                "started_at": record.started_at,
                "ended_at": record.ended_at,
                "result": record.result,
                "updated_at": now,
                "source_ts": record.source_ts,
            }
            for record in records
        ]
        stmt = pg_insert(MissionStageStateRow).values(values)
        set_ = {
            column.name: stmt.excluded[column.name]
            for column in MissionStageStateRow.__table__.columns
            if not column.primary_key
        }
        index = [MissionStageStateRow.mission_id, MissionStageStateRow.stage_id]
        if versioned:
            # The header_id guard applies to the conflict UPDATE only; a first insert lands.
            stmt = stmt.on_conflict_do_update(
                index_elements=index,
                set_=set_,
                where=MissionStageStateRow.header_id < stmt.excluded.header_id,
            )
        else:
            stmt = stmt.on_conflict_do_update(index_elements=index, set_=set_)
        await self._session.execute(stmt)

    async def get_stage_states(self, mission_id: UUID) -> list[StageStateRecord]:
        stmt = (
            select(MissionStageStateRow)
            .where(MissionStageStateRow.mission_id == mission_id)
            .order_by(MissionStageStateRow.stage_index)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [StageStateRecord.model_validate(row, from_attributes=True) for row in rows]

    async def delete(self, mission_id: UUID) -> None:
        await self._session.execute(
            delete(MissionStageStateRow).where(MissionStageStateRow.mission_id == mission_id)
        )
        await self._session.execute(
            delete(MissionSiteRefRow).where(MissionSiteRefRow.mission_id == mission_id)
        )
        await self._session.execute(delete(MissionRow).where(MissionRow.mission_id == mission_id))

    async def get_record(self, mission_id: UUID) -> MissionRecord | None:
        row = await self._session.get(MissionRow, (mission_id, 0))
        return _to_record(row) if row else None

    async def get_record_for_update(self, mission_id: UUID) -> MissionRecord | None:
        stmt = (
            select(MissionRow)
            .where(MissionRow.mission_id == mission_id, MissionRow.update_id == 0)
            .with_for_update()
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_record(row) if row else None

    async def list_records(
        self, *, robot_id: str | None = None, name: str | None = None
    ) -> list[MissionRecord]:
        stmt = (
            select(MissionRow)
            .where(MissionRow.update_id == 0)
            .order_by(MissionRow.created_at.desc())
        )
        if robot_id is not None:
            stmt = stmt.where(MissionRow.robot_id == robot_id)
        if name is not None:
            stmt = stmt.where(func.lower(MissionRow.name) == name.lower())
        result = await self._session.execute(stmt)
        return [_to_record(row) for row in result.scalars()]


def _to_domain(row: MissionRow) -> Mission:
    return Mission(
        mission_id=row.mission_id,
        update_id=row.update_id,
        name=row.name,
        description=row.description,
        stages=[_STAGE.validate_python(s) for s in row.stages],
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_record(row: MissionRow) -> MissionRecord:
    return MissionRecord(
        mission=_to_domain(row),
        status=MissionStatus(row.status),
        robot_id=row.robot_id,
        dispatched_at=row.dispatched_at,
        failure_errors=(
            [MissionError.model_validate(e) for e in row.failure_errors]
            if row.failure_errors
            else None
        ),
        coverage=(CoverageProvenance.model_validate(row.coverage) if row.coverage else None),
    )
