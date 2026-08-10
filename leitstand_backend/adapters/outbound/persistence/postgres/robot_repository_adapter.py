"""Postgres-backed RobotRepository."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from leitstand_backend.adapters.outbound.persistence.postgres.models import RobotRow
from leitstand_backend.domain.model.robot.robot import Metadata, Robot
from leitstand_backend.ports.outbound.robot_repository import RobotRepository


class PostgresRobotRepositoryAdapter(RobotRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, robot_id: str) -> Robot | None:
        row = await self._session.get(RobotRow, robot_id)
        return _to_domain(row) if row else None

    async def record_online(self, robot_id: str, metadata: Metadata) -> Robot:
        now = datetime.now(timezone.utc)
        meta_dict = metadata.model_dump(mode="json")
        stmt = pg_insert(RobotRow).values(
            id=robot_id,
            metadata_json=meta_dict,
            first_seen_at=now,
            last_seen_at=now,
            online=True,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[RobotRow.id],
            set_={
                "metadata_json": stmt.excluded.metadata_json,
                "last_seen_at": stmt.excluded.last_seen_at,
                "online": True,
            },
        ).returning(RobotRow)
        result = await self._session.execute(stmt)
        row = result.scalar_one()
        return _to_domain(row)

    async def record_offline(self, robot_id: str) -> Robot | None:
        stmt = (
            update(RobotRow).where(RobotRow.id == robot_id).values(online=False).returning(RobotRow)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _to_domain(row) if row else None

    async def list(self) -> list[Robot]:
        stmt = select(RobotRow).order_by(RobotRow.id.asc())
        result = await self._session.execute(stmt)
        return [_to_domain(row) for row in result.scalars()]

    async def mark_all_offline(self) -> None:
        await self._session.execute(update(RobotRow).values(online=False))

    async def save_telemetry(self, robot_id: str, kind: str, payload: dict) -> None:
        col = {
            "pose": RobotRow.last_pose,
            "battery": RobotRow.last_battery,
        }.get(kind)
        if col is None:
            raise ValueError(f"unknown telemetry kind: {kind!r}")
        await self._session.execute(
            update(RobotRow).where(RobotRow.id == robot_id).values({col: payload})
        )


def _to_domain(row: RobotRow) -> Robot:
    return Robot(
        id=row.id,
        metadata=Metadata.model_validate(row.metadata_json),
        online=row.online,
        last_seen=row.last_seen_at,
    )
