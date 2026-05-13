"""RobotStateView driven port: latest pose / battery / state per robot."""

from abc import ABC, abstractmethod

from leitstand_backend.domain.model.telemetry import Battery, Pose, RobotState


class RobotStateView(ABC):
    """Latest known telemetry per robot.

    Domain port for read-side access to the latest reported state
    values. Implementation may use any cache, store, or
    last-known-state source.
    """

    @abstractmethod
    def latest_pose(self, robot_id: str) -> Pose | None: ...

    @abstractmethod
    def latest_battery(self, robot_id: str) -> Battery | None: ...

    @abstractmethod
    def latest_state(self, robot_id: str) -> RobotState | None: ...
