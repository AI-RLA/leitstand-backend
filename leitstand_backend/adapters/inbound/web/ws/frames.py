"""WebSocket frame schemas (envelopes for /ws/v1)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class SubscribeFrame(BaseModel):
    type: Literal["subscribe"] = "subscribe"
    topic: str
    request_id: str | None = None


class UnsubscribeFrame(BaseModel):
    type: Literal["unsubscribe"] = "unsubscribe"
    topic: str
    request_id: str | None = None


class PingFrame(BaseModel):
    type: Literal["ping"] = "ping"


class PongFrame(BaseModel):
    type: Literal["pong"] = "pong"


class EventFrame(BaseModel):
    type: Literal["event"] = "event"
    topic: str
    payload: Any


class ErrorFrame(BaseModel):
    type: Literal["error"] = "error"
    request_id: str | None = None
    code: str
    message: str


class HelloFrame(BaseModel):
    type: Literal["hello"] = "hello"
    version: str
