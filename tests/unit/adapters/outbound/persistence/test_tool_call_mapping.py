"""The stored and domain shapes of a tool call stay in step.

The Postgres adapter maps them field by field and no test here exercises it against a database,
so a field added on one side alone would first surface at runtime.
"""

from __future__ import annotations

from leitstand_backend.adapters.outbound.persistence.postgres.models import ToolCallRow
from leitstand_backend.domain.model.chat.tool_call import ToolCall


def test_the_row_and_the_domain_model_carry_the_same_fields() -> None:
    assert set(ToolCall.model_fields) == {column.name for column in ToolCallRow.__table__.columns}
