"""Unit tests for derive_robot_status (the operational-status rule)."""

from __future__ import annotations

import pytest

from leitstand_backend.domain.model.robot.robot_status import RobotStatus, derive_robot_status


@pytest.mark.parametrize(
    ("online", "has_active_mission", "charging", "expected"),
    [
        (False, False, False, RobotStatus.OFFLINE),
        (False, True, True, RobotStatus.OFFLINE),  # offline wins over everything
        (True, True, True, RobotStatus.CHARGING),  # charging wins over active
        (True, True, False, RobotStatus.ACTIVE),
        (True, False, False, RobotStatus.IDLE),
        (True, False, True, RobotStatus.CHARGING),
    ],
)
def test_derive_robot_status_priority(
    online: bool, has_active_mission: bool, charging: bool, expected: RobotStatus
) -> None:
    assert (
        derive_robot_status(online=online, has_active_mission=has_active_mission, charging=charging)
        is expected
    )


def test_status_value_is_lowercase_wire_token() -> None:
    assert RobotStatus.ACTIVE.value == "active"
    assert RobotStatus.ACTIVE == "active"  # str-Enum equality holds on the wire
