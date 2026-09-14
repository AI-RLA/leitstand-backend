"""Mission run persistence port: one execution of a mission and its per-stage state."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from leitstand_backend.domain.model.mission.mission_run import (
    LastReport,
    MissionRun,
    MissionRunSummary,
)
from leitstand_backend.domain.model.mission.mission_state import MissionError
from leitstand_backend.domain.model.mission.run_lifecycle import RunTrigger
from leitstand_backend.domain.model.mission.run_status import RunStatus
from leitstand_backend.domain.model.mission.run_transition import RunTransition
from leitstand_backend.domain.model.mission.stage_state_record import StageStateRecord


class MissionRunRepository(ABC):
    @abstractmethod
    async def create(self, run: MissionRun) -> MissionRun: ...

    @abstractmethod
    async def get(self, run_id: UUID) -> MissionRun | None: ...

    @abstractmethod
    async def get_for_update(self, run_id: UUID) -> MissionRun | None:
        """Return the run under a row lock, with its transitions, or None; held until commit."""

    @abstractmethod
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
        """Compare-and-set the status and append the transition row; None unless ``expected`` holds.

        A transition is computed from a status the caller read; writing it only if that status
        still holds is what keeps a late dispatch reply from regressing a run the robot's own
        frame has already moved on. A same-state write (``new_status == expected``) changes
        nothing but still appends its row. ``started_at`` is stamped on first entering RUNNING
        and ``ended_at`` on entering a terminal state, both by the backend clock. The returned
        run carries no transitions; ``get`` and ``get_for_update`` load them.
        """

    @abstractmethod
    async def set_acknowledged(
        self, run_id: UUID, acknowledged: bool, detail: dict | None = None
    ) -> None:
        """Record the robot's receipt on the run's latest transition row, merging ``detail``."""

    @abstractmethod
    async def list_transitions(self, run_id: UUID) -> list[RunTransition]:
        """Return the run's transition rows in the order they were written."""

    @abstractmethod
    async def mark_dispatched_at(self, run_id: UUID, when: datetime) -> None:
        """Stamp ``dispatched_at`` if it is still unset.

        The robot's own frame may have moved the run on before the dispatch reply was recorded,
        and that path never writes it.
        """

    @abstractmethod
    async def set_notes(self, run_id: UUID, notes: str | None) -> MissionRun | None: ...

    @abstractmethod
    async def touch_last_report(self, run_id: UUID, report: LastReport) -> None:
        """Record the most recent state report the robot sent for the run."""

    @abstractmethod
    async def list_by_mission(
        self, mission_id: UUID, *, limit: int = 50, before: datetime | None = None
    ) -> list[MissionRunSummary]:
        """Return the mission's runs newest first, without their plans."""

    @abstractmethod
    async def list_active_by_mission(self, mission_id: UUID) -> list[MissionRunSummary]: ...

    @abstractmethod
    async def list_active_by_robot(self, robot_id: str) -> list[MissionRunSummary]:
        """Return the runs occupying the robot: every active state."""

    @abstractmethod
    async def has_executing_run(self, robot_id: str) -> bool:
        """Return True while this robot has, or may have, a goal.

        A boolean rather than the runs themselves: this is asked for every robot on a periodic
        sweep, and a run carries a frozen plan of thousands of waypoints.
        """

    @abstractmethod
    async def list_active(self) -> list[MissionRunSummary]:
        """Return every active run across the fleet in one query, for the fleet view."""

    @abstractmethod
    async def latest_by_mission(self, mission_ids: list[UUID]) -> dict[UUID, MissionRunSummary]:
        """Return the most recently created run of each mission, in one query."""

    @abstractmethod
    async def count_by_mission(self, mission_id: UUID) -> int: ...

    @abstractmethod
    async def upsert_stage_runs(self, run_id: UUID, records: list[StageStateRecord]) -> None:
        """Upsert live per-stage rows, ordered by ``header_id``.

        A row advances only on a strictly higher ``header_id`` (the robot's monotone per-frame
        counter), so a late or reordered frame is ignored. The caller holds the run's row lock.
        """

    @abstractmethod
    async def overwrite_stage_runs(self, run_id: UUID, records: list[StageStateRecord]) -> None:
        """Overwrite per-stage rows unconditionally as the resolved final view."""

    @abstractmethod
    async def get_stage_runs(self, run_id: UUID) -> list[StageStateRecord]:
        """Return the run's per-stage rows ordered by ``stage_index``; empty if none."""

    @abstractmethod
    async def delete(self, run_id: UUID) -> None:
        """Remove the run and its stage rows. The caller has checked it is not active.

        A table referencing the run with ``ON DELETE RESTRICT`` refuses the delete here.
        """
