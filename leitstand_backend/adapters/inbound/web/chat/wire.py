"""Vercel AI SDK wire: parsing, adapter construction, and the checks a turn runs before it starts."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import structlog
from fastapi import HTTPException, status
from pydantic_ai.ui import UIAdapter
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import (
    DynamicToolApprovalRespondedPart,
    DynamicToolOutputAvailablePart,
    DynamicToolOutputDeniedPart,
    DynamicToolOutputErrorPart,
    DynamicToolUIPart,
    FileUIPart,
    RequestData,
    ToolApprovalResponded,
    ToolApprovalRespondedPart,
    ToolOutputAvailablePart,
    ToolOutputDeniedPart,
    ToolOutputErrorPart,
    ToolUIPart,
    UIMessage,
)

logger = structlog.get_logger(__name__)

# Must match the frontend's `ai` package major. Below 6 the tool-approval chunks are dropped with
# no error, which loses the gate rather than breaking it.
_SDK_VERSION = 7

_TOOL_PARTS = (ToolUIPart, DynamicToolUIPart)

# A part keeps its approval after the call completes, so the decision counts only while the part is
# still the one asking. Reading the approval alone would answer every settled call again each turn.
_APPROVAL_RESPONDED_PARTS = (ToolApprovalRespondedPart, DynamicToolApprovalRespondedPart)

# A resent tool part is trustworthy only once it carries its result; anything earlier is a claim
# about a call that has not finished.
_TERMINAL_TOOL_PARTS = (
    ToolOutputAvailablePart,
    ToolOutputErrorPart,
    ToolOutputDeniedPart,
    DynamicToolOutputAvailablePart,
    DynamicToolOutputErrorPart,
    DynamicToolOutputDeniedPart,
)


@dataclass(frozen=True)
class OperatorDecision:
    """One approve or deny, reduced from the wire to what settling it needs.

    Downstream reads these fields rather than the SDK's own models, so what the turn depends on is
    exactly what is named here.
    """

    provider_tool_call_id: str
    approved: bool
    reason: str | None
    tool_name: str
    arguments: dict | None


def run_input(body: bytes) -> RequestData:
    """Parse the client's turn, refusing a body this endpoint will not run.

    The SDK owns this shape, so its refusal is translated here to keep a malformed request an
    answer about the request rather than a server fault.
    """
    try:
        parsed = _without_attachments(VercelAIAdapter.build_run_input(body))
        VercelAIAdapter.load_messages(parsed.messages)
    except ValueError as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "malformed chat request"
        ) from error
    return parsed


def _without_attachments(parsed: RequestData) -> RequestData:
    """Drop file parts: this endpoint carries an operator's text and decisions, nothing else.

    The SDK's URL allowlist cannot bound a ``data:`` URI, which is decoded before it is consulted.
    """
    kept: list[UIMessage] = []
    dropped = 0
    for message in parsed.messages:
        parts = [part for part in message.parts if not isinstance(part, FileUIPart)]
        dropped += len(message.parts) - len(parts)
        if parts:
            kept.append(message.model_copy(update={"parts": parts}))
    if not dropped:
        return parsed
    logger.warning("chat_attachment_dropped", parts=dropped)
    return parsed.model_copy(update={"messages": kept})


def adapter_for(agent, parsed, accept: str | None) -> UIAdapter:
    """Build the adapter that turns a run into the stream the browser expects.

    No URL scheme is allowed: file parts are dropped at parse, and this keeps one the run reaches by
    another route from being fetched inside the fleet network.
    """
    return VercelAIAdapter(
        agent=agent,
        run_input=parsed,
        accept=accept,
        sdk_version=_SDK_VERSION,
        allowed_file_url_schemes=frozenset(),
    )


def reject_repeated_tool_call_ids(messages: list[UIMessage]) -> None:
    """Refuse a history where an id the operator decided on names more than one call.

    The decision authorises the arguments the operator saw. A second part carrying that id offers
    the run a different set under the same name, and settling first would spend the proposal on a
    history that was never coherent. Ids that no decision names are left alone: a model is free to
    number its calls per response, and the run reads a repeat among finished calls without
    complaint.

    Read from the parsed parts, never the raw JSON: the SDK's models populate by field name as well
    as by alias, so one id can arrive spelled two ways and a raw scan would miss the collision.
    """
    decided: set[str] = set()
    counts: dict[str, int] = {}
    for message in messages:
        for part in message.parts:
            if not isinstance(part, _TOOL_PARTS):
                continue
            counts[part.tool_call_id] = counts.get(part.tool_call_id, 0) + 1
            if isinstance(part, _APPROVAL_RESPONDED_PARTS):
                decided.add(part.tool_call_id)
    for call_id in decided:
        if counts[call_id] > 1:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"tool call {call_id} appears more than once",
            )


def approval_responses(messages: list[UIMessage]) -> Iterator[OperatorDecision]:
    """Yield the operator's decisions on the latest proposal.

    Only the newest assistant message is read: the client resends the whole history every turn, and
    older messages keep their answered decisions in it forever.
    """
    latest = next((m for m in reversed(messages) if m.role == "assistant"), None)
    if latest is None:
        return
    for part in latest.parts:
        if not isinstance(part, _APPROVAL_RESPONDED_PARTS):
            continue
        approval = part.approval
        if not isinstance(approval, ToolApprovalResponded):
            continue
        yield OperatorDecision(
            provider_tool_call_id=part.tool_call_id,
            approved=bool(approval.approved),
            reason=approval.reason,
            tool_name=_tool_name_of(part),
            arguments=part.input,
        )


def pruned(parsed: RequestData, settled_ids: set[str]) -> RequestData:
    """Drop the tool parts the server did not settle, before the run sees them.

    Kept only when already terminal or settled this turn, so a replayed decision and one crafted
    into a message the settle scan never reached both go. The adapter's fallback rebuilds
    resolutions from these same messages, so once the claims are gone it has nothing to run.
    """
    kept = []
    for message in parsed.messages:
        parts = [
            part
            for part in message.parts
            if not isinstance(part, _TOOL_PARTS)
            or isinstance(part, _TERMINAL_TOOL_PARTS)
            or part.tool_call_id in settled_ids
        ]
        if parts:
            kept.append(message.model_copy(update={"parts": parts}))
    return parsed.model_copy(update={"messages": kept})


def _tool_name_of(part: ToolUIPart | DynamicToolUIPart) -> str:
    """Return the tool a part names, however the SDK spells it.

    A statically declared tool carries its name in the part type; a dynamic one carries the type
    ``dynamic-tool`` and names the tool separately.
    """
    return getattr(part, "tool_name", None) or str(part.type).removeprefix("tool-")
