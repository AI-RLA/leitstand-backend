"""Errors raised when a rule refuses an action or a dependency did not answer.

The caller decides how to report each one.
"""

from enum import Enum
from uuid import UUID

from leitstand_backend.domain.model.mission.run_status import RunStatus


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
    """Site cannot be deleted because missions, archived ones included, still reference it.

    The site's anchor is what gives every site-local position in those missions' history its
    meaning, so a site stays as long as any mission that used it.
    """

    def __init__(self, site_id: UUID, blocking_mission_ids: list[UUID] | None = None):
        super().__init__(f"site {site_id} is referenced by missions")
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
    """The (state, trigger) pair is not in the run's allowed-transitions table."""

    def __init__(self, current: RunStatus, trigger: str | Enum):
        # This message reaches an audit record, and an enum member's repr would carry its class
        # and value there.
        name = trigger.value if isinstance(trigger, Enum) else trigger
        super().__init__(f"cannot apply {name!r} to run in state {current.value}")
        self.current = current
        self.trigger = trigger


class MissionRunInProgress(DomainError):
    """A run of this mission is already active and the dispatch did not ask to run alongside it."""

    def __init__(self, mission_id: UUID, active_run_ids: list[UUID]):
        super().__init__(
            f"mission {mission_id} already has an active run; wait for it to end or cancel it"
        )
        self.mission_id = mission_id
        self.active_run_ids = active_run_ids


class AmbiguousRun(DomainError):
    """More than one run of this mission is active and the request did not say which."""

    def __init__(self, mission_id: UUID, active_run_ids: list[UUID]):
        super().__init__(
            f"mission {mission_id} has {len(active_run_ids)} active runs; pass run_id to choose one"
        )
        self.mission_id = mission_id
        self.active_run_ids = active_run_ids


class RunNotFoundError(DomainError):
    def __init__(self, run_id: UUID, *, mission_has_none: bool = False):
        if mission_has_none:
            super().__init__(f"mission {run_id} has no run to act on")
        else:
            super().__init__(f"run {run_id} not found")
        self.run_id = run_id


class MissionArchived(DomainError):
    """The mission is archived: it can be read, but not changed until restored."""

    def __init__(self, mission_id: UUID):
        super().__init__(f"mission {mission_id} is archived")
        self.mission_id = mission_id


class MissionNotArchived(DomainError):
    def __init__(self, mission_id: UUID):
        super().__init__(f"mission {mission_id} is not archived")
        self.mission_id = mission_id


class DuplicateStageId(DomainError):
    """One request names the same stage_id twice."""

    def __init__(self, stage_id: UUID):
        super().__init__(f"stage {stage_id} appears more than once in the request")
        self.stage_id = stage_id


class StageNotInMission(DomainError):
    """A supplied stage_id does not belong to the mission being edited, so cannot be kept."""

    def __init__(self, mission_id: UUID | None, stage_id: UUID):
        target = f"mission {mission_id}" if mission_id is not None else "a new mission"
        super().__init__(
            f"stage {stage_id} is not a stage of {target}; omit stage_id for a new stage"
        )
        self.mission_id = mission_id
        self.stage_id = stage_id


class StageSpansSites(DomainError):
    """Waypoints within one site-local stage name more than one site.

    A stage is driven in one map frame; crossing into another site's frame is a stage boundary.
    """

    def __init__(self, stage_index: int, site_ids: set[UUID]):
        super().__init__(
            f"stage at index {stage_index} spans {len(site_ids)} sites; a stage uses one site"
        )
        self.stage_index = stage_index
        self.site_ids = site_ids


class MissionRejectedByRobot(DomainError):
    """Robot's dispatch reply set ``accepted=False``."""

    def __init__(self, run_id: UUID, robot_id: str, reason: str | None):
        super().__init__(f"robot {robot_id!r} rejected run {run_id}: {reason}")
        self.run_id = run_id
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

    def __init__(self, run_id: UUID, robot_id: str):
        super().__init__(f"robot {robot_id!r} did not acknowledge run {run_id}")
        self.run_id = run_id
        self.robot_id = robot_id


class MissionDispatchFailed(DomainError):
    """The dispatch could not be delivered, so the robot never had a chance to answer."""

    def __init__(self, run_id: UUID, robot_id: str, reason: str):
        super().__init__(f"could not send run {run_id} to robot {robot_id!r}: {reason}")
        self.run_id = run_id
        self.robot_id = robot_id
        self.reason = reason
