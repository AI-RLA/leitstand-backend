"""Multiplexed WebSocket hub at /ws/v1."""

from __future__ import annotations

import asyncio
import time

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError

from leitstand_backend.adapters.inbound.web.ws.frames import (
    ErrorFrame,
    EventFrame,
    HelloFrame,
    PingFrame,
    PongFrame,
    SubscribeFrame,
    UnsubscribeFrame,
)
from leitstand_backend.infrastructure.auth import (
    selected_subprotocol,
    token_accepted,
    ws_credential,
)
from leitstand_backend.infrastructure.event_bus import EventBus

router = APIRouter()
logger = structlog.get_logger(__name__)

_HEARTBEAT_S = 3.0
_PONG_TIMEOUT_S = 10.0


@router.websocket("/ws/v1")
async def ws_endpoint(ws: WebSocket) -> None:
    offered = ws.scope.get("subprotocols") or []
    if not token_accepted(ws.app.state.settings, ws_credential(offered)):
        # Closing before accept rejects the handshake itself, so an unauthenticated client never
        # reaches the bus. This stream carries the whole fleet's live state.
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await ws.accept(subprotocol=selected_subprotocol(offered))
    await ws.send_json(HelloFrame(version=ws.app.version).model_dump())
    bus: EventBus = ws.app.state.event_bus
    subs: dict[str, asyncio.Task] = {}
    last_pong = time.monotonic()

    async def pump_subscription(topic_prefix: str) -> None:
        queue = bus.subscribe(topic_prefix)
        try:
            while True:
                event = await queue.get()
                frame = EventFrame(topic=event["topic"], payload=event["payload"])
                await ws.send_json(frame.model_dump())
        except (WebSocketDisconnect, RuntimeError):
            return
        finally:
            bus.unsubscribe(topic_prefix, queue)

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(_HEARTBEAT_S)
            if time.monotonic() - last_pong > _PONG_TIMEOUT_S:
                await ws.close(code=1011)
                return
            try:
                await ws.send_json(PingFrame().model_dump())
            except Exception:  # noqa: BLE001
                return

    hb_task = asyncio.create_task(heartbeat())

    try:
        while True:
            raw = await ws.receive_json()
            try:
                t = raw.get("type") if isinstance(raw, dict) else None
                if t == "subscribe":
                    frame = SubscribeFrame.model_validate(raw)
                    if frame.topic in subs:
                        continue
                    subs[frame.topic] = asyncio.create_task(pump_subscription(frame.topic))
                elif t == "unsubscribe":
                    fu = UnsubscribeFrame.model_validate(raw)
                    task = subs.pop(fu.topic, None)
                    if task is not None:
                        task.cancel()
                elif t == "pong":
                    PongFrame.model_validate(raw)
                    last_pong = time.monotonic()
                elif t == "ping":
                    await ws.send_json(PongFrame().model_dump())
                else:
                    raise ValueError(f"unknown frame type: {t!r}")
            except (ValidationError, ValueError) as e:
                err = ErrorFrame(
                    request_id=raw.get("request_id") if isinstance(raw, dict) else None,
                    code="bad_frame",
                    message=str(e),
                )
                await ws.send_json(err.model_dump())
    except WebSocketDisconnect:
        pass
    finally:
        hb_task.cancel()
        for task in subs.values():
            task.cancel()
        await asyncio.gather(hb_task, *subs.values(), return_exceptions=True)
