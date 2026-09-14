"""In-memory MissionRepository fake (test-only)."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from uuid import UUID

from leitstand_backend.domain.model.mission.mission import (
    Mission,
    Stage,
    referenced_site_ids,
    stage_ids,
)
from leitstand_backend.ports.outbound.mission_repository import MissionRepository


class InMemoryMissionRepository(MissionRepository):
    def __init__(self) -> None:
        self._missions: dict[UUID, Mission] = {}
        self._lock = threading.Lock()

    async def save(self, mission: Mission) -> Mission:
        with self._lock:
            self._missions[mission.mission_id] = mission
        return mission

    async def get(self, mission_id: UUID) -> Mission | None:
        with self._lock:
            return self._missions.get(mission_id)

    async def list(
        self,
        *,
        robot_id: str | None = None,
        name: str | None = None,
        include_archived: bool = False,
    ) -> list[Mission]:
        with self._lock:
            result = []
            for mission in sorted(
                self._missions.values(), key=lambda m: m.created_at, reverse=True
            ):
                if not include_archived and mission.archived_at is not None:
                    continue
                if robot_id is not None and mission.assigned_robot_id != robot_id:
                    continue
                if name is not None and mission.name.lower() != name.lower():
                    continue
                result.append(mission)
        return result

    async def replace_stages(self, mission_id: UUID, stages: list[Stage]) -> None:
        with self._lock:
            mission = self._missions[mission_id]
            self._missions[mission_id] = mission.model_copy(
                update={"stages": stages, "updated_at": datetime.now(timezone.utc)}
            )

    async def get_stages(self, mission_id: UUID) -> list[Stage]:
        with self._lock:
            return list(self._missions[mission_id].stages)

    async def set_assigned_robot(self, mission_id: UUID, robot_id: str | None) -> None:
        with self._lock:
            mission = self._missions[mission_id]
            self._missions[mission_id] = mission.model_copy(update={"assigned_robot_id": robot_id})

    async def archive(self, mission_id: UUID) -> Mission | None:
        return self._set_archived(mission_id, datetime.now(timezone.utc))

    async def restore(self, mission_id: UUID) -> Mission | None:
        return self._set_archived(mission_id, None)

    def _set_archived(self, mission_id: UUID, when: datetime | None) -> Mission | None:
        with self._lock:
            mission = self._missions.get(mission_id)
            if mission is None:
                return None
            updated = mission.model_copy(update={"archived_at": when})
            self._missions[mission_id] = updated
            return updated

    async def get_for_update(self, mission_id: UUID) -> Mission | None:
        # No real row lock in the in-memory fake; tests drive calls sequentially.
        return await self.get(mission_id)

    async def mission_id_for_stage(self, stage_id: UUID) -> UUID | None:
        with self._lock:
            for mission in self._missions.values():
                if stage_id in stage_ids(mission.stages):
                    return mission.mission_id
        return None

    async def missions_referencing_site(self, site_id: UUID) -> list[UUID]:
        with self._lock:
            return [
                mission.mission_id
                for mission in self._missions.values()
                if site_id in referenced_site_ids(mission.stages)
            ]

    async def delete(self, mission_id: UUID) -> None:
        with self._lock:
            self._missions.pop(mission_id, None)
