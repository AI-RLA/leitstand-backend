"""Robot operational status: the status vocabulary and the rule that derives it.

Inputs are plain bools, so the rule does not depend on the mission model.
"""

from enum import Enum


class RobotStatus(str, Enum):
    """Displayed operational status of a robot."""

    OFFLINE = "offline"
    CHARGING = "charging"
    ACTIVE = "active"
    IDLE = "idle"


def derive_robot_status(
    *,
    online: bool,
    has_active_mission: bool,
    charging: bool,
) -> RobotStatus:
    """Return the displayed status, resolved by priority offline > charging > active > idle.

    The priority is a product decision: a charging robot reads as unavailable even if it
    also holds a mission, and an offline robot overrides everything.
    """
    if not online:
        return RobotStatus.OFFLINE
    if charging:
        return RobotStatus.CHARGING
    if has_active_mission:
        return RobotStatus.ACTIVE
    return RobotStatus.IDLE
