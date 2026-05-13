"""Liveness and readiness probes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from leitstand_backend.infrastructure.deps import ping_db

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request, _: None = Depends(ping_db)) -> dict[str, str]:
    # Zenoh check skipped in zenoh_disabled mode.
    settings = request.app.state.settings
    if not settings.zenoh_disabled:
        session = getattr(request.app.state, "zenoh_session", None)
        if session is None:
            raise HTTPException(status_code=503, detail="zenoh session not initialised")
        closed = getattr(session, "is_closed", None)
        if callable(closed):
            try:
                if closed():
                    raise HTTPException(status_code=503, detail="zenoh session is closed")
            except HTTPException:
                raise
            except Exception as e:  # noqa: BLE001
                raise HTTPException(
                    status_code=503, detail=f"zenoh session check failed: {e}"
                ) from e

    return {"status": "ready"}
