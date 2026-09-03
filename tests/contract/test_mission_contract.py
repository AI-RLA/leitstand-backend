"""Contract test: keep the in-house mission pydantic and the robot proto aligned.

Shape-agnostic by design. The proto-JSON shape (oneof member keys, enum names,
RFC3339 timestamps) deliberately differs from the pydantic/OpenAPI shape, so
this is not a schema diff. A canonical corpus of domain objects must round-trip
losslessly through each projection, and a negative corpus of malformed
proto-JSON must be rejected by the strict parser.

The proto wire carries only the mission projection ({mission_id, stages});
backend bookkeeping (name, timestamps) never crosses it, so the ACL round-trip
asserts projection equality, never full-Mission equality.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import protovalidate
import pytest
from google.protobuf import json_format
from leitstand.robot.v1 import mission_pb2

from leitstand_backend.adapters.outbound.messaging.zenoh.mission.mission_mappers import (
    mission_projection_from_proto,
    mission_to_proto,
)
from leitstand_backend.domain.model.mission.mission import Mission, NavigationStage
from leitstand_backend.domain.model.mission.waypoint import SiteLocalWaypoint, WGS84Waypoint

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _mission(*stages: NavigationStage) -> Mission:
    return Mission(
        mission_id=uuid4(),
        name="contract-corpus",
        stages=list(stages),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _examples() -> list[Mission]:
    """Cover each waypoint kind, recursive on_cancel, and minimal edges."""
    wgs84 = NavigationStage(stage_id=uuid4(), waypoints=[WGS84Waypoint(lat=52.5, lon=13.4)])
    headed = NavigationStage(
        stage_id=uuid4(),
        waypoints=[WGS84Waypoint(lat=52.5, lon=13.4, heading_deg=90.0)],
    )
    site_local = NavigationStage(
        stage_id=uuid4(),
        waypoints=[SiteLocalWaypoint(site_id=uuid4(), x=1.0, y=2.0, theta=0.5)],
    )
    recursive = NavigationStage(
        stage_id=uuid4(),
        waypoints=[WGS84Waypoint(lat=0.0, lon=0.0)],
        on_cancel=[NavigationStage(stage_id=uuid4(), waypoints=[WGS84Waypoint(lat=1.0, lon=1.0)])],
    )
    mixed = NavigationStage(
        stage_id=uuid4(),
        waypoints=[
            WGS84Waypoint(lat=52.5, lon=13.4),
            SiteLocalWaypoint(site_id=uuid4(), x=3.0, y=4.0),
        ],
    )
    heading_bounds = NavigationStage(
        stage_id=uuid4(),
        waypoints=[
            WGS84Waypoint(lat=52.5, lon=13.4, heading_deg=0.0),
            WGS84Waypoint(lat=52.5, lon=13.4, heading_deg=270.0),
            WGS84Waypoint(lat=52.5, lon=13.4, heading_deg=359.9),
        ],
    )
    extremes = NavigationStage(
        stage_id=uuid4(),
        waypoints=[
            WGS84Waypoint(lat=90.0, lon=180.0),
            WGS84Waypoint(lat=-90.0, lon=-180.0),
        ],
    )
    return [
        _mission(wgs84),
        _mission(headed),
        _mission(site_local),
        _mission(recursive),
        _mission(mixed),
        _mission(wgs84, site_local),
        _mission(heading_bounds),
        _mission(extremes),
    ]


@pytest.mark.parametrize("mission", _examples())
def test_pydantic_roundtrip(mission: Mission) -> None:
    """domain -> pydantic-JSON -> domain yields an equal object."""
    assert Mission.model_validate_json(mission.model_dump_json()) == mission


@pytest.mark.parametrize("mission", _examples())
def test_roundtrip_via_acl(mission: Mission) -> None:
    """domain -> proto -> domain (through the named-field ACL) is projection-lossless."""
    mission_id, stages = mission_projection_from_proto(mission_to_proto(mission))
    assert mission_id == mission.mission_id
    assert stages == mission.stages


@pytest.mark.parametrize("mission", _examples())
def test_proto_json_roundtrip(mission: Mission) -> None:
    """domain -> proto -> proto-JSON -> proto -> domain survives the actual wire format."""
    proto = mission_to_proto(mission)
    wire = json_format.MessageToJson(proto, preserving_proto_field_name=True)
    parsed = json_format.Parse(wire, mission_pb2.Mission(), ignore_unknown_fields=False)
    assert mission_projection_from_proto(parsed) == (mission.mission_id, mission.stages)


@pytest.mark.parametrize("mission", _examples())
def test_proto_satisfies_protovalidate(mission: Mission) -> None:
    """A domain-valid mission must also pass the proto's buf.validate constraints.

    Guards against the proto bounds and the pydantic bounds drifting apart (e.g. a
    heading range mismatch): a value the domain accepts must not be one the proto's
    own validator rejects.
    """
    protovalidate.validate(mission_to_proto(mission))


def test_protovalidate_rejects_out_of_range_heading() -> None:
    """A heading outside [0, 360) must fail the proto's buf.validate guard.

    The positive corpus only proves valid headings pass; this proves the bound is
    actually enforced -- a missing or widened constraint would let -90 through.
    """
    mission = mission_pb2.Mission(
        mission_id=str(uuid4()),
        stages=[
            mission_pb2.Stage(
                stage_id=str(uuid4()),
                kind=mission_pb2.STAGE_KIND_NAVIGATION,
                navigation=mission_pb2.NavigationStage(
                    waypoints=[
                        mission_pb2.Waypoint(
                            wgs84=mission_pb2.WGS84Waypoint(lat=52.5, lon=13.4, heading_deg=-90.0)
                        )
                    ]
                ),
            )
        ],
    )
    with pytest.raises(protovalidate.ValidationError):
        protovalidate.validate(mission)


_NEGATIVE_CORPUS = [
    '{"mission_id":"m","stagez":[]}',  # renamed structural field
    '{"mission_id":"m","stages":[{"stage_id":"s","waypoints":[]}]}',  # field on wrong level
    # Unknown enum name. The value must be one the contract does not define, so it changes when a
    # kind is added; COVERAGE lived here until it became real.
    '{"mission_id":"m","stages":[{"stage_id":"s","kind":"STAGE_KIND_SPRAYING"}]}',
]


@pytest.mark.parametrize("bad", _NEGATIVE_CORPUS)
def test_negative_corpus_rejected(bad: str) -> None:
    """The strict parser must reject renamed/misplaced/unknown-enum-name input.

    This is the guard against silent field-drop: a global ignore_unknown_fields
    would parse these to defaults and let a robot run a plausible-but-wrong mission.
    """
    with pytest.raises(json_format.ParseError):
        json_format.Parse(bad, mission_pb2.Mission(), ignore_unknown_fields=False)
