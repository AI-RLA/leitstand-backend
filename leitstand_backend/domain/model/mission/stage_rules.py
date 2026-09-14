"""Rules a stage list must satisfy before it is stored or sent to a robot."""

from __future__ import annotations

from leitstand_backend.domain.errors import StageNotHomogeneous, StageSpansSites
from leitstand_backend.domain.model.mission.mission import CoverageStage, Stage, stage_waypoints
from leitstand_backend.domain.model.mission.waypoint import SiteLocalWaypoint


def validate_homogeneous_frames(stages: list[Stage]) -> None:
    """All waypoints in a stage must share their ``kind`` discriminator."""
    for index, stage in enumerate(stages):
        kinds = {wp.kind for wp in stage_waypoints(stage)}
        if len(kinds) > 1:
            raise StageNotHomogeneous(index, kinds)
        if stage.on_cancel:
            validate_homogeneous_frames(stage.on_cancel)


def validate_single_site(stages: list[Stage]) -> None:
    """A site-local stage is driven in one site's frame; a WGS84 stage names none."""
    for index, stage in enumerate(stages):
        sites = {wp.site_id for wp in stage_waypoints(stage) if isinstance(wp, SiteLocalWaypoint)}
        if len(sites) > 1:
            raise StageSpansSites(index, sites)
        if stage.on_cancel:
            validate_single_site(stage.on_cancel)


def coverage_stages(stages: list[Stage]) -> list[CoverageStage]:
    """Every coverage stage in the list, cleanup stages included."""
    found: list[CoverageStage] = []
    for stage in stages:
        if isinstance(stage, CoverageStage):
            found.append(stage)
        if stage.on_cancel:
            found.extend(coverage_stages(stage.on_cancel))
    return found
