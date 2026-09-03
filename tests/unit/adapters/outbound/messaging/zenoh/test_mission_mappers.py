"""Unit tests for the dispatch-direction ACL mappers.

The exhaustiveness tests pin the proto descriptors: adding a stage kind,
waypoint arm, or cancel mode to the contract must fail here until the mappers
handle it, so a new variant can never silently drop.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from leitstand.robot.v1 import mission_pb2

from leitstand_backend.adapters.outbound.messaging.zenoh.mission.mission_mappers import (
    cancel_to_proto,
    dispatch_request_to_proto,
    dispatch_response_from_proto,
    mission_projection_from_proto,
    mission_to_proto,
)
from leitstand_backend.domain.model.mission.mission import (
    CoverageStage,
    Mission,
    NavigationStage,
    Segment,
)
from leitstand_backend.domain.model.mission.mission_dispatch import CancelMode
from leitstand_backend.domain.model.mission.waypoint import SiteLocalWaypoint, WGS84Waypoint

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _mission(*stages: NavigationStage) -> Mission:
    return Mission(
        mission_id=uuid4(),
        name="mapper-test",
        stages=list(stages),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _proto_mission(*stages: mission_pb2.Stage) -> mission_pb2.Mission:
    return mission_pb2.Mission(mission_id=str(uuid4()), stages=list(stages))


def test_projection_roundtrip_with_both_waypoint_kinds_and_on_cancel() -> None:
    stage = NavigationStage(
        stage_id=uuid4(),
        waypoints=[
            WGS84Waypoint(lat=52.5, lon=13.4, heading_deg=90.0),
            SiteLocalWaypoint(site_id=uuid4(), x=1.0, y=2.0, theta=0.5),
        ],
        on_cancel=[NavigationStage(stage_id=uuid4(), waypoints=[WGS84Waypoint(lat=1.0, lon=1.0)])],
    )
    mission = _mission(stage)
    mission_id, stages = mission_projection_from_proto(mission_to_proto(mission))
    assert mission_id == mission.mission_id
    assert stages == mission.stages


def test_absent_heading_and_theta_stay_absent_on_the_wire() -> None:
    proto = mission_to_proto(
        _mission(
            NavigationStage(
                stage_id=uuid4(),
                waypoints=[
                    WGS84Waypoint(lat=0.0, lon=0.0),
                    SiteLocalWaypoint(site_id=uuid4(), x=0.0, y=0.0),
                ],
            )
        )
    )
    wgs84, site_local = proto.stages[0].navigation.waypoints
    assert not wgs84.wgs84.HasField("heading_deg")
    assert not site_local.site_local.HasField("theta")


def test_empty_on_cancel_roundtrips_to_none() -> None:
    mission = _mission(
        NavigationStage(stage_id=uuid4(), waypoints=[WGS84Waypoint(lat=0.0, lon=0.0)])
    )
    _, stages = mission_projection_from_proto(mission_to_proto(mission))
    assert stages[0].on_cancel is None


def test_dispatch_request_carries_dispatch_id_and_projection() -> None:
    mission = _mission(
        NavigationStage(stage_id=uuid4(), waypoints=[WGS84Waypoint(lat=0.0, lon=0.0)])
    )
    request = dispatch_request_to_proto(mission, "dispatch-1")
    assert request.dispatch_id == "dispatch-1"
    assert request.mission.mission_id == str(mission.mission_id)


def test_dispatch_response_reason_absent_maps_to_none() -> None:
    assert dispatch_response_from_proto(mission_pb2.MissionDispatchResponse(accepted=True)) == (
        True,
        None,
    )
    assert dispatch_response_from_proto(
        mission_pb2.MissionDispatchResponse(accepted=False, reason="busy")
    ) == (False, "busy")


def test_unknown_stage_kind_is_rejected() -> None:
    stage = mission_pb2.Stage(
        stage_id=str(uuid4()),
        kind=mission_pb2.STAGE_KIND_UNSPECIFIED,
        navigation=mission_pb2.NavigationStage(),
    )
    with pytest.raises(ValueError, match="unsupported stage kind"):
        mission_projection_from_proto(_proto_mission(stage))


def test_kind_payload_mismatch_is_rejected() -> None:
    stage = mission_pb2.Stage(stage_id=str(uuid4()), kind=mission_pb2.STAGE_KIND_NAVIGATION)
    with pytest.raises(ValueError, match="does not match payload arm"):
        mission_projection_from_proto(_proto_mission(stage))


def test_waypoint_without_frame_arm_is_rejected() -> None:
    stage = mission_pb2.Stage(
        stage_id=str(uuid4()),
        kind=mission_pb2.STAGE_KIND_NAVIGATION,
        navigation=mission_pb2.NavigationStage(waypoints=[mission_pb2.Waypoint()]),
    )
    with pytest.raises(ValueError, match="no known frame arm"):
        mission_projection_from_proto(_proto_mission(stage))


def test_cancel_modes_cover_every_declared_proto_mode() -> None:
    # A new CancelMode value must extend the mapper and the domain enum together.
    mission_id = uuid4()
    mapped = {cancel_to_proto(mission_id, mode).mode for mode in CancelMode}
    declared = {
        v.number
        for v in mission_pb2.CancelMode.DESCRIPTOR.values
        if v.number != mission_pb2.CANCEL_MODE_UNSPECIFIED
    }
    assert mapped == declared
    assert cancel_to_proto(mission_id, CancelMode.GRACEFUL).mission_id == str(mission_id)


def test_stage_kind_descriptor_is_exhaustively_handled() -> None:
    # A new StageKind value must extend the mappers and this set together.
    assert {v.number for v in mission_pb2.StageKind.DESCRIPTOR.values} == {
        mission_pb2.STAGE_KIND_UNSPECIFIED,
        mission_pb2.STAGE_KIND_NAVIGATION,
        mission_pb2.STAGE_KIND_COVERAGE,
    }


def test_waypoint_oneof_arms_are_exhaustively_handled() -> None:
    arms = {f.name for f in mission_pb2.Waypoint.DESCRIPTOR.oneofs_by_name["kind"].fields}
    assert arms == {"wgs84", "site_local"}


def test_a_coverage_stage_survives_the_round_trip() -> None:
    """Which segments are swaths is the payload's meaning, so losing it would change the work.

    A route stripped of its kinds visits the same points and covers different ground: nothing
    would say where the machine must hold a line and where it is only repositioning.
    """
    stage = CoverageStage(
        stage_id=uuid4(),
        segments=[
            Segment(
                kind="swath",
                waypoints=[
                    WGS84Waypoint(lat=52.0, lon=8.0, heading_deg=0.0),
                    WGS84Waypoint(lat=52.001, lon=8.0),
                ],
            ),
            Segment(
                kind="turn",
                waypoints=[
                    WGS84Waypoint(lat=52.001, lon=8.0),
                    WGS84Waypoint(lat=52.001, lon=8.0001, heading_deg=90.0),
                ],
            ),
            Segment(
                kind="swath",
                waypoints=[
                    WGS84Waypoint(lat=52.001, lon=8.0001),
                    WGS84Waypoint(lat=52.0, lon=8.0001),
                ],
            ),
        ],
    )
    mission = Mission(
        mission_id=uuid4(),
        name="cover",
        stages=[stage],
        created_at=_NOW,
        updated_at=_NOW,
    )

    _, stages = mission_projection_from_proto(mission_to_proto(mission))

    assert stages == [stage]


def test_a_coverage_payload_tagged_as_navigation_is_rejected() -> None:
    """Kind and payload must agree, or a receiver would drive the wrong thing entirely."""
    proto = mission_pb2.Stage(
        stage_id=str(uuid4()),
        kind=mission_pb2.STAGE_KIND_NAVIGATION,
        coverage=mission_pb2.CoverageStage(
            segments=[
                mission_pb2.Segment(
                    kind=mission_pb2.SEGMENT_KIND_SWATH,
                    geometry=[
                        mission_pb2.Waypoint(wgs84=mission_pb2.WGS84Waypoint(lat=52.0, lon=8.0)),
                        mission_pb2.Waypoint(wgs84=mission_pb2.WGS84Waypoint(lat=52.1, lon=8.0)),
                    ],
                )
            ],
        ),
    )

    with pytest.raises(ValueError, match="does not match payload arm"):
        mission_projection_from_proto(mission_pb2.Mission(mission_id=str(uuid4()), stages=[proto]))
