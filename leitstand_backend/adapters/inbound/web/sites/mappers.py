"""Mappers: wire DTOs <-> domain commands/views for sites."""

from uuid import UUID

from leitstand_backend.adapters.inbound.web.sites.dto import SiteCreate, SiteUpdate, SiteView
from leitstand_backend.domain.model.site import Site
from leitstand_backend.ports.inbound.site_management import (
    CreateSiteCommand,
    DeleteSiteCommand,
    UpdateSiteCommand,
)


def to_create_command(req: SiteCreate) -> CreateSiteCommand:
    return CreateSiteCommand(
        name=req.name,
        anchor_lat=req.anchor_lat,
        anchor_lon=req.anchor_lon,
        anchor_heading_deg=req.anchor_heading_deg,
        nav2_map_ref=req.nav2_map_ref,
        outline=req.outline,
        description=req.description,
    )


def to_update_command(site_id: UUID, req: SiteUpdate) -> UpdateSiteCommand:
    return UpdateSiteCommand(
        site_id=site_id,
        name=req.name,
        anchor_lat=req.anchor_lat,
        anchor_lon=req.anchor_lon,
        anchor_heading_deg=req.anchor_heading_deg,
        nav2_map_ref=req.nav2_map_ref,
        outline=req.outline,
        description=req.description,
    )


def to_delete_command(site_id: UUID) -> DeleteSiteCommand:
    return DeleteSiteCommand(site_id=site_id)


def to_site_view(site: Site) -> SiteView:
    return SiteView(
        site_id=site.site_id,
        name=site.name,
        anchor_lat=site.anchor_lat,
        anchor_lon=site.anchor_lon,
        anchor_heading_deg=site.anchor_heading_deg,
        nav2_map_ref=site.nav2_map_ref,
        outline=site.outline,
        description=site.description,
        created_at=site.created_at,
        updated_at=site.updated_at,
    )
