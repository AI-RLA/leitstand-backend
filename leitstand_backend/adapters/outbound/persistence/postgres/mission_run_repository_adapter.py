"""Postgres-backed MissionRunRepository."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import cast, delete, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from leitstand_backend.adapters.outbound.persistence.postgres.models import (
    MissionRunRow,
    MissionRunTransitionRow,
    StageRunRow,
)
from leitstand_backend.domain.errors import RobotBusy
from leitstand_backend.domain.model.mission.mission import Stage
from leitstand_backend.domain.model.mission.mission_run import (
    LastReport,
    MissionRun,
    MissionRunSummary,
    RunOrigin,
    RunSiteAnchor,
)
from leitstand_backend.domain.model.mission.mission_state import MissionError
from leitstand_backend.domain.model.mission.run_lifecycle import (
    ACTIVE_STATES,
    EXECUTING_STATES,
    TERMINAL_STATES,
    RunTrigger,
    actor_of,
    is_terminal,
)
from leitstand_backend.domain.model.mission.run_status import RunStatus
from leitstand_backend.domain.model.mission.run_transition import RunTransition
from leitstand_backend.domain.model.mission.stage_state_record import StageStateRecord
from leitstand_backend.ports.outbound.mission_run_repository import MissionRunRepository

_STAGE = TypeAdapter(Stage)
_ACTIVE = tuple(s.value for s in ACTIVE_STATES)
_EXECUTING = tuple(s.value for s in EXECUTING_STATES)
_TERMINAL = tuple(s.value for s in TERMINAL_STATES)
_ROBOT_INDEX = "uq_run_robot_active"


class PostgresMissionRunRepositoryAdapter(MissionRunRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, run: MissionRun) -> MissionRun:
        stmt = (
            pg_insert(MissionRunRow)
            .values(
                run_id=run.run_id,
                mission_id=run.mission_id,
                robot_id=run.robot_id,
                status=run.status.value,
                stages=[stage.model_dump(mode="json") for stage in run.stages],
                stages_digest=run.stages_digest,
                site_anchors=(
                    {sid: a.model_dump(mode="json") for sid, a in run.site_anchors.items()}
                    if run.site_anchors
                    else None
                ),
                origin=run.origin.model_dump(mode="json"),
                notes=run.notes,
                failure_errors=None,
                created_at=run.created_at,
                dispatched_at=run.dispatched_at,
                started_at=run.started_at,
                ended_at=run.ended_at,
                last_report=(run.last_report.model_dump(mode="json") if run.last_report else None),
                updated_at=run.updated_at,
            )
            .returning(MissionRunRow)
        )
        # A savepoint keeps the transaction usable after the robot index refuses, so the run
        # that holds the robot can be named in the error rather than a bare constraint.
        try:
            async with self._session.begin_nested():
                row = (await self._session.execute(stmt)).scalar_one()
        except IntegrityError as exc:
            if _ROBOT_INDEX not in str(exc.orig):
                raise
            holders = await self.list_active_by_robot(run.robot_id)
            raise RobotBusy(run.robot_id, holders[0].mission_id if holders else run.mission_id)
        return _to_run(row)

    async def get(self, run_id: UUID) -> MissionRun | None:
        row = await self._session.get(MissionRunRow, run_id)
        if row is None:
            return None
        return _to_run(row, await self.list_transitions(run_id))

    async def get_for_update(self, run_id: UUID) -> MissionRun | None:
        stmt = select(MissionRunRow).where(MissionRunRow.run_id == run_id).with_for_update()
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return _to_run(row, await self.list_transitions(run_id))

    async def update_status(
        self,
        run_id: UUID,
        new_status: RunStatus,
        *,
        expected: RunStatus,
        trigger: RunTrigger,
        report_header_id: int | None = None,
        acknowledged: bool | None = None,
        detail: dict | None = None,
        errors: list[MissionError] | None = None,
        dispatched_at: datetime | None = None,
    ) -> MissionRun | None:
        now = datetime.now(timezone.utc)
        values: dict = {"status": new_status.value, "updated_at": now}
        if errors is not None:
            values["failure_errors"] = [e.model_dump(mode="json") for e in errors]
        if dispatched_at is not None:
            values["dispatched_at"] = dispatched_at
        if new_status is RunStatus.RUNNING:
            values["started_at"] = func.coalesce(MissionRunRow.started_at, now)
        if is_terminal(new_status):
            values["ended_at"] = now
        stmt = (
            update(MissionRunRow)
            .where(
                MissionRunRow.run_id == run_id,
                MissionRunRow.status == expected.value,
                MissionRunRow.status.notin_(_TERMINAL),
            )
            .values(**values)
            .returning(MissionRunRow)
            .execution_options(populate_existing=True)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        await self._session.execute(
            pg_insert(MissionRunTransitionRow).values(
                run_id=run_id,
                from_status=expected.value,
                to_status=new_status.value,
                trigger=trigger.value,
                at=now,
                actor=actor_of(trigger),
                report_header_id=report_header_id,
                acknowledged=acknowledged,
                detail=detail,
            )
        )
        return _to_run(row)

    async def set_acknowledged(
        self, run_id: UUID, acknowledged: bool, detail: dict | None = None
    ) -> None:
        latest = (
            select(MissionRunTransitionRow.id)
            .where(MissionRunTransitionRow.run_id == run_id)
            .order_by(MissionRunTransitionRow.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        values: dict = {"acknowledged": acknowledged}
        if detail:
            values["detail"] = func.coalesce(MissionRunTransitionRow.detail, cast({}, JSONB)).op(
                "||"
            )(cast(detail, JSONB))
        await self._session.execute(
            update(MissionRunTransitionRow)
            .where(MissionRunTransitionRow.id == latest)
            .values(**values)
        )

    async def list_transitions(self, run_id: UUID) -> list[RunTransition]:
        stmt = (
            select(MissionRunTransitionRow)
            .where(MissionRunTransitionRow.run_id == run_id)
            .order_by(MissionRunTransitionRow.id)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            RunTransition(
                run_id=row.run_id,
                from_status=RunStatus(row.from_status),
                to_status=RunStatus(row.to_status),
                trigger=RunTrigger(row.trigger),
                at=row.at,
                actor=row.actor,
                report_header_id=row.report_header_id,
                acknowledged=row.acknowledged,
                detail=row.detail,
            )
            for row in rows
        ]

    async def mark_dispatched_at(self, run_id: UUID, when: datetime) -> None:
        await self._session.execute(
            update(MissionRunRow)
            .where(MissionRunRow.run_id == run_id, MissionRunRow.dispatched_at.is_(None))
            .values(dispatched_at=when)
        )

    async def set_notes(self, run_id: UUID, notes: str | None) -> MissionRun | None:
        stmt = (
            update(MissionRunRow)
            .where(MissionRunRow.run_id == run_id)
            .values(notes=notes, updated_at=datetime.now(timezone.utc))
            .returning(MissionRunRow)
            .execution_options(populate_existing=True)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_run(row) if row else None

    async def touch_last_report(self, run_id: UUID, report: LastReport) -> None:
        await self._session.execute(
            update(MissionRunRow)
            .where(MissionRunRow.run_id == run_id)
            .values(last_report=report.model_dump(mode="json"))
        )

    async def list_by_mission(
        self, mission_id: UUID, *, limit: int = 50, before: datetime | None = None
    ) -> list[MissionRunSummary]:
        stmt = (
            select(MissionRunRow)
            .where(MissionRunRow.mission_id == mission_id)
            .order_by(MissionRunRow.created_at.desc())
            .limit(limit)
        )
        if before is not None:
            stmt = stmt.where(MissionRunRow.created_at < before)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_summary(row) for row in rows]

    async def list_active_by_mission(self, mission_id: UUID) -> list[MissionRunSummary]:
        stmt = (
            select(MissionRunRow)
            .where(MissionRunRow.mission_id == mission_id, MissionRunRow.status.in_(_ACTIVE))
            .order_by(MissionRunRow.created_at.desc())
        )
        return [_to_summary(r) for r in (await self._session.execute(stmt)).scalars()]

    async def list_active_by_robot(self, robot_id: str) -> list[MissionRunSummary]:
        stmt = (
            select(MissionRunRow)
            .where(MissionRunRow.robot_id == robot_id, MissionRunRow.status.in_(_ACTIVE))
            .order_by(MissionRunRow.created_at.desc())
        )
        return [_to_summary(r) for r in (await self._session.execute(stmt)).scalars()]

    async def has_executing_run(self, robot_id: str) -> bool:
        stmt = select(
            select(MissionRunRow.run_id)
            .where(MissionRunRow.robot_id == robot_id, MissionRunRow.status.in_(_EXECUTING))
            .exists()
        )
        return bool((await self._session.execute(stmt)).scalar())

    async def list_active(self) -> list[MissionRunSummary]:
        stmt = select(MissionRunRow).where(MissionRunRow.status.in_(_ACTIVE))
        return [_to_summary(r) for r in (await self._session.execute(stmt)).scalars()]

    async def latest_by_mission(self, mission_ids: list[UUID]) -> dict[UUID, MissionRunSummary]:
        if not mission_ids:
            return {}
        stmt = (
            select(MissionRunRow)
            .distinct(MissionRunRow.mission_id)
            .where(MissionRunRow.mission_id.in_(mission_ids))
            .order_by(MissionRunRow.mission_id, MissionRunRow.created_at.desc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return {row.mission_id: _to_summary(row) for row in rows}

    async def count_by_mission(self, mission_id: UUID) -> int:
        stmt = select(func.count()).where(MissionRunRow.mission_id == mission_id)
        return int((await self._session.execute(stmt)).scalar_one())

    async def upsert_stage_runs(self, run_id: UUID, records: list[StageStateRecord]) -> None:
        if records:
            await self._write_stage_rows(run_id, records, versioned=True)

    async def overwrite_stage_runs(self, run_id: UUID, records: list[StageStateRecord]) -> None:
        if records:
            await self._write_stage_rows(run_id, records, versioned=False)

    async def _write_stage_rows(
        self, run_id: UUID, records: list[StageStateRecord], *, versioned: bool
    ) -> None:
        """Upsert the given stage rows, keyed ``(run_id, stage_id)``.

        When ``versioned``, a row advances only on a strictly higher ``header_id``; otherwise the
        write is unconditional. Both assume the caller holds the run's
        row lock.
        """
        now = datetime.now(timezone.utc)
        values = [
            {
                "run_id": run_id,
                "stage_id": record.stage_id,
                "header_id": record.header_id,
                "stage_index": record.stage_index,
                "status": record.status.value,
                "progress": record.progress,
                "result": record.result,
                "reported_started_at": record.started_at,
                "reported_ended_at": record.ended_at,
                "occurred_at": record.source_ts,
                "recorded_at": now,
                "status_source": record.status_source,
                "parent_stage_id": record.parent_stage_id,
            }
            for record in records
        ]
        stmt = pg_insert(StageRunRow).values(values)
        set_ = {
            column.name: stmt.excluded[column.name]
            for column in StageRunRow.__table__.columns
            if not column.primary_key
        }
        index = [StageRunRow.run_id, StageRunRow.stage_id]
        if versioned:
            # The header_id guard applies to the conflict UPDATE only; a first insert always succeeds.
            stmt = stmt.on_conflict_do_update(
                index_elements=index,
                set_=set_,
                where=StageRunRow.header_id < stmt.excluded.header_id,
            )
        else:
            stmt = stmt.on_conflict_do_update(index_elements=index, set_=set_)
        await self._session.execute(stmt)

    async def get_stage_runs(self, run_id: UUID) -> list[StageStateRecord]:
        stmt = (
            select(StageRunRow)
            .where(StageRunRow.run_id == run_id)
            .order_by(StageRunRow.stage_index)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            StageStateRecord(
                stage_id=row.stage_id,
                header_id=row.header_id,
                stage_index=row.stage_index,
                status=row.status,
                progress=row.progress,
                started_at=row.reported_started_at,
                ended_at=row.reported_ended_at,
                result=row.result,
                source_ts=row.occurred_at,
                status_source=row.status_source,
                parent_stage_id=row.parent_stage_id,
            )
            for row in rows
        ]

    async def delete(self, run_id: UUID) -> None:
        await self._session.execute(delete(StageRunRow).where(StageRunRow.run_id == run_id))
        await self._session.execute(
            delete(MissionRunTransitionRow).where(MissionRunTransitionRow.run_id == run_id)
        )
        await self._session.execute(delete(MissionRunRow).where(MissionRunRow.run_id == run_id))


def _to_summary(row: MissionRunRow) -> MissionRunSummary:
    return MissionRunSummary(
        run_id=row.run_id,
        mission_id=row.mission_id,
        robot_id=row.robot_id,
        status=RunStatus(row.status),
        stages_digest=row.stages_digest,
        origin=RunOrigin.model_validate(row.origin),
        notes=row.notes,
        created_at=row.created_at,
        dispatched_at=row.dispatched_at,
        started_at=row.started_at,
        ended_at=row.ended_at,
        last_report=LastReport.model_validate(row.last_report) if row.last_report else None,
        updated_at=row.updated_at,
    )


def _to_run(row: MissionRunRow, transitions: list[RunTransition] | None = None) -> MissionRun:
    return MissionRun(
        **_to_summary(row).model_dump(),
        transitions=transitions or [],
        stages=[_STAGE.validate_python(s) for s in row.stages],
        site_anchors=(
            {sid: RunSiteAnchor.model_validate(a) for sid, a in row.site_anchors.items()}
            if row.site_anchors
            else None
        ),
        failure_errors=(
            [MissionError.model_validate(e) for e in row.failure_errors]
            if row.failure_errors
            else None
        ),
    )
