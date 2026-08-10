"""Tests for referenced_site_ids -- the recursive site-reference extractor.

This is the correctness core of the site-in-use guard (H3): it must find
site_local references nested inside on_cancel cleanup stages, which the old
single-level jsonpath query missed.
"""

from __future__ import annotations

from uuid import uuid4

from leitstand_backend.domain.model.mission.mission import NavigationStage, referenced_site_ids
from leitstand_backend.domain.model.mission.waypoint import SiteLocalWaypoint, WGS84Waypoint

SITE_A = uuid4()
SITE_B = uuid4()
SITE_C = uuid4()


def _wgs84_stage(on_cancel=None) -> NavigationStage:
    return NavigationStage(
        stage_id=uuid4(),
        waypoints=[WGS84Waypoint(lat=52.0, lon=8.0)],
        on_cancel=on_cancel,
    )


def _site_stage(site_id, on_cancel=None) -> NavigationStage:
    return NavigationStage(
        stage_id=uuid4(),
        waypoints=[SiteLocalWaypoint(site_id=site_id, x=1.0, y=1.0)],
        on_cancel=on_cancel,
    )


def test_no_site_local_waypoints_returns_empty():
    assert referenced_site_ids([_wgs84_stage(), _wgs84_stage()]) == set()


def test_collects_top_level_site_refs():
    assert referenced_site_ids([_site_stage(SITE_A), _site_stage(SITE_B)]) == {SITE_A, SITE_B}


def test_collects_site_ref_in_on_cancel_branch():
    # SITE_B is referenced ONLY inside the cleanup branch -- the old jsonpath missed this.
    stage = _wgs84_stage(on_cancel=[_site_stage(SITE_B)])
    assert referenced_site_ids([stage]) == {SITE_B}


def test_collects_site_ref_nested_deeper_in_on_cancel():
    # on_cancel whose cleanup stage itself has an on_cancel referencing SITE_C.
    deep = _wgs84_stage(on_cancel=[_site_stage(SITE_C)])
    stage = _site_stage(SITE_A, on_cancel=[deep])
    assert referenced_site_ids([stage]) == {SITE_A, SITE_C}
