"""An audit row records what performed an action, not only on whose behalf.

The mechanism is ambient: the audit writer reads a ContextVar rather than taking provenance as an
argument, so no service or route has to thread it through. These pin that read, because with one
operator and no agent write the property is otherwise unobservable, and a wrong label would
surface first in the evidence record itself. Only the writer is isolated here; which provenance a
turn carries is pinned where it is decided, in the chat route's tests.
"""

from __future__ import annotations

import pytest

from leitstand_backend.domain.model.audit import (
    AUTHORITY_APPROVED_PROPOSAL,
    AUTHORITY_AUTONOMOUS,
)
from leitstand_backend.domain.user import User
from leitstand_backend.infrastructure.deps import _make_audit_writer
from leitstand_backend.infrastructure.provenance import (
    AgentOrigin,
    ToolCallProvenance,
    agent_origin,
    tool_call_provenance,
)

_OPERATOR = User(id="operator", name="Operator")


class _CapturingSession:
    """Captures the row the audit writer adds, standing in for a real AsyncSession."""

    def __init__(self) -> None:
        self.rows: list = []

    def add(self, row) -> None:
        self.rows.append(row)


@pytest.fixture(autouse=True)
def _clear_context():
    turn = agent_origin.set(None)
    call = tool_call_provenance.set(None)
    yield
    agent_origin.reset(turn)
    tool_call_provenance.reset(call)


async def _write_once() -> _CapturingSession:
    session = _CapturingSession()
    writer = _make_audit_writer(session, _OPERATOR)
    await writer("mission.dispatch", "mission", "m-1", {"robot_id": "scout_2"})
    return session


@pytest.mark.asyncio
async def test_a_direct_action_is_recorded_as_human() -> None:
    session = await _write_once()

    (row,) = session.rows
    assert row.actor == "human"
    assert row.authority == "direct"
    assert row.decided_by is None
    assert row.user_id == "operator"


@pytest.mark.asyncio
async def test_an_approved_call_names_its_decision() -> None:
    agent_origin.set(AgentOrigin(model="nemotron", prompt_version="assistant-v1"))
    tool_call_provenance.set(
        ToolCallProvenance(
            tool_call_id="3f6c1a90-0000-4000-8000-000000000009",
            approved=True,
            approved_by="operator",
        )
    )

    session = await _write_once()

    (row,) = session.rows
    assert row.actor == "ai_agent"
    assert row.authority == AUTHORITY_APPROVED_PROPOSAL
    assert row.decided_by == "operator"
    assert str(row.tool_call_id) == "3f6c1a90-0000-4000-8000-000000000009"
    assert row.model == "nemotron"
    assert row.prompt_version == "assistant-v1"
    # user_id is still on-whose-behalf, distinct from actor.
    assert row.user_id == "operator"


@pytest.mark.asyncio
async def test_an_unapproved_agent_write_is_the_alarm() -> None:
    """v1 never legitimately produces this, so it has to be loud rather than plausible."""
    agent_origin.set(AgentOrigin(model="nemotron", prompt_version="assistant-v1"))

    session = await _write_once()

    (row,) = session.rows
    assert row.actor == "ai_agent"
    assert row.authority == AUTHORITY_AUTONOMOUS
    assert row.decided_by is None
    assert row.tool_call_id is None
