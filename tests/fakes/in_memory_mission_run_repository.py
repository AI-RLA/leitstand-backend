"""In-memory MissionRunRepository fake (test-only).

Mirrors the Postgres adapter's two invariants that tests depend on: the status write is a
compare-and-set that never overwrites a terminal row, and a live stage row advances only on a
strictly higher ``header_id``. The one-robot-one-run index is mirrored too, so a test can see the
race the database would refuse.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from uuid import UUID

from leitstand_backend.domain.errors import RobotBusy
from leitstand_backend.domain.model.mission.mission_run import (
    LastReport,
    MissionRun,
    MissionRunSummary,
)
from leitstand_backend.domain.model.mission.mission_state import MissionError
from leitstand_backend.domain.model.mission.run_lifecycle import (
    RunTrigger,
    actor_of,
    is_active,
    is_executing,
    is_terminal,
)
from leitstand_backend.domain.model.mission.run_status import RunStatus
from leitstand_backend.domain.model.mission.run_transition import RunTransition
from leitstand_backend.domain.model.mission.stage_state_record import StageStateRecord
from leitstand_backend.ports.outbound.mission_run_repository import MissionRunRepository


class InMemoryMissionRunRepository(MissionRunRepository):
    def __init__(self) -> None:
        self._runs: dict[UUID, MissionRun] = {}
        self._stage_runs: dict[tuple[UUID, UUID], StageStateRecord] = {}
        self._transitions: dict[UUID, list[RunTransition]] = {}
        self._lock = threading.Lock()

    async def create(self, run: MissionRun) -> MissionRun:
        with self._lock:
            for other in self._runs.values():
                if other.robot_id == run.robot_id and is_active(other.status):
                    raise RobotBusy(run.robot_id, other.mission_id)
            self._runs[run.run_id] = run
        return run

    async def get(self, run_id: UUID) -> MissionRun | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            return run.model_copy(update={"transitions": list(self._transitions.get(run_id, []))})

    async def get_for_update(self, run_id: UUID) -> MissionRun | None:
        return await self.get(run_id)

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
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.status is not expected or is_terminal(run.status):
                return None
            self._transitions.setdefault(run_id, []).append(
                RunTransition(
                    run_id=run_id,
                    from_status=expected,
                    to_status=new_status,
                    trigger=trigger,
                    at=now,
                    actor=actor_of(trigger),
                    report_header_id=report_header_id,
                    acknowledged=acknowledged,
                    detail=detail,
                )
            )
            patch: dict = {"status": new_status, "updated_at": now}
            if errors is not None:
                # Stored empty, read back as absent: what the Postgres column does.
                patch["failure_errors"] = errors or None
            if dispatched_at is not None:
                patch["dispatched_at"] = dispatched_at
            if new_status is RunStatus.RUNNING and run.started_at is None:
                patch["started_at"] = now
            if is_terminal(new_status):
                patch["ended_at"] = now
            updated = run.model_copy(update=patch)
            self._runs[run_id] = updated
            return updated

    async def set_acknowledged(
        self, run_id: UUID, acknowledged: bool, detail: dict | None = None
    ) -> None:
        with self._lock:
            rows = self._transitions.get(run_id)
            if not rows:
                return
            last = rows[-1]
            merged = {**(last.detail or {}), **(detail or {})} if (last.detail or detail) else None
            rows[-1] = last.model_copy(update={"acknowledged": acknowledged, "detail": merged})

    async def list_transitions(self, run_id: UUID) -> list[RunTransition]:
        with self._lock:
            return list(self._transitions.get(run_id, []))

    async def mark_dispatched_at(self, run_id: UUID, when: datetime) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None and run.dispatched_at is None:
                self._runs[run_id] = run.model_copy(update={"dispatched_at": when})

    async def set_notes(self, run_id: UUID, notes: str | None) -> MissionRun | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            updated = run.model_copy(update={"notes": notes})
            self._runs[run_id] = updated
            return updated

    async def touch_last_report(self, run_id: UUID, report: LastReport) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None:
                self._runs[run_id] = run.model_copy(update={"last_report": report})

    async def list_by_mission(
        self, mission_id: UUID, *, limit: int = 50, before: datetime | None = None
    ) -> list[MissionRunSummary]:
        with self._lock:
            runs = [
                r
                for r in self._runs.values()
                if r.mission_id == mission_id and (before is None or r.created_at < before)
            ]
        runs.sort(key=lambda r: r.created_at, reverse=True)
        return [_summary(r) for r in runs[:limit]]

    async def list_active_by_mission(self, mission_id: UUID) -> list[MissionRunSummary]:
        with self._lock:
            runs = [
                r for r in self._runs.values() if r.mission_id == mission_id and is_active(r.status)
            ]
        return [_summary(r) for r in sorted(runs, key=lambda r: r.created_at, reverse=True)]

    async def list_active_by_robot(self, robot_id: str) -> list[MissionRunSummary]:
        with self._lock:
            runs = [
                r for r in self._runs.values() if r.robot_id == robot_id and is_active(r.status)
            ]
        return [_summary(r) for r in runs]

    async def has_executing_run(self, robot_id: str) -> bool:
        with self._lock:
            return any(
                r.robot_id == robot_id and is_executing(r.status) for r in self._runs.values()
            )

    async def list_active(self) -> list[MissionRunSummary]:
        with self._lock:
            return [_summary(r) for r in self._runs.values() if is_active(r.status)]

    async def latest_by_mission(self, mission_ids: list[UUID]) -> dict[UUID, MissionRunSummary]:
        latest: dict[UUID, MissionRun] = {}
        with self._lock:
            for run in self._runs.values():
                if run.mission_id not in mission_ids:
                    continue
                current = latest.get(run.mission_id)
                if current is None or run.created_at > current.created_at:
                    latest[run.mission_id] = run
        return {mid: _summary(r) for mid, r in latest.items()}

    async def count_by_mission(self, mission_id: UUID) -> int:
        with self._lock:
            return sum(1 for r in self._runs.values() if r.mission_id == mission_id)

    async def upsert_stage_runs(self, run_id: UUID, records: list[StageStateRecord]) -> None:
        with self._lock:
            self._write_stage_rows(run_id, records, versioned=True)

    async def overwrite_stage_runs(self, run_id: UUID, records: list[StageStateRecord]) -> None:
        with self._lock:
            self._write_stage_rows(run_id, records, versioned=False)

    def _write_stage_rows(
        self, run_id: UUID, records: list[StageStateRecord], *, versioned: bool
    ) -> None:
        for record in records:
            key = (run_id, record.stage_id)
            existing = self._stage_runs.get(key)
            if versioned and existing is not None and record.header_id <= existing.header_id:
                continue
            self._stage_runs[key] = record

    async def get_stage_runs(self, run_id: UUID) -> list[StageStateRecord]:
        with self._lock:
            records = [rec for (rid, _), rec in self._stage_runs.items() if rid == run_id]
        return sorted(records, key=lambda rec: rec.stage_index)

    async def delete(self, run_id: UUID) -> None:
        with self._lock:
            self._runs.pop(run_id, None)
            self._transitions.pop(run_id, None)
            self._stage_runs = {k: v for k, v in self._stage_runs.items() if k[0] != run_id}

    # Test-only inspection helpers

    def set_status_directly(self, run_id: UUID, status: RunStatus) -> None:
        with self._lock:
            self._runs[run_id] = self._runs[run_id].model_copy(update={"status": status})


def _summary(run: MissionRun) -> MissionRunSummary:
    return MissionRunSummary.model_validate(
        run.model_dump(exclude={"stages", "site_anchors", "failure_errors"})
    )
