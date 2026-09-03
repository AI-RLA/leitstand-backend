"""Domain-level errors. Raised by services + adapters at boundaries;
caught by routers and translated to HTTP status codes."""

from enum import Enum
from uuid import UUID

from leitstand_backend.domain.model.mission.mission import MissionStatus


class DomainError(Exception):
    """Base for all domain-level errors."""


class RobotNotFoundError(DomainError):
    def __init__(self, robot_id: str):
        super().__init__(f"robot {robot_id!r} not found")
        self.robot_id = robot_id


class FieldNotFoundError(DomainError):
    def __init__(self, field_id: UUID):
        super().__init__(f"field {field_id} not found")
        self.field_id = field_id


class FieldNotPlannable(DomainError):
    """The field's geometry has no usable area, so no path can be derived from it."""

    def __init__(self, field_id: UUID):
        super().__init__(f"field {field_id} has no area; its geometry is empty or degenerate")
        self.field_id = field_id


class RobotPhysicalParametersMissing(DomainError):
    """The robot's factsheet declares no width or turning radius, so no path can be planned for it."""

    def __init__(self, robot_id: str):
        super().__init__(
            f"robot {robot_id!r} has not declared its physical parameters, "
            "so a path cannot be planned for it"
        )
        self.robot_id = robot_id


class CoveragePlanRejected(DomainError):
    """A returned coverage plan failed a validity check, so no mission was created."""

    def __init__(self, reason: str):
        super().__init__(f"coverage plan rejected: {reason}")
        self.reason = reason


class CoveragePlannerUnavailable(DomainError):
    """No coverage planner is configured, or none answered."""

    def __init__(self, reason: str | None = None):
        super().__init__(reason or "no coverage planner is configured")
        self.reason = reason


class MissionNotFoundError(DomainError):
    def __init__(self, mission_id: UUID):
        super().__init__(f"mission {mission_id} not found")
        self.mission_id = mission_id


class SiteNotFoundError(DomainError):
    def __init__(self, site_id: UUID):
        super().__init__(f"site {site_id} not found")
        self.site_id = site_id


class SiteInUse(DomainError):
    """Site cannot be deleted because non-terminal missions still reference it."""

    def __init__(self, site_id: UUID, blocking_mission_ids: list[UUID] | None = None):
        super().__init__(f"site {site_id} is referenced by non-terminal missions")
        self.site_id = site_id
        self.blocking_mission_ids = blocking_mission_ids or []


class StageNotHomogeneous(DomainError):
    """Waypoints within one stage mix discriminator kinds (e.g., wgs84 + site_local).

    Identifies the stage by position rather than stage_id: ids are assigned by the backend, so a
    rejected request has no id its caller would recognise.
    """

    def __init__(self, stage_index: int, kinds: set[str]):
        super().__init__(
            f"stage at index {stage_index} mixes waypoint kinds {sorted(kinds)}; "
            "all waypoints in a stage must share kind"
        )
        self.stage_index = stage_index
        self.kinds = kinds


class UnsupportedStageKind(DomainError):
    """Robot's factsheet does not list the stage kind required by the mission."""

    def __init__(self, robot_id: str, stage_id: UUID, kind: str):
        super().__init__(
            f"robot {robot_id!r} does not support stage kind {kind!r} (stage {stage_id})"
        )
        self.robot_id = robot_id
        self.stage_id = stage_id
        self.kind = kind


class UnknownSite(DomainError):
    """A site_local waypoint references a site_id absent from the backend catalog."""

    def __init__(self, site_id: UUID):
        super().__init__(f"site {site_id} does not exist")
        self.site_id = site_id


class UnknownSiteForRobot(DomainError):
    """Robot's factsheet does not list the site_id referenced by a site-local waypoint."""

    def __init__(self, robot_id: str, site_id: UUID):
        super().__init__(f"robot {robot_id!r} does not know site {site_id}")
        self.robot_id = robot_id
        self.site_id = site_id


class UnsupportedWaypointFrame(DomainError):
    """Robot's factsheet does not list the coordinate frame a waypoint uses."""

    def __init__(self, robot_id: str, stage_id: UUID, frame: str):
        super().__init__(
            f"robot {robot_id!r} does not support waypoint frame {frame!r} (stage {stage_id})"
        )
        self.robot_id = robot_id
        self.stage_id = stage_id
        self.frame = frame


class IncompatibleTurningRadius(DomainError):
    """Robot cannot make the turns a generated plan was laid out for."""

    def __init__(self, robot_id: str, robot_radius_m: float, plan_radius_m: float):
        super().__init__(
            f"robot {robot_id!r} turns at {robot_radius_m} m, wider than the {plan_radius_m} m "
            "this plan was laid out for; re-plan it for this robot"
        )
        self.robot_id = robot_id
        self.robot_radius_m = robot_radius_m
        self.plan_radius_m = plan_radius_m


class ImplementNarrowerThanRobot(DomainError):
    """Robot is wider than the implement a generated plan spaced its swaths for."""

    def __init__(self, robot_id: str, track_width_m: float, operation_width_m: float):
        super().__init__(
            f"robot {robot_id!r} has a {track_width_m} m wheel track, wider than the "
            f"{operation_width_m} m implement this plan spaced its swaths for; its wheels would "
            "run over worked ground"
        )
        self.robot_id = robot_id
        self.track_width_m = track_width_m
        self.operation_width_m = operation_width_m


class GeneratedPlanNotEditable(DomainError):
    """Stages of a mission a planner produced cannot be replaced by hand."""

    def __init__(self, mission_id: UUID):
        super().__init__(
            f"mission {mission_id} was planned rather than typed; re-plan it instead of "
            "replacing its stages"
        )
        self.mission_id = mission_id


class StaleCoverageBoundary(DomainError):
    """Field a coverage plan was derived from no longer matches what the plan was made against."""

    def __init__(self, field_id: UUID, reason: str):
        super().__init__(f"field {field_id} {reason} since this plan was made; re-plan it")
        self.field_id = field_id
        self.reason = reason


class RobotBusy(DomainError):
    """Dispatch rejected because the robot already has a non-terminal mission assigned."""

    def __init__(self, robot_id: str, active_mission_id: UUID):
        super().__init__(f"robot {robot_id!r} is already running mission {active_mission_id}")
        self.robot_id = robot_id
        self.active_mission_id = active_mission_id


class RobotFactsheetMissing(DomainError):
    """No factsheet for the target robot, which may not exist at all.

    A factsheet view is the only robot fact the mission path holds, so a robot that has never
    registered and an id that names nothing are indistinguishable from here. The message says
    both rather than the one it happens to be named for.
    """

    def __init__(self, robot_id: str):
        super().__init__(
            f"no factsheet for robot {robot_id!r}, which may not exist or may not have registered"
        )
        self.robot_id = robot_id


class InvalidMissionTransition(DomainError):
    """The (state, trigger) pair is not in the allowed-transitions table."""

    def __init__(self, current: MissionStatus, trigger: str | Enum):
        # This message reaches an audit record, and an enum member's repr would carry its class
        # and value there.
        name = trigger.value if isinstance(trigger, Enum) else trigger
        super().__init__(f"cannot apply {name!r} to mission in state {current}")
        self.current = current
        self.trigger = trigger


class MissionRejectedByRobot(DomainError):
    """Robot's dispatch reply set ``accepted=False``."""

    def __init__(self, mission_id: UUID, robot_id: str, reason: str | None):
        super().__init__(f"robot {robot_id!r} rejected mission {mission_id}: {reason}")
        self.mission_id = mission_id
        self.robot_id = robot_id
        self.reason = reason


class NoRobotAssigned(DomainError):
    """Dispatch called on a mission that has no robot assigned and none was provided."""

    def __init__(self, mission_id: UUID):
        super().__init__(
            f"mission {mission_id} has no robot assigned; assign a robot first or provide robot_id"
        )
        self.mission_id = mission_id


class MissionDispatchTimeout(DomainError):
    """Robot did not reply to the dispatch queryable within the timeout."""

    def __init__(self, mission_id: UUID, robot_id: str):
        super().__init__(f"robot {robot_id!r} did not acknowledge mission {mission_id}")
        self.mission_id = mission_id
        self.robot_id = robot_id
