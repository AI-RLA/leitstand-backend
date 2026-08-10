"""Event-bus topic namespace -- single source of truth.

All real-time topics use one all-slashes hierarchy: ``events/<entity>/<id>/<kind>``.
Publishers and latched-readers build topics here so they cannot drift (a mismatch
would silently make a latched read return None). The frontend mirrors this scheme.

Matching on the bus is segment-aware (see ``EventBus``): a subscription to a prefix
matches the prefix itself or any path continuing after a ``/`` -- so ``events/robot``
matches ``events/robot/r1/pose`` but never ``events/robotXYZ``.
"""

from __future__ import annotations

from uuid import UUID

REGISTRY = "events/registry"


def robot_topic(robot_id: str, kind: str) -> str:
    """Per-robot topic. ``kind`` is pose | battery | factsheet | status."""
    return f"events/robot/{robot_id}/{kind}"


def robot_aggregate_topic(robot_id: str) -> str:
    """Merged per-robot stream (all kinds under one robot)."""
    return f"events/robot/{robot_id}"


def mission_topic(mission_id: UUID, kind: str) -> str:
    """Per-mission topic. ``kind`` is state | lifecycle."""
    return f"events/mission/{mission_id}/{kind}"
