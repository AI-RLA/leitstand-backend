"""Mission persistence port."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from leitstand_backend.domain.model.mission.coverage import CoverageProvenance
from leitstand_backend.domain.model.mission.mission import Mission, MissionStatus
from leitstand_backend.domain.model.mission.mission_state import MissionError
from leitstand_backend.domain.model.mission.stage_state_record import StageStateRecord


class MissionRecord(BaseModel):
    """Full mission record including backend lifecycle state not in the wire schema."""

    mission: Mission
    status: MissionStatus
    robot_id: str | None
    dispatched_at: datetime | None
    failure_errors: list[MissionError] | None = None
    coverage: CoverageProvenance | None = None


class MissionRepository(ABC):
    @abstractmethod
    async def save(self, mission: Mission) -> Mission: ...

    @abstractmethod
    async def get(self, mission_id: UUID, update_id: int = 0) -> Mission | None: ...

    @abstractmethod
    async def list(self) -> list[Mission]: ...

    @abstractmethod
    async def list_by_robot(self, robot_id: str) -> list[Mission]: ...

    @abstractmethod
    async def list_active_by_robot(self, robot_id: str) -> list[Mission]:
        """Return non-terminal missions assigned to ``robot_id``."""

    @abstractmethod
    async def list_executing_by_robot(self, robot_id: str) -> list[Mission]:
        """Return the robot's currently executing missions (DISPATCHED/RUNNING/PAUSED).

        Narrower than ``list_active_by_robot``: excludes ASSIGNED (assigned but not yet
        dispatched), so a caller acting on a robot going offline touches only missions the
        robot was actually running.
        """

    @abstractmethod
    async def list_active(self) -> list[Mission]:
        """Return all non-terminal missions across the fleet."""

    @abstractmethod
    async def get_status(self, mission_id: UUID) -> MissionStatus: ...

    @abstractmethod
    async def update_status(
        self,
        mission_id: UUID,
        new_status: MissionStatus,
        *,
        errors: list[MissionError] | None = None,
    ) -> Mission | None: ...

    @abstractmethod
    async def set_robot(self, mission_id: UUID, robot_id: str | None) -> None:
        """Store or clear the assigned robot without setting dispatched_at."""

    @abstractmethod
    async def assign_robot(
        self,
        mission_id: UUID,
        robot_id: str,
        dispatched_at: datetime,
    ) -> None: ...

    @abstractmethod
    async def get_assigned_robot(self, mission_id: UUID) -> str | None: ...

    @abstractmethod
    async def save_coverage_provenance(
        self,
        mission_id: UUID,
        provenance: CoverageProvenance,
    ) -> None:
        """Record how a mission's stages were derived.

        Written in the same transaction as the mission it describes, so a mission whose geometry
        nobody authored by hand never exists without the record of what produced it.
        """

    @abstractmethod
    async def upsert_stage_states(
        self,
        mission_id: UUID,
        records: list[StageStateRecord],
    ) -> None:
        """Upsert live per-stage rows, ordered by ``header_id``.

        Keyed ``(mission_id, stage_id)``. A row advances only on a strictly higher
        ``header_id`` (the robot's monotone per-frame counter), so a late or reordered frame
        is ignored. A persistence primitive with no lifecycle knowledge: the caller confirms
        the mission is executing and holds the mission row lock, which serializes the ordering.
        """

    @abstractmethod
    async def overwrite_stage_states(
        self,
        mission_id: UUID,
        records: list[StageStateRecord],
    ) -> None:
        """Overwrite per-stage rows unconditionally as the authoritative final view.

        Written at a terminal transition to persist the resolved per-stage statuses. Applies
        no header ordering, so it never loses to an existing row; the caller holds the mission
        row lock (via ``get_record_for_update``, or an equivalent guarded UPDATE in the same
        transaction) to serialize it against concurrent live writes.
        """

    @abstractmethod
    async def get_stage_states(self, mission_id: UUID) -> list[StageStateRecord]:
        """Return the mission's per-stage rows ordered by ``stage_index``; empty if none."""

    @abstractmethod
    async def get_record(self, mission_id: UUID) -> MissionRecord | None:
        """Return full mission record (content + lifecycle state) or None."""

    @abstractmethod
    async def get_record_for_update(self, mission_id: UUID) -> MissionRecord | None:
        """Return the mission record under a row lock (``SELECT ... FOR UPDATE``), or None.

        Serializes a read-decide-write critical section against concurrent mutators; the
        lock is held until the surrounding transaction commits.
        """

    @abstractmethod
    async def list_records(
        self, *, robot_id: str | None = None, name: str | None = None
    ) -> list[MissionRecord]:
        """Return missions as full records, newest first.

        ``robot_id`` filters to that robot's missions and ``name`` matches case-insensitively;
        each omitted filter widens the result. Both together are an AND.
        """

    @abstractmethod
    async def executing_robot_ids(self) -> set[str]:
        """Return the ids of robots that currently have an executing mission.

        A set rather than the missions themselves, because the fleet view only asks whether a
        robot is busy and a mission carries its whole stage list.
        """

    @abstractmethod
    async def reset_to_draft(self, mission_id: UUID) -> Mission | None:
        """Reset a FAILED or CANCELLED mission to DRAFT, clearing robot_id and dispatched_at.
        Returns None if the mission was not in a resettable state."""

    @abstractmethod
    async def delete(self, mission_id: UUID) -> None:
        """Hard-delete the mission row. Caller must verify status is deletable."""
