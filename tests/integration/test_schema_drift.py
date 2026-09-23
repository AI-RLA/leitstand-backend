"""The mission and run ORM rows must match what the migrations actually build.

Both sides are hand-written, so nothing but a comparison against a migrated database stops them
diverging: a column added to one and not the other stays green forever and fails at runtime.
Scoped to the mission, run and site tables, because a whole-schema comparison reports unrelated
noise (the PostGIS system table, a computed column) that nobody introduced.
"""

from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# Imported for the side effect: the Row classes register on Base.metadata at import time.
from leitstand_backend.adapters.outbound.persistence.postgres import models  # noqa: F401
from leitstand_backend.domain.model.mission.run_lifecycle import ACTIVE_STATES, TERMINAL_STATES
from leitstand_backend.infrastructure.db import Base

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

_OWNED_TABLES = {
    "missions",
    "mission_stages",
    "mission_runs",
    "mission_run_transitions",
    "stage_runs",
    "sites",
}


def _owned(obj, name, type_, reflected, compare_to) -> bool:
    table = obj.name if type_ == "table" else getattr(getattr(obj, "table", None), "name", None)
    return table in _OWNED_TABLES


def _diffs(connection):
    context = MigrationContext.configure(
        connection, opts={"compare_type": True, "include_object": _owned}
    )
    return compare_metadata(context, Base.metadata)


async def test_owned_tables_match_the_migrated_schema(db_engine: AsyncEngine) -> None:
    async with db_engine.connect() as connection:
        diffs = await connection.run_sync(_diffs)

    assert diffs == [], (
        "ORM rows drifted from the migrated schema for "
        f"{sorted(_OWNED_TABLES)}: {diffs}. Add a migration, or fix the row definition."
    )


@pytest.mark.parametrize("index_name", ["uq_run_robot_active", "ix_run_mission_active"])
async def test_the_active_run_indexes_cover_every_active_state(
    db_engine: AsyncEngine, index_name: str
) -> None:
    """Alembic's autogenerate ignores a partial index's predicate, so it is checked by hand."""
    async with db_engine.connect() as connection:
        predicate = await connection.scalar(
            text(
                "SELECT pg_get_expr(i.indpred, i.indrelid) FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :name"
            ),
            {"name": index_name},
        )
    assert predicate, f"{index_name} has no predicate"
    for state in ACTIVE_STATES:
        assert f"'{state.value}'" in predicate, f"{index_name} lacks {state.value}"
    for state in TERMINAL_STATES:
        assert f"'{state.value}'" not in predicate, f"{index_name} includes {state.value}"
