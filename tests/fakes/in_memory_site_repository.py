"""In-memory SiteRepository fake (test-only)."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from uuid import UUID, uuid4

from geojson_pydantic import Polygon

from leitstand_backend.domain.errors import SiteInUse
from leitstand_backend.domain.model.site import Site
from leitstand_backend.ports.outbound.site_repository import SiteRepository


class InMemorySiteRepository(SiteRepository):
    def __init__(self) -> None:
        self._sites: dict[UUID, Site] = {}
        self._in_use: set[UUID] = set()
        self._lock = threading.Lock()

    async def list(self) -> list[Site]:
        with self._lock:
            return sorted(self._sites.values(), key=lambda s: s.created_at, reverse=True)

    async def get(self, site_id: UUID) -> Site | None:
        with self._lock:
            return self._sites.get(site_id)

    async def create(
        self,
        name: str,
        anchor_lat: float,
        anchor_lon: float,
        anchor_heading_deg: float,
        nav2_map_ref: str,
        outline: Polygon | None,
        description: str | None,
    ) -> Site:
        now = datetime.now(timezone.utc)
        site = Site(
            site_id=uuid4(),
            name=name,
            anchor_lat=anchor_lat,
            anchor_lon=anchor_lon,
            anchor_heading_deg=anchor_heading_deg,
            nav2_map_ref=nav2_map_ref,
            outline=outline,
            description=description,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._sites[site.site_id] = site
        return site

    async def update(
        self,
        site_id: UUID,
        name: str | None = None,
        anchor_lat: float | None = None,
        anchor_lon: float | None = None,
        anchor_heading_deg: float | None = None,
        nav2_map_ref: str | None = None,
        outline: Polygon | None = None,
        description: str | None = None,
    ) -> Site | None:
        with self._lock:
            current = self._sites.get(site_id)
            if current is None:
                return None
            updated = current.model_copy(
                update={
                    "name": name if name is not None else current.name,
                    "anchor_lat": anchor_lat if anchor_lat is not None else current.anchor_lat,
                    "anchor_lon": anchor_lon if anchor_lon is not None else current.anchor_lon,
                    "anchor_heading_deg": (
                        anchor_heading_deg
                        if anchor_heading_deg is not None
                        else current.anchor_heading_deg
                    ),
                    "nav2_map_ref": (
                        nav2_map_ref if nav2_map_ref is not None else current.nav2_map_ref
                    ),
                    "outline": outline if outline is not None else current.outline,
                    "description": (
                        description if description is not None else current.description
                    ),
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self._sites[site_id] = updated
            return updated

    async def delete(self, site_id: UUID) -> bool:
        with self._lock:
            if site_id in self._in_use:
                raise SiteInUse(site_id)
            return self._sites.pop(site_id, None) is not None

    # Test-only inspection helpers ------------------------------------------------

    def mark_in_use(self, site_id: UUID) -> None:
        with self._lock:
            self._in_use.add(site_id)

    def seed(self, site_id: UUID) -> Site:
        """Insert a minimal site with a chosen id (so missions can reference it)."""
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        site = Site(
            site_id=site_id,
            name=f"site-{site_id}",
            anchor_lat=0.0,
            anchor_lon=0.0,
            anchor_heading_deg=0.0,
            nav2_map_ref="map",
            outline=None,
            description=None,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._sites[site_id] = site
        return site
