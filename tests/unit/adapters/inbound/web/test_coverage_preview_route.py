"""GET /api/v1/coverage/preview plans a stage and stores nothing."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from leitstand_backend.domain.errors import (
    CoveragePlannerUnavailable,
    CoveragePlanRejected,
    FieldNotFoundError,
    FieldNotPlannable,
    RobotFactsheetMissing,
    TurningRadiusBelowRobot,
    UnsupportedStageKind,
)
from leitstand_backend.infrastructure.deps import get_audit_writer, get_coverage_stage_planner
from leitstand_backend.infrastructure.factory import create_app
from leitstand_backend.infrastructure.settings import Settings
from tests.fakes.planned_coverage import coverage_stage

_FIELD_ID = uuid4()
_QUERY = {"field_id": str(_FIELD_ID), "operation_width_m": 3.0, "params_robot_id": "scout"}


def _app(error: Exception | None = None):
    app = create_app(Settings(zenoh_disabled=True, auto_migrate=False))
    audited: list[tuple] = []
    planned = coverage_stage(field_id=_FIELD_ID)

    async def plan(inputs, *, stage_id=None):
        if error is not None:
            raise error
        return planned

    async def audit(*args):
        audited.append(args)

    app.dependency_overrides[get_coverage_stage_planner] = lambda: plan
    app.dependency_overrides[get_audit_writer] = lambda: audit
    return app, planned, audited


def test_a_preview_returns_the_planned_stage_and_audits_nothing():
    app, planned, audited = _app()
    with TestClient(app) as client:
        resp = client.get("/api/v1/coverage/preview", params=_QUERY)

    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "coverage"
    assert body["stage_id"] == str(planned.stage_id)
    assert body["provenance"]["field_id"] == str(_FIELD_ID)
    assert body["provenance"]["metrics"]["swath_count"] == 3
    assert audited == []


@pytest.mark.parametrize(
    "error,status",
    [
        (FieldNotFoundError(_FIELD_ID), 404),
        (FieldNotPlannable(_FIELD_ID), 422),
        (RobotFactsheetMissing("scout"), 422),
        (UnsupportedStageKind("scout", uuid4(), "coverage"), 422),
        (TurningRadiusBelowRobot("scout", 1.0, 1.5), 422),
        (CoveragePlanRejected("the plan covers 40 m2 of a 170 m2 field"), 422),
        (CoveragePlannerUnavailable("coverage planner unreachable"), 503),
    ],
    ids=lambda v: type(v).__name__,
)
def test_each_planning_error_maps_to_its_status(error, status):
    app, _, _ = _app(error)
    with TestClient(app) as client:
        resp = client.get("/api/v1/coverage/preview", params=_QUERY)

    assert resp.status_code == status


def test_neither_robot_nor_radius_is_a_validation_error():
    app, _, _ = _app()
    with TestClient(app) as client:
        resp = client.get(
            "/api/v1/coverage/preview",
            params={"field_id": str(_FIELD_ID), "operation_width_m": 3.0},
        )

    assert resp.status_code == 422
    assert "params_robot_id or turning_radius_m" in resp.text


def test_a_hand_entered_radius_needs_no_robot():
    app, _, _ = _app()
    with TestClient(app) as client:
        resp = client.get(
            "/api/v1/coverage/preview",
            params={"field_id": str(_FIELD_ID), "operation_width_m": 3.0, "turning_radius_m": 1.0},
        )

    assert resp.status_code == 200
