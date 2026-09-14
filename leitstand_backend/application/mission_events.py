"""Run lifecycle event: publish the status-change notification."""

from uuid import UUID

from leitstand_backend.domain.model.mission.run_lifecycle import RunTrigger
from leitstand_backend.domain.model.mission.run_status import RunStatus
from leitstand_backend.ports.outbound.event_publisher import EventPublisher, mission_topic


def emit_run_lifecycle(
    events: EventPublisher,
    mission_id: UUID,
    run_id: UUID,
    robot_id: str,
    status: RunStatus,
    trigger: RunTrigger,
    reason: str | None = None,
) -> None:
    """Publish a run lifecycle transition on the mission's topic.

    One fire-once notification per status change (``trigger`` names the transition), distinct
    from the latched ``state`` telemetry frame. The topic stays keyed by mission because that is
    what subscribers know; the payload names the run and the robot, so a consumer knows the robot
    without a lookup.
    """
    payload: dict[str, str] = {
        "mission_id": str(mission_id),
        "run_id": str(run_id),
        "robot_id": robot_id,
        "status": status.value,
        "trigger": trigger.value,
    }
    if reason is not None:
        payload["reason"] = reason
    events.publish(mission_topic(mission_id, "lifecycle"), payload, latch=False)
