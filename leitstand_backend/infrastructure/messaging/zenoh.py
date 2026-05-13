"""Zenoh session lifecycle helper."""

import json
from contextlib import asynccontextmanager

import zenoh

from leitstand_backend.infrastructure.settings import Settings


@asynccontextmanager
async def zenoh_session(settings: Settings):
    if settings.zenoh_config:
        cfg = zenoh.Config.from_file(settings.zenoh_config)
    else:
        cfg = zenoh.Config()
        cfg.insert_json5("mode", '"client"')
        cfg.insert_json5("connect/endpoints", json.dumps([settings.zenoh_endpoint]))
        cfg.insert_json5("scouting/multicast/enabled", "false")
    session = zenoh.open(cfg)
    try:
        yield session
    finally:
        session.close()
