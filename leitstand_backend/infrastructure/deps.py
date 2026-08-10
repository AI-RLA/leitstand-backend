"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from leitstand_backend.adapters.outbound.persistence.postgres.audit_log_adapter import (
    PostgresAuditLogAdapter,
)
from leitstand_backend.adapters.outbound.persistence.postgres.field_repository_adapter import (
    PostgresFieldRepositoryAdapter,
)
from leitstand_backend.adapters.outbound.persistence.postgres.mission_repository_adapter import (
    PostgresMissionRepositoryAdapter,
)
from leitstand_backend.adapters.outbound.persistence.postgres.robot_repository_adapter import (
    PostgresRobotRepositoryAdapter,
)
from leitstand_backend.adapters.outbound.persistence.postgres.site_repository_adapter import (
    PostgresSiteRepositoryAdapter,
)
from leitstand_backend.application.field_management_service import FieldManagementService
from leitstand_backend.application.fleet_view_service import FleetViewService
from leitstand_backend.application.mission_management_service import MissionManagementService
from leitstand_backend.application.site_management_service import SiteManagementService
from leitstand_backend.domain.errors import MissionDispatchTimeout, MissionRejectedByRobot
from leitstand_backend.domain.model.mission.mission import Mission
from leitstand_backend.domain.user import DUMMY_OPERATOR, User
from leitstand_backend.infrastructure.db import (
    ping,
    run_after_commit_callbacks,
    transactional_scope,
)
from leitstand_backend.infrastructure.event_bus import EventBus
from leitstand_backend.infrastructure.transactional_events import TransactionBoundEventPublisher
from leitstand_backend.ports.inbound.field_management import FieldManagementUseCase
from leitstand_backend.ports.inbound.fleet_view import FleetViewUseCase
from leitstand_backend.ports.inbound.mission_management import (
    DispatchMissionCommand,
    MissionManagementUseCase,
)
from leitstand_backend.ports.inbound.site_management import SiteManagementUseCase
from leitstand_backend.ports.outbound.audit_log import AuditWriter
from leitstand_backend.ports.outbound.event_publisher import EventPublisher
from leitstand_backend.ports.outbound.field_repository import FieldRepository
from leitstand_backend.ports.outbound.mission_dispatcher import MissionDispatcher
from leitstand_backend.ports.outbound.mission_repository import MissionRepository
from leitstand_backend.ports.outbound.robot_factsheet_view import RobotFactsheetView
from leitstand_backend.ports.outbound.robot_repository import RobotRepository
from leitstand_backend.ports.outbound.robot_state_view import RobotStateView
from leitstand_backend.ports.outbound.site_repository import SiteRepository


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


def _make_audit_writer(session: AsyncSession, current_user: User | None) -> AuditWriter:
    """Build an audit writer bound to ``session`` for ``current_user``."""
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


def get_audit_writer(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> AuditWriter:
    """Audit writer bound to the request's main session.

    Audit and main op share one transaction. The boundary commits both
    atomically on clean exit, rolls both back on exception. This
    eliminates the split-brain failure mode where audit row outlives a
    rolled-back operation, or vice versa.
    """
    return _make_audit_writer(session, current_user)


def get_event_bus(request: Request) -> EventBus:
    return request.app.state.event_bus


def get_transactional_event_publisher(
    session: AsyncSession = Depends(get_db_session),
    bus: EventBus = Depends(get_event_bus),
) -> EventPublisher:
    """Provide an EventPublisher that defers publishes until the request transaction commits.

    Bound to the request session (FastAPI dedupes ``get_db_session``, so it shares the session the
    repositories and audit writer use); the deferred events are released by that session's
    ``transactional_scope`` after commit.
    """
    return TransactionBoundEventPublisher(session, bus)


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
    return FleetViewService(
        repo=PostgresRobotRepositoryAdapter(session),
        state_view=state_view,
        missions=PostgresMissionRepositoryAdapter(session),
    )


async def get_mission_repository(
    session: AsyncSession = Depends(get_db_session),
) -> MissionRepository:
    return PostgresMissionRepositoryAdapter(session)


async def get_site_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SiteRepository:
    return PostgresSiteRepositoryAdapter(session)


def get_mission_dispatcher(request: Request) -> MissionDispatcher:
    return request.app.state.mission_dispatcher


def get_factsheet_view(request: Request) -> RobotFactsheetView:
    return request.app.state.factsheet_view


def get_mission_management_use_case(
    repo: MissionRepository = Depends(get_mission_repository),
    dispatcher: MissionDispatcher = Depends(get_mission_dispatcher),
    factsheets: RobotFactsheetView = Depends(get_factsheet_view),
    events: EventPublisher = Depends(get_transactional_event_publisher),
    audit: AuditWriter = Depends(get_audit_writer),
    sites: SiteRepository = Depends(get_site_repository),
) -> MissionManagementUseCase:
    return MissionManagementService(
        repo=repo,
        dispatcher=dispatcher,
        factsheets=factsheets,
        events=events,
        audit=audit,
        sites=sites,
    )


class _DispatchOrchestrator:
    """Runs mission dispatch in a dedicated transaction.

    Dispatch waits on a robot RPC (Zenoh queryable, up to a timeout); a failure
    (reject / timeout) marks the mission FAILED. That FAILED write must survive the
    error propagating to the route, so dispatch cannot share the request's
    transaction (which rolls back on the raised error). This owns its own session
    and commits on both the success and failure paths before re-raising.

    The transaction is held across the robot RPC; acceptable at fleet scale (a
    timeout is the only slow case, and dispatch is operator-initiated). The
    connection-releasing variant is a documented follow-up.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        dispatcher: MissionDispatcher,
        factsheets: RobotFactsheetView,
        bus: EventBus,
        current_user: User,
    ) -> None:
        self._session_factory = session_factory
        self._dispatcher = dispatcher
        self._factsheets = factsheets
        self._bus = bus
        self._current_user = current_user

    async def dispatch(self, command: DispatchMissionCommand) -> Mission:
        async with self._session_factory() as session:
            service = MissionManagementService(
                repo=PostgresMissionRepositoryAdapter(session),
                dispatcher=self._dispatcher,
                factsheets=self._factsheets,
                events=TransactionBoundEventPublisher(session, self._bus),
                audit=_make_audit_writer(session, self._current_user),
                sites=PostgresSiteRepositoryAdapter(session),
            )
            try:
                mission = await service.dispatch(command)
            except (MissionRejectedByRobot, MissionDispatchTimeout):
                # service.dispatch already moved the row to FAILED in this session;
                # commit so the failure is durable, then re-raise for the HTTP mapping.
                await session.commit()
                run_after_commit_callbacks(session)
                raise
            except Exception:
                await session.rollback()
                raise
            await session.commit()
            run_after_commit_callbacks(session)
            return mission


def get_dispatch_use_case(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> _DispatchOrchestrator:
    return _DispatchOrchestrator(
        session_factory=request.app.state.session_factory,
        dispatcher=request.app.state.mission_dispatcher,
        factsheets=request.app.state.factsheet_view,
        bus=request.app.state.event_bus,
        current_user=current_user,
    )


def get_site_management_use_case(
    repo: SiteRepository = Depends(get_site_repository),
    audit: AuditWriter = Depends(get_audit_writer),
) -> SiteManagementUseCase:
    return SiteManagementService(repo=repo, audit=audit)
