"""Plan digest: identity-blind, order-blind over keys, content-sensitive."""

from uuid import uuid4

from leitstand_backend.domain.model.mission.mission import NavigationStage
from leitstand_backend.domain.model.mission.stages_digest import stages_digest
from leitstand_backend.domain.model.mission.waypoint import WGS84Waypoint


def _wp(lat: float, lon: float) -> WGS84Waypoint:
    return WGS84Waypoint(lat=lat, lon=lon)


def _stage(*points: tuple[float, float], on_cancel=None) -> NavigationStage:
    return NavigationStage(
        stage_id=uuid4(),
        waypoints=[_wp(lat, lon) for lat, lon in points],
        on_cancel=on_cancel,
    )


def test_identical_content_with_different_ids_digests_the_same():
    a = [_stage((52.0, 8.0), (52.1, 8.1))]
    b = [_stage((52.0, 8.0), (52.1, 8.1))]
    assert a[0].stage_id != b[0].stage_id
    assert stages_digest(a) == stages_digest(b)


def test_ids_are_stripped_inside_cleanup_stages_too():
    a = [_stage((52.0, 8.0), (52.1, 8.1), on_cancel=[_stage((52.0, 8.0), (52.0, 8.0))])]
    b = [_stage((52.0, 8.0), (52.1, 8.1), on_cancel=[_stage((52.0, 8.0), (52.0, 8.0))])]
    assert stages_digest(a) == stages_digest(b)


def test_key_order_does_not_matter():
    dumped = [s.model_dump(mode="json") for s in [_stage((52.0, 8.0), (52.1, 8.1))]]
    reordered = [dict(reversed(list(stage.items()))) for stage in dumped]
    assert stages_digest(dumped) == stages_digest(reordered)


def test_a_moved_waypoint_changes_the_digest():
    before = [_stage((52.0, 8.0), (52.1, 8.1))]
    after = [_stage((52.0, 8.0), (52.1, 8.1001))]
    assert stages_digest(before) != stages_digest(after)


def test_dumped_and_model_inputs_agree():
    stages = [_stage((52.0, 8.0), (52.1, 8.1))]
    assert stages_digest(stages) == stages_digest([s.model_dump(mode="json") for s in stages])
