"""RobotStateView driven port: latest pose / battery per robot."""

from abc import ABC, abstractmethod

from leitstand_backend.domain.model.robot.telemetry import Battery, Pose


class RobotStateView(ABC):
    """Latest known telemetry per robot.

    Domain port for read-side access to the latest reported telemetry
    values. Implementation may use any cache, store, or
    last-known-state source.
    """

    @abstractmethod
    def latest_pose(self, robot_id: str) -> Pose | None: ...

    @abstractmethod
    def latest_battery(self, robot_id: str) -> Battery | None: ...
