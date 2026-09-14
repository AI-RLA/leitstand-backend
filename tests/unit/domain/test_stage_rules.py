"""Rules a stage list must satisfy, checked on the domain functions directly."""

from uuid import uuid4

import pytest

from leitstand_backend.domain.errors import StageNotHomogeneous, StageSpansSites
from leitstand_backend.domain.model.mission.mission import NavigationStage
from leitstand_backend.domain.model.mission.stage_rules import (
    coverage_stages,
    validate_homogeneous_frames,
    validate_single_site,
)
from leitstand_backend.domain.model.mission.waypoint import SiteLocalWaypoint, WGS84Waypoint
from tests.fakes.planned_coverage import coverage_stage


def _wgs(lat: float = 52.3) -> WGS84Waypoint:
    return WGS84Waypoint(lat=lat, lon=8.05)


def _local(site_id, x: float = 0.0) -> SiteLocalWaypoint:
    return SiteLocalWaypoint(site_id=site_id, x=x, y=0.0)


def test_a_stage_mixing_frames_is_refused_by_its_index() -> None:
    site = uuid4()
    stages = [
        NavigationStage(stage_id=uuid4(), waypoints=[_wgs()]),
        NavigationStage(stage_id=uuid4(), waypoints=[_wgs(), _local(site)]),
    ]
    with pytest.raises(StageNotHomogeneous) as exc:
        validate_homogeneous_frames(stages)
    assert exc.value.stage_index == 1


def test_a_cleanup_stage_is_checked_too() -> None:
    site = uuid4()
    cleanup = NavigationStage(stage_id=uuid4(), waypoints=[_wgs(), _local(site)])
    stage = NavigationStage(stage_id=uuid4(), waypoints=[_wgs()], on_cancel=[cleanup])
    with pytest.raises(StageNotHomogeneous):
        validate_homogeneous_frames([stage])


def test_a_stage_spanning_two_sites_is_refused() -> None:
    a, b = uuid4(), uuid4()
    stage = NavigationStage(stage_id=uuid4(), waypoints=[_local(a), _local(b, 1.0)])
    with pytest.raises(StageSpansSites) as exc:
        validate_single_site([stage])
    assert exc.value.site_ids == {a, b}


def test_one_site_per_stage_passes() -> None:
    site = uuid4()
    validate_single_site(
        [NavigationStage(stage_id=uuid4(), waypoints=[_local(site), _local(site, 1.0)])]
    )


def test_coverage_stages_finds_nested_ones() -> None:
    nested = coverage_stage()
    outer = NavigationStage(stage_id=uuid4(), waypoints=[_wgs()], on_cancel=[nested])
    top = coverage_stage()
    assert [s.stage_id for s in coverage_stages([outer, top])] == [nested.stage_id, top.stage_id]
