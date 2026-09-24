"""Curated MCP tool surface over this backend's own REST API.

Generated from the live OpenAPI spec and called through an ASGI transport, so tools re-enter
the app in-process: no extra port, and reads see live in-process telemetry rather than the
staler values Postgres holds.

``from_openapi`` maps every endpoint to a tool by default, which would bury a small local model in
dozens of near-identical choices, so only the routes matched below are exposed.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.providers.openapi import MCPType, RouteMap

from leitstand_backend.infrastructure.auth import caller_credential
from leitstand_backend.infrastructure.provenance import (
    AgentOrigin,
    AgentOriginBoundary,
    ToolCallProvenance,
    tool_call_provenance,
)

# The key the agent packs each call's approval provenance under, in the MCP request metadata. Both
# ends must agree on it, so it lives here with the server that reads it.
CALL_PROVENANCE_KEY = "leitstand"

# The fields inside that envelope. A mismatch between the end that mints them and the end that
# reads them raises nothing; it audits an approved write as if nobody had approved it.
APPROVED_KEY = "approved"
APPROVED_BY_KEY = "approved_by"
DECISION_ID_KEY = "decision_id"

# The curated read tools, by operation_id
_READ_TOOLS = frozenset(
    {
        "list_robots",
        "get_robot",
        "list_missions",
        "get_mission",
        "get_mission_state",
        "preview_coverage",
        "list_fields",
        "get_field",
        "list_sites",
        "get_site",
    }
)

_READ_PATHS = (
    r"^/api/v1/robots$",
    r"^/api/v1/robots/\{robot_id\}$",
    r"^/api/v1/missions/$",
    r"^/api/v1/missions/\{mission_id\}$",
    r"^/api/v1/missions/\{mission_id\}/state$",
    r"^/api/v1/coverage/preview$",
    r"^/api/v1/fields/$",
    r"^/api/v1/fields/\{field_id\}$",
    r"^/api/v1/sites/$",
    r"^/api/v1/sites/\{site_id\}$",
)

# Write routes exposed to the agent, each by (path, methods). Every one changes the physical fleet
# or the catalog and is gated behind human approval on the agent side (see requires_approval).
_WRITE_ROUTES = (
    (r"^/api/v1/missions/$", ["POST"]),
    (r"^/api/v1/missions/coverage$", ["POST"]),
    (r"^/api/v1/missions/\{mission_id\}$", ["PATCH", "DELETE"]),
    (r"^/api/v1/missions/\{mission_id\}/assign$", ["POST"]),
    (r"^/api/v1/missions/\{mission_id\}/unassign$", ["POST"]),
    (r"^/api/v1/missions/\{mission_id\}/dispatch$", ["POST"]),
    (r"^/api/v1/missions/\{mission_id\}/cancel$", ["POST"]),
    (r"^/api/v1/missions/\{mission_id\}/pause$", ["POST"]),
    (r"^/api/v1/missions/\{mission_id\}/resume$", ["POST"]),
    (r"^/api/v1/missions/\{mission_id\}/restore$", ["POST"]),
    (r"^/api/v1/fields/$", ["POST"]),
    (r"^/api/v1/fields/\{field_id\}$", ["PATCH", "DELETE"]),
    (r"^/api/v1/sites/$", ["POST"]),
    (r"^/api/v1/sites/\{site_id\}$", ["PATCH", "DELETE"]),
)


# When the model must reach for a tool, as opposed to what the tool returns, which the route
# docstring already says and which stays the API's own documentation. Appended rather than
# substituted so the facts keep one home and a missing entry degrades to the docstring alone.
#
# Measured, not guessed: without these, a fleet question carrying a brevity instruction ("answer in
# one short sentence") gets answered from nothing, with invented robot ids, most of the time.
_WHEN_TO_CALL = {
    "list_robots": (
        "Call this for any question about which robots exist, which are online, or the fleet's "
        "current state. You cannot know the fleet without calling it, and robot ids are not "
        "guessable."
    ),
    "get_robot": (
        "Call this for any question about one named robot's current status, battery, connectivity "
        "or position."
    ),
    "list_missions": (
        "Call this for any question about which missions exist or their status, including whether "
        "one is finished, running or failed. Each mission carries its latest run, whose status is "
        "the mission's current state; null means it has never run. A question naming a mission "
        "in words is answered by passing that name and reading the latest run back."
    ),
    "dispatch_mission": (
        "Call this to run a mission, including one that has run before: every run keeps its own "
        "record and nothing is reset. While a run of the same mission is still active it is "
        "refused: wait for it to end, or cancel it first."
    ),
    "get_mission": "Call this for the full definition of one mission whose id you already have.",
    "get_mission_state": (
        "Call this for a mission's live per-stage progress: which stage it is on, how far along, "
        "or why it failed. Progress changes continuously, so never answer it from memory."
    ),
    "list_fields": (
        "Call this for any question about which fields exist. The list may legitimately be empty."
    ),
    "get_field": "Call this for one field's boundary and area, given its id.",
    "list_sites": (
        "Call this for any question about which sites exist. The list may legitimately be empty."
    ),
    "get_site": "Call this for one site's anchor and outline, given its id.",
    "plan_coverage_mission": (
        "Call this whenever the operator wants a whole field covered, surveyed, mown or treated, "
        "rather than the robot driven to points they named. It derives the path from the stored "
        "field boundary, so you pass a field id and numbers and never coordinates. The mission "
        "name defaults to the field's, so the one value to ask the operator for is the "
        "implement's working width. Never substitute the robot's own width for it, and never "
        "guess it from anything you read elsewhere. "
        "The robot id is required and has no default. Take it from list_robots and never from a "
        "name the operator speaks: robots carry no name, so what they call a machine is not its "
        "id, and nothing here resolves one to the other. The robot you name decides the plan's "
        "shape, because its declared turning radius is what every turn is laid out to, and is "
        "also the headland unless the operator states one. Say which robot you used. "
        "To change an existing path, pass its coverage stage's id as replan: a path is "
        "re-derived rather than edited, and without it you leave the operator holding two "
        "missions for one field. The mission keeps its id, its name, its other stages and "
        "every run it has had; only that stage's path changes, so report it as the same "
        "mission re-planned. "
        "When the result reports a max_excursion_m above zero, say so and give the number: the "
        "machine leaves the field by that much, and only the operator knows what the edge is. "
        "covered_area_m2 is clipped to the field boundary, so it never exceeds field_area_m2. "
        "Report both as the result gives them."
    ),
    "create_mission": (
        "Call this for a mission whose waypoints the operator stated, or one that combines such "
        "stages with a coverage stage. For a field to be covered on its own, call "
        "plan_coverage_mission instead. Never derive a waypoint from a boundary: a coverage stage "
        "is the way to work a field, and guessing coordinates is not a substitute."
    ),
    "preview_coverage": (
        "Call this to show what a coverage plan would look like before anything is stored: the "
        "same inputs as a coverage stage, the same result, nothing written. Report the swath "
        "count, the covered area and max_excursion_m as it gives them."
    ),
}


def _add_agent_hints(route, component) -> None:
    """Append the when-to-call hint for a tool, if it has one.

    Deliberately trivial: the generator swallows an exception here into a log warning, so anything
    that can fail would fail silently and leave the plain description.
    """
    hint = _WHEN_TO_CALL.get(component.name)
    if hint:
        component.description = f"{component.description}\n\n{hint}"


def requires_approval(tool_name: str) -> bool:
    """Whether a tool must wait for the operator, given the bare or the prefixed name.

    Denies by default, so a tool nobody classified costs an approval card rather than an
    unauthorised fleet change. Deciding from the write list would invert that.
    """
    return tool_name.removeprefix("leitstand_") not in _READ_TOOLS


_ROUTE_MAPS = [
    *(RouteMap(pattern=p, methods=["GET"], mcp_type=MCPType.TOOL) for p in _READ_PATHS),
    *(RouteMap(pattern=p, methods=m, mcp_type=MCPType.TOOL) for p, m in _WRITE_ROUTES),
    # Order matters: first match wins, so this catch-all stays last. Everything not named above is
    # excluded, so a route added later is invisible to the agent until deliberately exposed here.
    RouteMap(pattern=r".*", mcp_type=MCPType.EXCLUDE),
]


class _ForwardCallerCredential(httpx.Auth):
    """Present the asking operator's credential on the loopback.

    The loopback meets the same auth gate a browser does, so it must carry a credential. Forwarding
    the caller's own rather than a service token keeps a tool call attributable to the operator.
    """

    def auth_flow(self, request: httpx.Request):
        token = caller_credential.get()
        if token is not None:
            request.headers["Authorization"] = f"Bearer {token}"
        yield request


class _CallProvenanceMiddleware(Middleware):
    """Make one tool call's approval provenance ambient for exactly the duration of that call.

    Set on the server task that runs the tool, which is the task the loopback re-enters this app on.
    The reset guards against a later call inheriting this one's approver.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        token = tool_call_provenance.set(_provenance_of(context))
        try:
            return await call_next(context)
        finally:
            tool_call_provenance.reset(token)


def _provenance_of(context: MiddlewareContext) -> ToolCallProvenance:
    """Read the agent's envelope off the request metadata, defaulting to unapproved.

    Anything absent or malformed yields no approval, so a call that arrives without a decision
    behind it is audited as autonomous rather than inheriting someone else's approver.
    """
    # Both reads stay guarded: request_context is None until the session opens, and meta is None
    # for a call that sent no metadata.
    request_context = getattr(context.fastmcp_context, "request_context", None)
    meta = getattr(request_context, "meta", None)
    envelope = meta.get(CALL_PROVENANCE_KEY) if isinstance(meta, dict) else None
    if not isinstance(envelope, dict):
        return ToolCallProvenance()
    return ToolCallProvenance(
        tool_call_id=envelope.get(DECISION_ID_KEY),
        approved=bool(envelope.get(APPROVED_KEY)),
        approved_by=envelope.get(APPROVED_BY_KEY),
    )


def build_domain_mcp(app: FastAPI, origin: AgentOrigin) -> tuple[FastMCP, httpx.AsyncClient]:
    """Build the in-process domain MCP server plus the ASGI client it owns.

    ``origin`` is required because a loopback built without it audits the agent's own writes as a
    human acting directly. The caller must close the returned client on shutdown; ``base_url`` is a
    placeholder the ASGI transport never dials, needed only to resolve relative paths.
    """
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=AgentOriginBoundary(app, origin)),
        base_url="http://leitstand-backend.internal",
        auth=_ForwardCallerCredential(),
    )
    # Unmasked so a validation failure reaches the model as something it can correct, and pinned
    # here rather than left to the environment-configurable default. Safe because the loopback
    # reaches only this app's own routes, so the detail never leaves the operator's assistant.
    mcp = FastMCP.from_openapi(
        openapi_spec=app.openapi(),
        client=client,
        name="leitstand-domain",
        route_maps=_ROUTE_MAPS,
        mask_error_details=False,
        mcp_component_fn=_add_agent_hints,
    )
    mcp.add_middleware(_CallProvenanceMiddleware())
    return mcp, client
