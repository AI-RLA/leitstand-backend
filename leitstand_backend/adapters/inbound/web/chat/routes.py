"""Chat endpoint: streams an assistant turn to the browser and records what it proposed.

Holds no request-scoped DB session, unlike the fleet routes: a turn waits on the model for seconds.
Persistence goes through a repository that opens its own short transaction per write.

No conversation content is stored: only the fleet-changing calls the agent proposes, their
decisions, and their outcomes. The browser owns the transcript.

Returns 503 rather than 500 when chat is disabled or unconfigured: an absent assistant is an
expected state, not a server fault.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic_ai import DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.agent import AgentRunResult
from pydantic_ai.messages import FunctionToolResultEvent, RetryPromptPart, ToolReturnPart
from pydantic_ai.ui import NativeEvent

from leitstand_backend.adapters.inbound.web.chat import wire
from leitstand_backend.adapters.outbound.llm.agent_factory import (
    system_prompt_version,
    turn_usage_limits,
)
from leitstand_backend.adapters.outbound.llm.domain_mcp import (
    APPROVED_BY_KEY,
    DECISION_ID_KEY,
)
from leitstand_backend.application.tool_call_approval_service import ToolCallApprovalService
from leitstand_backend.domain.user import User
from leitstand_backend.infrastructure.deps import (
    get_current_user,
    get_tool_call_approval_service,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["chat"])


@router.post(
    "/chat",
    operation_id="chat",
    # Not a request model: this body's shape is the SDK's, and restating part of it would publish
    # a contract a caller could satisfy and still be refused.
    openapi_extra={
        "externalDocs": {
            "description": "Vercel AI SDK stream protocol",
            "url": "https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol",
        },
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "title": "ChatTurnRequest",
                        "description": (
                            "A Vercel AI SDK UI message stream request: the whole conversation so "
                            "far, plus any decision the operator has made on a proposed action. "
                            "The shape is defined by the AI SDK, whose client sends it; use that "
                            "client rather than composing it by hand."
                        ),
                    }
                }
            },
        },
    },
)
async def chat(
    request: Request,
    approval_service: ToolCallApprovalService = Depends(get_tool_call_approval_service),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Run one assistant turn and stream it as a Vercel AI UI message stream.

    The caller owns the conversation and sends the whole history every turn; no transcript is
    stored, so there is nothing to restore from this side.

    Proposed actions never execute on their own: they arrive as tool-approval chunks and pause the
    turn, and the caller returns a decision with the next request. A decision takes effect only
    where it matches a proposal this server recorded, and only once, so resending one or changing
    the action it names actuates nothing.
    """
    agent = getattr(request.app.state, "chat_agent", None)
    if agent is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "assistant unavailable; fleet control is unaffected",
        )

    # Parse before anything is spent: settling first would consume a proposal on a request that
    # then cannot run, and a spent proposal can never be approved again.
    parsed = wire.run_input(await request.body())

    wire.reject_repeated_tool_call_ids(parsed.messages)

    responses = list(wire.approval_responses(parsed.messages))
    settled = await _settle_approvals(approval_service, parsed.messages, current_user)

    settings = request.app.state.settings

    if responses and not settled.approvals:
        # An approval the operator gave has quietly done nothing: already decided, or naming an
        # action other than the one recorded. A warning, because that reads to them as a hang.
        logger.warning(
            "chat_decision_not_settled",
            decisions=len(responses),
            provider_tool_call_ids=[decision.provider_tool_call_id for decision in responses],
        )

    recorded: set[str] = set()

    async def record(result: AgentRunResult) -> None:
        """Persist what the turn proposed, once it has finished proposing it."""
        output = result.output
        if isinstance(output, DeferredToolRequests):
            for call in output.approvals:
                await approval_service.record_pending_tool_call(
                    provider_tool_call_id=call.tool_call_id,
                    tool_name=call.tool_name,
                    arguments=call.args_as_dict(),
                    model=settings.llm_model,
                    prompt_version=system_prompt_version(),
                )

    async def recording_outcomes(events: AsyncIterator[NativeEvent]) -> AsyncIterator[NativeEvent]:
        """Record each approved call's outcome as it returns, then pass the event on.

        An approved tool runs before the model request that may end the turn, so waiting for the run
        to finish loses the outcome of an action that already happened. Recorded before the event is
        forwarded, so the row is durable before the browser is told the action succeeded.

        A failure to record ends nothing: the action is done, and abandoning the turn would neither
        undo it nor leave the operator better off. Broad, because naming the persistence layer's
        errors here would reach across a boundary this layer does not cross.
        """
        async for event in events:
            if isinstance(event, FunctionToolResultEvent):
                try:
                    accounted = await _record_outcome(approval_service, settled, event.part)
                except Exception:
                    logger.exception(
                        "chat_outcome_not_recorded", provider_tool_call_id=event.part.tool_call_id
                    )
                else:
                    if accounted is not None:
                        recorded.add(accounted)
            yield event

    # Passing no results is safe only because the prune ran first: settle, then prune, then run,
    # and that order must not change.
    adapter = wire.adapter_for(
        agent, wire.pruned(parsed, set(settled.approvals)), request.headers.get("accept")
    )
    stream = adapter.transform_stream(
        recording_outcomes(
            adapter.run_stream_native(
                deferred_tool_results=settled if settled.approvals else None,
                usage_limits=turn_usage_limits(settings),
            )
        ),
        on_complete=record,
    )

    async def streamed():
        """Stream the turn, and name any decision it settled but never accounted for.

        A turn can end before the tool it authorised ever runs, leaving the row approved with no
        outcome. That is what is known, so no status is invented; the decision is named here because
        nothing else would point at it.
        """
        try:
            async for chunk in stream:
                yield chunk
        finally:
            # ToolDenied is truthy, so a plain truth test would read every denial as an approval
            # still owing an outcome.
            approved = {call for call, decision in settled.approvals.items() if decision is True}
            unaccounted = approved - recorded
            if unaccounted:
                logger.warning("chat_turn_ended_with_unknown_outcome", settled=sorted(unaccounted))

    return adapter.streaming_response(streamed())


async def _record_outcome(
    approval_service: ToolCallApprovalService,
    settled: DeferredToolResults,
    part: ToolReturnPart | RetryPromptPart,
) -> str | None:
    """Close out one approved call, returning the call id it accounted for.

    A tool that raised surfaces as a retry prompt, which is the only shape a live tool failure
    takes; a return whose outcome is a denial or an interruption is not an execution outcome.

    The outcome names the row this turn settled, never the id the model minted, which is not
    guaranteed unique beyond one run. The minted id is what comes back, because that is what the
    settled set is keyed by.
    """
    settled_id = _settled_row_id(settled, part.tool_call_id)
    if settled_id is None:
        return None
    if isinstance(part, ToolReturnPart):
        if part.outcome != "success":
            return None
        await approval_service.record_tool_outcome(
            settled_id, succeeded=True, result={"content": _jsonable(part.content)}
        )
    else:
        await approval_service.record_tool_outcome(
            settled_id, succeeded=False, error=str(part.content)
        )
    return part.tool_call_id


def _settled_row_id(settled: DeferredToolResults, provider_tool_call_id: str | None) -> UUID | None:
    """Which stored proposal this turn settled for that call, if any."""
    if provider_tool_call_id is None:
        return None
    decision = settled.metadata.get(provider_tool_call_id)
    if not isinstance(decision, dict):
        return None
    decision_id = decision.get(DECISION_ID_KEY)
    return UUID(decision_id) if decision_id else None


def _jsonable(content) -> object:
    """Reduce a tool's return value to something the JSON column can hold.

    Asks the serialiser rather than checking the outermost type: an unstorable value nested in a
    list would otherwise fail the insert while recording an action that already happened.
    """
    try:
        json.dumps(content)
    except (TypeError, ValueError):
        return str(content)
    return content


async def _settle_approvals(
    approval_service: ToolCallApprovalService,
    messages,
    user: User,
) -> DeferredToolResults:
    """Resume only the approvals the server's own record settles, so the record gates actuation.

    Building the deferred results from the request body would trust the client: a replayed approve
    could run a create twice.

    Each settled decision carries its provenance forward as per-call metadata, so the audit row
    names the decision that authorised it rather than one inferred from the turn.
    """
    approvals: dict[str, bool | ToolDenied] = {}
    metadata: dict[str, dict] = {}
    for decision in wire.approval_responses(messages):
        decision_id = await approval_service.record_decision(
            provider_tool_call_id=decision.provider_tool_call_id,
            decided_by=str(user.id),
            approved=decision.approved,
            tool_name=decision.tool_name,
            arguments=decision.arguments,
        )
        if decision_id is None:
            continue
        approvals[decision.provider_tool_call_id] = (
            True
            if decision.approved
            else ToolDenied(message=decision.reason or "Denied by the operator.")
        )
        metadata[decision.provider_tool_call_id] = {
            APPROVED_BY_KEY: str(user.id),
            DECISION_ID_KEY: str(decision_id),
        }
    return DeferredToolResults(approvals=approvals, metadata=metadata)
