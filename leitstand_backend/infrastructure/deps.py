"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from leitstand_backend.adapters.outbound.persistence.postgres.audit_log_adapter import (
    PostgresAuditLogAdapter,
)
from leitstand_backend.adapters.outbound.persistence.postgres.field_repository_adapter import (
    PostgresFieldRepositoryAdapter,
)
from leitstand_backend.adapters.outbound.persistence.postgres.robot_repository_adapter import (
    PostgresRobotRepositoryAdapter,
)
from leitstand_backend.application.field_management_service import FieldManagementService
from leitstand_backend.application.fleet_view_service import FleetViewService
from leitstand_backend.domain.user import DUMMY_OPERATOR, User
from leitstand_backend.infrastructure.db import ping, transactional_scope
from leitstand_backend.infrastructure.event_bus import EventBus
from leitstand_backend.ports.inbound.field_management import FieldManagementUseCase
from leitstand_backend.ports.inbound.fleet_view import FleetViewUseCase
from leitstand_backend.ports.outbound.audit_log import AuditWriter
from leitstand_backend.ports.outbound.field_repository import FieldRepository
from leitstand_backend.ports.outbound.robot_repository import RobotRepository
from leitstand_backend.ports.outbound.robot_state_view import RobotStateView


def get_current_user() -> User:
    return DUMMY_OPERATOR


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped AsyncSession with boundary-owned transaction.

    Commit on clean exit, rollback on exception. FastAPI dedupes this
    dependency per-request — get_audit_writer + get_field_repository
    receive the same session, so audit + main op are atomic.
    """
    factory = request.app.state.session_factory
    async with transactional_scope(factory) as session:
        yield session


async def get_robot_repository(
    session: AsyncSession = Depends(get_db_session),
) -> RobotRepository:
    return PostgresRobotRepositoryAdapter(session)


async def get_field_repository(
    session: AsyncSession = Depends(get_db_session),
) -> FieldRepository:
    return PostgresFieldRepositoryAdapter(session)


def get_audit_writer(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> AuditWriter:
    """Audit writer bound to the request's main session.

    Audit and main op share one transaction. The boundary commits both
    atomically on clean exit, rolls both back on exception. This
    eliminates the split-brain failure mode where audit row outlives a
    rolled-back operation, or vice versa. See ADR 0017 Transactions
    addendum.
    """
    user_id_str = str(current_user.id) if current_user is not None else None
    audit = PostgresAuditLogAdapter(session)

    async def write(
        action: str,
        target_type: str | None,
        target_id: str | None,
        payload: dict | None,
    ) -> None:
        await audit.append(
            action=action,
            user_id=user_id_str,
            target_type=target_type,
            target_id=target_id,
            payload=payload,
        )

    return write


def get_event_bus(request: Request) -> EventBus:
    return request.app.state.event_bus


def get_state_view(request: Request) -> RobotStateView:
    return request.app.state.state_view


async def ping_db(request: Request) -> None:
    await ping(request.app.state.engine)


def get_field_management_use_case(
    repo: FieldRepository = Depends(get_field_repository),
    audit: AuditWriter = Depends(get_audit_writer),
) -> FieldManagementUseCase:
    return FieldManagementService(repo=repo, audit=audit)


def get_fleet_view_use_case(
    session: AsyncSession = Depends(get_db_session),
    state_view: RobotStateView = Depends(get_state_view),
) -> FleetViewUseCase:
    repo = PostgresRobotRepositoryAdapter(session)
    return FleetViewService(repo=repo, state_view=state_view)
