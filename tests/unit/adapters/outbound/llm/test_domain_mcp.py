"""The agent's tool surface is curated, and the curation has to be enforced.

Generation from the OpenAPI spec exposes every endpoint by default, so the failures are silent: a
rename drops a tool, or a new write route reaches the model ungated.
"""

from __future__ import annotations

import pytest
from fastmcp import Client

from leitstand_backend.adapters.outbound.llm.domain_mcp import (
    build_domain_mcp,
    requires_approval,
)
from leitstand_backend.infrastructure.factory import create_app
from leitstand_backend.infrastructure.provenance import AgentOrigin
from leitstand_backend.infrastructure.settings import Settings

pytestmark = pytest.mark.asyncio


# A method that changes state. Reading the spec rather than the tool's name is the point: a name is
# a convention and a method is the contract, so a mutating route named without a familiar verb is
# still caught.
_MUTATING_METHODS = frozenset({"post", "put", "patch", "delete"})


def _app():
    return create_app(Settings(zenoh_disabled=True, auto_migrate=False))


async def _tools_of(app):
    mcp, client = build_domain_mcp(app, AgentOrigin())
    try:
        async with Client(mcp) as connected:
            return await connected.list_tools()
    finally:
        await client.aclose()


async def _tools():
    return await _tools_of(_app())


def _mutating_operation_ids(app) -> set[str]:
    """Every operation the API itself declares as state-changing."""
    return {
        operation["operationId"]
        for path in app.openapi()["paths"].values()
        for method, operation in path.items()
        if method.lower() in _MUTATING_METHODS and "operationId" in operation
    }


async def test_a_tool_nobody_classified_is_still_gated() -> None:
    """A write route exposed but never declared must be held, not treated as safe."""
    assert requires_approval("estop_robot")
    assert requires_approval("leitstand_estop_robot")


async def test_writes_are_gated_and_reads_are_not() -> None:
    """The gate's two halves, keyed on the spec rather than a hand-kept list."""
    app = _app()
    exposed = {t.name for t in await _tools_of(app)}
    mutating = _mutating_operation_ids(app)

    assert len(exposed & mutating) >= 10
    assert len(exposed - mutating) >= 5
    assert all(requires_approval(name) for name in exposed & mutating)
    assert not any(requires_approval(name) for name in exposed - mutating)


async def test_tool_names_are_operation_ids() -> None:
    """The method-keyed guard matches tools to operations by name, so pin that they correspond.

    Names track operation ids only because every curated route sets one explicitly; without that the
    generator derives them, and the guard above would quietly stop matching anything.
    """
    app = _app()
    exposed = {t.name for t in await _tools_of(app)}
    operation_ids = {
        operation["operationId"]
        for path in app.openapi()["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    }

    assert exposed <= operation_ids


async def test_every_tool_has_a_usable_description() -> None:
    """Guards the failure that makes a curated surface useless.

    Without an explicit operation_id and docstring, the generator derives a summary from the
    function name: "List Robots", eleven characters. One tool survives that; several do not,
    because the model has nothing to tell them apart with.
    """
    tools = await _tools()
    thin = [(t.name, len(t.description or "")) for t in tools if len(t.description or "") < 40]

    assert tools, "no tools exposed at all: curation matched nothing"
    assert thin == [], f"tools with descriptions too thin to route on: {thin}"
