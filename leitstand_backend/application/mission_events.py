"""Mission lifecycle event: publish the status-change notification."""

from uuid import UUID

from leitstand_backend.domain import event_topics
from leitstand_backend.domain.model.mission.mission import MissionStatus
from leitstand_backend.domain.model.mission.mission_lifecycle import MissionTrigger
from leitstand_backend.ports.outbound.event_publisher import EventPublisher


def emit_mission_lifecycle(
    events: EventPublisher,
    mission_id: UUID,
    status: MissionStatus,
    trigger: MissionTrigger,
    reason: str | None = None,
) -> None:
    """Publish a mission lifecycle transition notification.

    One fire-once notification per status change (``trigger`` names the transition),
    distinct from the latched ``state`` telemetry frame. The latched ``state`` frame is
    owned by the state producers, which republish the resolved view on a terminal change.
    """
    payload: dict[str, str] = {
        "mission_id": str(mission_id),
        "status": status.value,
        "trigger": trigger.value,
    }
    if reason is not None:
        payload["reason"] = reason
    events.publish(event_topics.mission_topic(mission_id, "lifecycle"), payload, latch=False)
