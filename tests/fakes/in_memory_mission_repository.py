"""In-memory MissionRepository fake (test-only)."""

from __future__ import annotations

import threading
from datetime import datetime
from uuid import UUID

from leitstand_backend.domain.errors import MissionNotFoundError
from leitstand_backend.domain.model.mission.mission import Mission, MissionStatus
from leitstand_backend.domain.model.mission.mission_lifecycle import is_executing, is_terminal
from leitstand_backend.domain.model.mission.mission_state import MissionError
from leitstand_backend.domain.model.mission.stage_state_record import StageStateRecord
from leitstand_backend.ports.outbound.mission_repository import MissionRecord, MissionRepository


class InMemoryMissionRepository(MissionRepository):
    def __init__(self) -> None:
        self._missions: dict[UUID, Mission] = {}
        self._status: dict[UUID, MissionStatus] = {}
        self._assignments: dict[UUID, tuple[str, datetime]] = {}
        self._stage_state: dict[tuple[UUID, UUID], StageStateRecord] = {}
        self._failure_errors: dict[UUID, list[MissionError]] = {}
        self._lock = threading.Lock()

    async def save(self, mission: Mission) -> Mission:
        with self._lock:
            self._missions[mission.mission_id] = mission
            self._status.setdefault(mission.mission_id, MissionStatus.DRAFT)
        return mission

    async def get(self, mission_id: UUID, update_id: int = 0) -> Mission | None:
        with self._lock:
            return self._missions.get(mission_id)

    async def list(self) -> list[Mission]:
        with self._lock:
            return sorted(self._missions.values(), key=lambda m: m.created_at, reverse=True)

    async def list_by_robot(self, robot_id: str) -> list[Mission]:
        with self._lock:
            mission_ids = [mid for mid, (rid, _) in self._assignments.items() if rid == robot_id]
            return [self._missions[mid] for mid in mission_ids if mid in self._missions]

    async def list_active_by_robot(self, robot_id: str) -> list[Mission]:
        with self._lock:
            return [
                self._missions[mid]
                for mid, (rid, _) in self._assignments.items()
                if rid == robot_id
                and mid in self._missions
                and not is_terminal(self._status.get(mid, MissionStatus.DRAFT))
            ]

    async def list_executing_by_robot(self, robot_id: str) -> list[Mission]:
        with self._lock:
            return [
                self._missions[mid]
                for mid, (rid, _) in self._assignments.items()
                if rid == robot_id
                and mid in self._missions
                and is_executing(self._status.get(mid, MissionStatus.DRAFT))
            ]

    async def list_active(self) -> list[Mission]:
        with self._lock:
            return [
                m
                for mid, m in self._missions.items()
                if not is_terminal(self._status.get(mid, MissionStatus.DRAFT))
            ]

    async def get_status(self, mission_id: UUID) -> MissionStatus:
        with self._lock:
            if mission_id not in self._status:
                raise MissionNotFoundError(mission_id)
            return self._status[mission_id]

    async def update_status(
        self,
        mission_id: UUID,
        new_status: MissionStatus,
        *,
        errors: list[MissionError] | None = None,
    ) -> Mission | None:
        with self._lock:
            if mission_id not in self._missions:
                return None
            # Mirror the real adapter's compare-and-set: never overwrite a
            # terminal row.
            if is_terminal(self._status.get(mission_id, MissionStatus.DRAFT)):
                return None
            self._status[mission_id] = new_status
            if errors is not None:
                self._failure_errors[mission_id] = errors
            return self._missions[mission_id]

    async def set_robot(self, mission_id: UUID, robot_id: str | None) -> None:
        with self._lock:
            if robot_id is None:
                self._assignments.pop(mission_id, None)
            else:
                existing = self._assignments.get(mission_id)
                dispatched_at = existing[1] if existing else None
                self._assignments[mission_id] = (robot_id, dispatched_at)

    async def assign_robot(
        self,
        mission_id: UUID,
        robot_id: str,
        dispatched_at: datetime,
    ) -> None:
        with self._lock:
            self._assignments[mission_id] = (robot_id, dispatched_at)

    async def reset_to_draft(self, mission_id: UUID) -> Mission | None:
        with self._lock:
            if mission_id not in self._missions:
                return None
            current = self._status.get(mission_id)
            if current not in (MissionStatus.FAILED, MissionStatus.CANCELLED):
                return None
            self._status[mission_id] = MissionStatus.DRAFT
            self._assignments.pop(mission_id, None)
            self._failure_errors.pop(mission_id, None)
            self._drop_stage_states(mission_id)
            return self._missions[mission_id]

    async def get_assigned_robot(self, mission_id: UUID) -> str | None:
        with self._lock:
            assignment = self._assignments.get(mission_id)
            return assignment[0] if assignment else None

    async def upsert_stage_states(
        self,
        mission_id: UUID,
        records: list[StageStateRecord],
    ) -> None:
        with self._lock:
            if records:
                self._write_stage_rows(mission_id, records, versioned=True)

    async def overwrite_stage_states(
        self,
        mission_id: UUID,
        records: list[StageStateRecord],
    ) -> None:
        with self._lock:
            if records:
                self._write_stage_rows(mission_id, records, versioned=False)

    def _write_stage_rows(
        self,
        mission_id: UUID,
        records: list[StageStateRecord],
        *,
        versioned: bool,
    ) -> None:
        for record in records:
            key = (mission_id, record.stage_id)
            existing = self._stage_state.get(key)
            if versioned and existing is not None and record.header_id <= existing.header_id:
                continue
            self._stage_state[key] = record

    async def get_stage_states(self, mission_id: UUID) -> list[StageStateRecord]:
        with self._lock:
            records = [
                record for (mid, _), record in self._stage_state.items() if mid == mission_id
            ]
        return sorted(records, key=lambda record: record.stage_index)

    async def get_record(self, mission_id: UUID) -> MissionRecord | None:
        with self._lock:
            mission = self._missions.get(mission_id)
            if mission is None:
                return None
            status = self._status.get(mission_id, MissionStatus.DRAFT)
            assignment = self._assignments.get(mission_id)
            robot_id = assignment[0] if assignment else None
            dispatched_at = assignment[1] if assignment else None
            failure_errors = self._failure_errors.get(mission_id)
        return MissionRecord(
            mission=mission,
            status=status,
            robot_id=robot_id,
            dispatched_at=dispatched_at,
            failure_errors=failure_errors,
        )

    async def get_record_for_update(self, mission_id: UUID) -> MissionRecord | None:
        # No real row lock in the in-memory fake; tests drive record() calls sequentially.
        return await self.get_record(mission_id)

    async def list_records(self, *, robot_id: str | None = None) -> list[MissionRecord]:
        with self._lock:
            result = []
            for mission in sorted(
                self._missions.values(), key=lambda m: m.created_at, reverse=True
            ):
                status = self._status.get(mission.mission_id, MissionStatus.DRAFT)
                assignment = self._assignments.get(mission.mission_id)
                rid = assignment[0] if assignment else None
                dispatched_at = assignment[1] if assignment else None
                if robot_id is not None and rid != robot_id:
                    continue
                result.append(
                    MissionRecord(
                        mission=mission,
                        status=status,
                        robot_id=rid,
                        dispatched_at=dispatched_at,
                        failure_errors=self._failure_errors.get(mission.mission_id),
                    )
                )
        return result

    async def delete(self, mission_id: UUID) -> None:
        with self._lock:
            self._missions.pop(mission_id, None)
            self._status.pop(mission_id, None)
            self._assignments.pop(mission_id, None)
            self._failure_errors.pop(mission_id, None)
            self._drop_stage_states(mission_id)

    def _drop_stage_states(self, mission_id: UUID) -> None:
        self._stage_state = {
            key: record for key, record in self._stage_state.items() if key[0] != mission_id
        }

    # Test-only inspection helpers ------------------------------------------------

    def set_status_directly(self, mission_id: UUID, status: MissionStatus) -> None:
        with self._lock:
            self._status[mission_id] = status
