"""Bearer authentication, and the credential the in-process tool loopback carries.

A null ``auth_bearer_token`` leaves the API open, which is the dev default. Setting it closes REST
and the WebSocket together: gating REST alone would still hand an unauthenticated client the live
fleet stream.

One shared token is a gate, not an identity, so every authenticated caller is the same operator
until OIDC replaces this.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from contextvars import ContextVar

from fastapi import HTTPException, status
from starlette.datastructures import Headers

from leitstand_backend.domain.user import User
from leitstand_backend.infrastructure.settings import Settings

_BEARER_SCHEME = "bearer"

# Browsers cannot set headers on a WebSocket handshake, so the token rides the one field they can
# control. A query parameter would land in access logs and proxy history.
_WS_TOKEN_PREFIX = "leitstand.bearer."
WS_SUBPROTOCOL = "leitstand.v1"

# What the current caller presented, bound for the whole request. The agent's tools re-enter this
# app through a loopback that meets this same gate, and forwarding the caller's own credential is
# what keeps a tool call attributable to the operator rather than to a service identity.
caller_credential: ContextVar[str | None] = ContextVar("caller_credential", default=None)

# One shared token, so every caller is the same operator until real identity exists.
_SHARED_OPERATOR = User(id="operator", name="Operator")


def bearer_token(authorization: str | None) -> str | None:
    """Return the token from an Authorization header, or None if it carries no bearer."""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != _BEARER_SCHEME:
        return None
    return token.strip() or None


def ws_credential(subprotocols: Sequence[str]) -> str | None:
    """Return the token offered among a WebSocket handshake's subprotocols, if any."""
    for offered in subprotocols:
        if offered.startswith(_WS_TOKEN_PREFIX):
            return offered[len(_WS_TOKEN_PREFIX) :] or None
    return None


def selected_subprotocol(subprotocols: Sequence[str]) -> str | None:
    """Return the subprotocol to accept with.

    A browser drops the connection unless the server echoes one of the protocols it offered, and
    the token-bearing one must never be echoed back.
    """
    return WS_SUBPROTOCOL if WS_SUBPROTOCOL in subprotocols else None


def token_accepted(settings: Settings, token: str | None) -> bool:
    """Whether a presented token satisfies the configured one.

    compare_digest rather than ``==``: this comparison is attacker-controlled and endlessly
    repeatable, which is exactly the setting where ``==`` leaks a token a byte at a time. Compared
    as bytes because headers decode as latin-1, and compare_digest raises on a non-ASCII str rather
    than rejecting it.
    """
    expected = settings.auth_bearer_token
    if expected is None:
        return True
    if token is None:
        return False
    return secrets.compare_digest(
        token.encode("utf-8"), expected.get_secret_value().encode("utf-8")
    )


def authenticate(settings: Settings, authorization: str | None) -> User:
    """Resolve the caller of an HTTP request, raising 401 when the token is missing or wrong."""
    if not token_accepted(settings, bearer_token(authorization)):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "missing or invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _SHARED_OPERATOR


class CredentialContextMiddleware:
    """Bind the caller's credential for the whole request, response streaming included.

    Pure ASGI rather than BaseHTTPMiddleware: a chat turn calls its tools while the response is
    still streaming, long after the endpoint function returned, and this form is the one that is
    still on the stack by then.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = bearer_token(Headers(scope=scope).get("authorization"))
        reset = caller_credential.set(token)
        try:
            await self.app(scope, receive, send)
        finally:
            caller_credential.reset(reset)
