"""Mission definition persistence port."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from leitstand_backend.domain.model.mission.mission import Mission, Stage


class MissionRepository(ABC):
    @abstractmethod
    async def save(self, mission: Mission) -> Mission:
        """Insert or update the definition, stages included."""

    @abstractmethod
    async def get(self, mission_id: UUID) -> Mission | None: ...

    @abstractmethod
    async def list(
        self,
        *,
        robot_id: str | None = None,
        name: str | None = None,
        include_archived: bool = False,
    ) -> list[Mission]:
        """Return missions, newest first.

        ``robot_id`` filters to that robot's assigned missions and ``name`` matches
        case-insensitively; each omitted filter widens the result.
        """

    @abstractmethod
    async def replace_stages(self, mission_id: UUID, stages: list[Stage]) -> None:
        """Make ``stages`` the mission's stages, keeping the rows whose ids are kept."""

    @abstractmethod
    async def get_stages(self, mission_id: UUID) -> list[Stage]: ...

    @abstractmethod
    async def set_assigned_robot(self, mission_id: UUID, robot_id: str | None) -> None:
        """Store or clear the default robot a dispatch goes to when it names none."""

    @abstractmethod
    async def archive(self, mission_id: UUID) -> Mission | None:
        """Set ``archived_at``; None when the mission does not exist."""

    @abstractmethod
    async def restore(self, mission_id: UUID) -> Mission | None:
        """Clear ``archived_at``; None when the mission does not exist."""

    @abstractmethod
    async def get_for_update(self, mission_id: UUID) -> Mission | None:
        """Return the mission under a row lock (``SELECT ... FOR UPDATE``), or None.

        Serializes a read-decide-write critical section against concurrent mutators; the lock
        is held until the surrounding transaction commits. Every dispatch of a mission takes it,
        which is what makes the default refusal of a second concurrent run race-free.
        """

    @abstractmethod
    async def mission_id_for_stage(self, stage_id: UUID) -> UUID | None:
        """Return the mission a stage belongs to, or None if no such stage exists."""

    @abstractmethod
    async def missions_referencing_site(self, site_id: UUID) -> list[UUID]:
        """Return every mission, archived included, with a stage at ``site_id``.

        The site's anchor gives those missions' site-local history its meaning, so an archived
        mission keeps a site referenced as a live one does.
        """

    @abstractmethod
    async def delete(self, mission_id: UUID) -> None:
        """Hard-delete the definition and its stages. The caller has checked it has no runs."""
