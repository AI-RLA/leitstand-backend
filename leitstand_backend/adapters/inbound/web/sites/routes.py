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


@router.get("/", response_model=list[SiteView], operation_id="list_sites")
async def list_sites(
    uc: SiteManagementUseCase = Depends(get_site_management_use_case),
) -> list[SiteView]:
    """List the sites, each a named local frame with its anchor position and map reference."""
    return [to_site_view(s) for s in await uc.list()]


@router.get("/{site_id}", response_model=SiteView, operation_id="get_site")
async def get_site(
    site_id: UUID,
    uc: SiteManagementUseCase = Depends(get_site_management_use_case),
) -> SiteView:
    """Return one site's name, anchor position and map reference by its id."""
    site = await uc.get(site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "site not found")
    return to_site_view(site)


@router.post(
    "/", response_model=SiteView, status_code=status.HTTP_201_CREATED, operation_id="create_site"
)
async def create_site(
    body: SiteCreate,
    uc: SiteManagementUseCase = Depends(get_site_management_use_case),
) -> SiteView:
    """Create a site: a named local frame anchored at a GNSS position with a Nav2 map reference."""
    site = await uc.create(to_create_command(body))
    return to_site_view(site)


@router.patch("/{site_id}", response_model=SiteView, operation_id="update_site")
async def update_site(
    site_id: UUID,
    body: SiteUpdate,
    uc: SiteManagementUseCase = Depends(get_site_management_use_case),
) -> SiteView:
    """Change a site's name, anchor position, or map reference. Identify it by site_id."""
    try:
        site = await uc.update(to_update_command(site_id, body))
    except SiteNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "site not found")
    return to_site_view(site)


@router.delete("/{site_id}", status_code=status.HTTP_204_NO_CONTENT, operation_id="delete_site")
async def delete_site(
    site_id: UUID,
    uc: SiteManagementUseCase = Depends(get_site_management_use_case),
) -> None:
    """Delete a site. Fails if any mission still references it. Identify it by site_id."""
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
