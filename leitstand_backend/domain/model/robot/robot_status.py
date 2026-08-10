"""Robot operational status: the status vocabulary and its derivation rule.

``RobotStatus`` is the displayed status; ``derive_robot_status`` composes it from facts
the backend owns -- liveliness, the mission relationship, and battery. It is a derived
rule, so it lives beside the other pure domain rules (cf. ``mission_lifecycle``) rather
than with the robot-reported payloads in ``telemetry``. Inputs are plain bools to keep
the rule decoupled from the mission domain.
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
