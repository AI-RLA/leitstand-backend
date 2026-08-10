"""Unit tests for the factsheet ACL mapper."""

from __future__ import annotations

import pytest
from leitstand.robot.v1 import factsheet_pb2

from leitstand_backend.adapters.inbound.messaging.zenoh.robot.factsheet_mappers import (
    _WAYPOINT_KIND_FROM_PROTO,
    factsheet_from_proto,
)
from leitstand_backend.domain.model.robot.robot_factsheet import WaypointKind

ROBOT_ID = "scout-mini-04"


def _proto(*frames: int) -> factsheet_pb2.Factsheet:
    return factsheet_pb2.Factsheet(
        stage_capabilities=[
            factsheet_pb2.StageCapability(
                navigation=factsheet_pb2.NavigationCapability(supported_waypoint_kinds=list(frames))
            )
        ]
    )


def test_maps_navigation_with_frames_and_stamps_robot_id() -> None:
    proto = _proto(factsheet_pb2.WAYPOINT_KIND_WGS84, factsheet_pb2.WAYPOINT_KIND_SITE_LOCAL)
    fs = factsheet_from_proto(proto, ROBOT_ID)
    assert fs.robot_id == ROBOT_ID
    assert fs.navigation is not None
    assert fs.navigation.supported_waypoint_kinds == [WaypointKind.WGS84, WaypointKind.SITE_LOCAL]


def test_no_navigation_capability_maps_to_none() -> None:
    assert factsheet_from_proto(factsheet_pb2.Factsheet(), ROBOT_ID).navigation is None


def test_empty_stage_capability_entry_is_skipped() -> None:
    proto = factsheet_pb2.Factsheet(stage_capabilities=[factsheet_pb2.StageCapability()])
    assert factsheet_from_proto(proto, ROBOT_ID).navigation is None


def test_duplicate_navigation_is_rejected() -> None:
    proto = factsheet_pb2.Factsheet(
        stage_capabilities=[
            factsheet_pb2.StageCapability(navigation=factsheet_pb2.NavigationCapability()),
            factsheet_pb2.StageCapability(navigation=factsheet_pb2.NavigationCapability()),
        ]
    )
    with pytest.raises(ValueError, match="more than once"):
        factsheet_from_proto(proto, ROBOT_ID)


def test_waypoint_kind_table_is_exhaustive_against_the_descriptor() -> None:
    # A new WaypointKind value must extend the mapper table and this set together.
    declared = {v.number for v in factsheet_pb2.WaypointKind.DESCRIPTOR.values if v.number != 0}
    assert set(_WAYPOINT_KIND_FROM_PROTO) == declared
