"""SiteManagementService - audit-aware CRUD for sites."""

from uuid import UUID

from leitstand_backend.domain.errors import SiteInUse, SiteNotFoundError
from leitstand_backend.domain.model.site import Site
from leitstand_backend.ports.inbound.site_management import (
    CreateSiteCommand,
    DeleteSiteCommand,
    SiteManagementUseCase,
    UpdateSiteCommand,
)
from leitstand_backend.ports.outbound.audit_log import AuditWriter
from leitstand_backend.ports.outbound.mission_repository import MissionRepository
from leitstand_backend.ports.outbound.site_repository import SiteRepository


class SiteManagementService(SiteManagementUseCase):
    def __init__(self, repo: SiteRepository, missions: MissionRepository, audit: AuditWriter):
        self._repo = repo
        self._missions = missions
        self._audit = audit

    async def create(self, command: CreateSiteCommand) -> Site:
        site = await self._repo.create(
            name=command.name,
            anchor_lat=command.anchor_lat,
            anchor_lon=command.anchor_lon,
            anchor_heading_deg=command.anchor_heading_deg,
            nav2_map_ref=command.nav2_map_ref,
            outline=command.outline,
            description=command.description,
        )
        await self._audit(
            "site.create",
            "site",
            str(site.site_id),
            command.model_dump(mode="json"),
        )
        return site

    async def update(self, command: UpdateSiteCommand) -> Site:
        before = await self._repo.get(command.site_id)
        if before is None:
            raise SiteNotFoundError(command.site_id)

        updated = await self._repo.update(
            site_id=command.site_id,
            name=command.name,
            anchor_lat=command.anchor_lat,
            anchor_lon=command.anchor_lon,
            anchor_heading_deg=command.anchor_heading_deg,
            nav2_map_ref=command.nav2_map_ref,
            outline=command.outline,
            description=command.description,
        )
        if updated is None:
            raise SiteNotFoundError(command.site_id)

        await self._audit(
            "site.update",
            "site",
            str(command.site_id),
            {
                "before": before.model_dump(mode="json"),
                "patch": command.model_dump(mode="json", exclude_none=True),
            },
        )
        return updated

    async def delete(self, command: DeleteSiteCommand) -> None:
        """Delete a site nothing has ever driven in.

        Site-local positions are measured against the site's anchor, so any mission that used it,
        archived or not, keeps it. The check asks the mission repository so the site store need
        not know about missions.
        """
        site = await self._repo.get(command.site_id)
        if site is None:
            raise SiteNotFoundError(command.site_id)
        blocking = await self._missions.missions_referencing_site(command.site_id)
        if blocking:
            raise SiteInUse(command.site_id, blocking)
        snapshot = site.model_dump(mode="json")

        await self._audit("site.delete", "site", str(command.site_id), snapshot)

        deleted = await self._repo.delete(command.site_id)
        if not deleted:
            raise SiteNotFoundError(command.site_id)

    async def get(self, site_id: UUID) -> Site | None:
        return await self._repo.get(site_id)

    async def list(self) -> list[Site]:
        return await self._repo.list()
