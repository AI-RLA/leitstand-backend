"""Site CRUD endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from leitstand_backend.adapters.inbound.web.sites.dto import SiteCreate, SiteUpdate, SiteView
from leitstand_backend.adapters.inbound.web.sites.mappers import (
    to_create_command,
    to_delete_command,
    to_site_view,
    to_update_command,
)
from leitstand_backend.domain.errors import SiteInUse, SiteNotFoundError
from leitstand_backend.infrastructure.deps import get_site_management_use_case
from leitstand_backend.ports.inbound.site_management import SiteManagementUseCase

router = APIRouter(prefix="/api/v1/sites", tags=["sites"])


@router.get("/", response_model=list[SiteView])
async def list_sites(
    uc: SiteManagementUseCase = Depends(get_site_management_use_case),
) -> list[SiteView]:
    return [to_site_view(s) for s in await uc.list()]


@router.get("/{site_id}", response_model=SiteView)
async def get_site(
    site_id: UUID,
    uc: SiteManagementUseCase = Depends(get_site_management_use_case),
) -> SiteView:
    site = await uc.get(site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "site not found")
    return to_site_view(site)


@router.post("/", response_model=SiteView, status_code=status.HTTP_201_CREATED)
async def create_site(
    body: SiteCreate,
    uc: SiteManagementUseCase = Depends(get_site_management_use_case),
) -> SiteView:
    site = await uc.create(to_create_command(body))
    return to_site_view(site)


@router.patch("/{site_id}", response_model=SiteView)
async def update_site(
    site_id: UUID,
    body: SiteUpdate,
    uc: SiteManagementUseCase = Depends(get_site_management_use_case),
) -> SiteView:
    try:
        site = await uc.update(to_update_command(site_id, body))
    except SiteNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "site not found")
    return to_site_view(site)


@router.delete("/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_site(
    site_id: UUID,
    uc: SiteManagementUseCase = Depends(get_site_management_use_case),
) -> None:
    try:
        await uc.delete(to_delete_command(site_id))
    except SiteNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "site not found")
    except SiteInUse as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "message": str(exc),
                "blocking_mission_ids": [str(mid) for mid in exc.blocking_mission_ids],
            },
        )
