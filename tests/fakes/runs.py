"""Builder for a MissionRun in whatever state a test needs it, so a field added to ``MissionRun``
is set in one place."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from leitstand_backend.domain.model.mission.mission import NavigationStage, Stage
from leitstand_backend.domain.model.mission.mission_run import MissionRun, RunOrigin
from leitstand_backend.domain.model.mission.run_status import RunStatus
from leitstand_backend.domain.model.mission.stages_digest import stages_digest
from leitstand_backend.domain.model.mission.waypoint import WGS84Waypoint

RUN_AT = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def mission_run(
    *,
    robot_id: str = "robot-1",
    status: RunStatus = RunStatus.RUNNING,
    run_id: UUID | None = None,
    mission_id: UUID | None = None,
    stages: list[Stage] | None = None,
    when: datetime = RUN_AT,
) -> MissionRun:
    """A run as it would have been frozen at dispatch, with a one-stage plan by default."""
    frozen = (
        stages
        if stages is not None
        else [NavigationStage(stage_id=uuid4(), waypoints=[WGS84Waypoint(lat=52.3, lon=8.05)])]
    )
    return MissionRun(
        run_id=run_id or uuid4(),
        mission_id=mission_id or uuid4(),
        robot_id=robot_id,
        status=status,
        stages=frozen,
        stages_digest=stages_digest(frozen),
        origin=RunOrigin(kind="manual", actor="tester"),
        created_at=when,
        updated_at=when,
    )
