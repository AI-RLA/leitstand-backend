"""Composition root: build the FastAPI app."""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from importlib.metadata import version as _pkg_version
from pathlib import Path
from uuid import UUID

import httpx
import structlog
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from geojson_pydantic import Polygon
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from leitstand_backend.adapters.inbound.messaging.zenoh.mission.mission_state_adapter import (
    ZenohMissionStateAdapter,
)
from leitstand_backend.adapters.inbound.messaging.zenoh.robot.robot_connectivity_adapter import (
    ZenohRobotConnectivityAdapter,
)
from leitstand_backend.adapters.inbound.messaging.zenoh.robot.robot_factsheet_adapter import (
    ZenohRobotFactsheetAdapter,
)
from leitstand_backend.adapters.inbound.messaging.zenoh.robot.robot_telemetry_adapter import (
    ZenohRobotTelemetryAdapter,
)
from leitstand_backend.adapters.inbound.web.chat.routes import router as chat_router
from leitstand_backend.adapters.inbound.web.coverage.routes import router as coverage_router
from leitstand_backend.adapters.inbound.web.fields.routes import router as fields_router
from leitstand_backend.adapters.inbound.web.missions.routes import router as missions_router
from leitstand_backend.adapters.inbound.web.robots.routes import router as robots_router
from leitstand_backend.adapters.inbound.web.runs.routes import router as runs_router
from leitstand_backend.adapters.inbound.web.shared.health import router as health_router
from leitstand_backend.adapters.inbound.web.sites.routes import router as sites_router
from leitstand_backend.adapters.inbound.web.users.routes import router as users_router
from leitstand_backend.adapters.inbound.web.ws.routes import router as ws_router
from leitstand_backend.adapters.outbound.llm.agent_factory import (
    build_chat_agent,
    system_prompt_version,
)
from leitstand_backend.adapters.outbound.llm.domain_mcp import build_domain_mcp
from leitstand_backend.adapters.outbound.messaging.zenoh.mission.mission_dispatcher_adapter import (
    ZenohMissionDispatcherAdapter,
)
from leitstand_backend.adapters.outbound.persistence.postgres.mission_run_repository_adapter import (
    PostgresMissionRunRepositoryAdapter,
)
from leitstand_backend.adapters.outbound.persistence.postgres.models import RobotRow
from leitstand_backend.adapters.outbound.persistence.postgres.robot_repository_adapter import (
    PostgresRobotRepositoryAdapter,
)
from leitstand_backend.adapters.outbound.planning.http_coverage_planner_adapter import (
    HttpCoveragePlannerAdapter,
)
from leitstand_backend.application.mission_state_service import MissionStateService
from leitstand_backend.application.robot_connectivity_service import RobotConnectivityService
from leitstand_backend.application.robot_factsheet_service import RobotFactsheetService
from leitstand_backend.application.robot_telemetry_service import RobotTelemetryService
from leitstand_backend.application.run_reconciliation import reconcile_robot_runs
from leitstand_backend.domain.errors import CoveragePlannerUnavailable
from leitstand_backend.domain.model.mission.coverage import CoverageParams, CoveragePlan
from leitstand_backend.domain.model.mission.mission import Stage
from leitstand_backend.domain.model.mission.mission_dispatch import CancelMode
from leitstand_backend.infrastructure.auth import CredentialContextMiddleware
from leitstand_backend.infrastructure.db import (
    create_engine,
    create_session_factory,
    ping,
    transactional_scope,
)
from leitstand_backend.infrastructure.deps import get_current_user
from leitstand_backend.infrastructure.event_bus import EventBus
from leitstand_backend.infrastructure.factsheet_view import EventBusBackedRobotFactsheetView
from leitstand_backend.infrastructure.messaging.zenoh import zenoh_session
from leitstand_backend.infrastructure.middleware import RequestIdMiddleware
from leitstand_backend.infrastructure.provenance import AgentOrigin
from leitstand_backend.infrastructure.robot_status_projector import (
    RobotStatusProjector,
    SessionScopedRobotStatusReadModel,
)
from leitstand_backend.infrastructure.settings import Settings
from leitstand_backend.infrastructure.state_view import EventBusBackedRobotStateView
from leitstand_backend.infrastructure.transactional_events import TransactionBoundEventPublisher
from leitstand_backend.logging_setup import configure_logging
from leitstand_backend.ports.inbound.mission_state import (
    HandleRobotOfflineCommand,
    MissionStateUseCase,
)
from leitstand_backend.ports.inbound.robot_connectivity import (
    RecordOfflineCommand,
    RecordOnlineCommand,
    RobotConnectivityUseCase,
)
from leitstand_backend.ports.inbound.robot_factsheet import RobotFactsheetUseCase
from leitstand_backend.ports.outbound.coverage_planner import CoveragePlanner
from leitstand_backend.ports.outbound.event_publisher import robot_topic
from leitstand_backend.ports.outbound.mission_dispatcher import MissionDispatcher

logger = structlog.get_logger(__name__)


async def _db_unavailable(request: Request, exc: Exception) -> JSONResponse:
    logger.warning(
        "database_unavailable",
        path=request.url.path,
        method=request.method,
        error_class=type(exc).__name__,
    )
    return JSONResponse(
        status_code=503,
        content={"detail": "database unavailable"},
    )


def alembic_url_option(database_url: str) -> str:
    """Escape a URL for alembic's ini parser, which interpolates `%`; an encoded password has them."""
    return database_url.replace("%", "%%")


def _run_migrations(database_url: str) -> None:
    """Run alembic upgrade head against the configured DB."""
    ini = Path(__file__).resolve().parents[2] / "migrations" / "alembic.ini"
    cfg = AlembicConfig(str(ini))
    # env.py takes the URL from here, so the app's settings win over the ones it would build.
    cfg.attributes["database_url"] = database_url
    # The ini's logging config would replace the process's own and silence uvicorn's errors.
    cfg.attributes["configure_logger"] = False
    alembic_command.upgrade(cfg, "head")


# How long a reconnected robot has to mention the runs it holds before the backend treats it
# as not having them. The client republishes state every few seconds while executing, so
# this is many heartbeats.
_RECONCILE_GRACE_S = 30.0


async def _bulk_mark_offline(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Mark every robot offline before Zenoh subscribe."""
    async with transactional_scope(session_factory) as session:
        repo = PostgresRobotRepositoryAdapter(session)
        await repo.mark_all_offline()


async def _seed_event_bus_from_db(
    session_factory: async_sessionmaker[AsyncSession], bus: EventBus
) -> None:
    """Re-publish last-known telemetry snapshots as latched events."""
    async with transactional_scope(session_factory) as session:
        result = await session.execute(select(RobotRow))
        rows = result.scalars().all()
    for row in rows:
        for kind, payload in [
            ("pose", row.last_pose),
            ("battery", row.last_battery),
            # Re-latching the factsheet is what lets dispatch validate against a robot that has
            # not reconnected since this process started; the view reads the latch, not the row.
            ("factsheet", row.factsheet_json),
        ]:
            if payload is not None:
                bus.publish(robot_topic(row.id, kind), payload, latch=True)
    logger.info("event_bus_seeded_from_db", robots=len(rows))


class _NullMissionDispatcher(MissionDispatcher):
    """No-op dispatcher used when Zenoh is disabled (dev/test)."""

    async def dispatch(self, run_id: UUID, stages: list[Stage], robot_id: str) -> None:
        pass

    async def cancel(
        self,
        run_id: UUID,
        robot_id: str,
        mode: CancelMode = CancelMode.GRACEFUL,
    ) -> None:
        pass

    async def pause(self, run_id: UUID, robot_id: str) -> None:
        pass

    async def resume(self, run_id: UUID, robot_id: str) -> None:
        pass


class _NullCoveragePlanner(CoveragePlanner):
    """Refuse every plan, so the route answers 503 until a planner is deployed.

    Coverage planning is a soft dependency in the same sense the model endpoint is: its absence
    must cost the operator the planning route and nothing else.
    """

    async def plan(self, boundary: Polygon, params: CoverageParams) -> CoveragePlan:
        raise CoveragePlannerUnavailable()


def _build_coverage_planner(
    settings: Settings,
) -> tuple[CoveragePlanner, httpx.AsyncClient | None]:
    """Return the configured planner and the client it borrows, or one that refuses.

    Constructed without contacting anything: the service is reached at planning time, so a planner
    that is down costs a planning request and never a boot. The client is returned rather than
    owned, so one connection pool serves the process and the caller closes it on shutdown.
    """
    if not settings.coverage_planner_url:
        logger.info("coverage_planner_unconfigured")
        return _NullCoveragePlanner(), None
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(
            settings.coverage_planner_read_timeout_s,
            connect=settings.coverage_planner_connect_timeout_s,
        )
    )
    return HttpCoveragePlannerAdapter(settings.coverage_planner_url, client), client


class _SessionScopedMissionStateUseCase(MissionStateUseCase):
    """Wraps MissionStateService so each call opens its own DB session.

    Called from the Zenoh mission-state subscriber thread (via
    run_coroutine_threadsafe) and from the connectivity adapter on
    robot-offline events; concurrent callers must not share session state.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        bus: EventBus,
    ) -> None:
        self._session_factory = session_factory
        self._bus = bus

    async def record(self, command) -> None:
        async with transactional_scope(self._session_factory) as s:
            await MissionStateService(
                runs=PostgresMissionRunRepositoryAdapter(s),
                events=TransactionBoundEventPublisher(s, self._bus),
            ).record(command)

    async def handle_robot_offline(self, command) -> None:
        async with transactional_scope(self._session_factory) as s:
            await MissionStateService(
                runs=PostgresMissionRunRepositoryAdapter(s),
                events=TransactionBoundEventPublisher(s, self._bus),
            ).handle_robot_offline(command)


class _SessionScopedFactsheetUseCase(RobotFactsheetUseCase):
    """Wraps RobotFactsheetService so each call opens its own DB session.

    Called from the Zenoh factsheet adapter's executor thread when a robot comes online, so
    concurrent arrivals must not share session state.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        bus: EventBus,
    ) -> None:
        self._session_factory = session_factory
        self._bus = bus

    async def record(self, command) -> None:
        async with transactional_scope(self._session_factory) as s:
            repo = PostgresRobotRepositoryAdapter(s)
            await RobotFactsheetService(
                repo=repo, events=TransactionBoundEventPublisher(s, self._bus)
            ).record(command)


class _SessionScopedConnectivityUseCase(RobotConnectivityUseCase):
    """Wraps the connectivity service so each call opens its own DB session.

    The Zenoh-thread bridge calls these methods via
    run_coroutine_threadsafe; concurrent calls must not share session
    state. Per-call session is the safe pattern.

    Side effect: starts/stops per-robot telemetry adapters tied to
    online/offline transitions.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        bus: EventBus,
        z_session,
        telemetry_use_case: RobotTelemetryService,
        loop: asyncio.AbstractEventLoop,
        factsheet_adapter: ZenohRobotFactsheetAdapter | None = None,
        mission_state_uc: MissionStateUseCase | None = None,
        mission_dispatcher: MissionDispatcher | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._bus = bus
        self._z_session = z_session
        self._telemetry_uc = telemetry_use_case
        self._loop = loop
        self._factsheet_adapter = factsheet_adapter
        self._mission_state_uc = mission_state_uc
        self._mission_dispatcher = mission_dispatcher
        self._data_adapters: dict[str, ZenohRobotTelemetryAdapter] = {}
        self._adapters_lock = threading.Lock()
        self._reconcile_tasks: set[asyncio.Task] = set()

    async def record_online(self, command: RecordOnlineCommand):
        since = datetime.now(timezone.utc)
        async with transactional_scope(self._session_factory) as s:
            repo = PostgresRobotRepositoryAdapter(s)
            events = TransactionBoundEventPublisher(s, self._bus)
            service = RobotConnectivityService(repo=repo, events=events)
            robot = await service.record_online(command)
            if self._mission_dispatcher is not None and command.claim_reported:
                # The robot said which run it holds, so the others are settled here and now. A
                # failure here is logged rather than raised: the robot is online either way.
                try:
                    await reconcile_robot_runs(
                        PostgresMissionRunRepositoryAdapter(s),
                        self._mission_dispatcher,
                        events,
                        command.robot_id,
                        since,
                        claimed_run_id=command.active_run_id,
                        claim_reported=True,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("run_reconciliation_failed", robot_id=command.robot_id)
        with self._adapters_lock:
            if command.robot_id not in self._data_adapters:
                adapter = ZenohRobotTelemetryAdapter(
                    session=self._z_session,
                    robot_id=command.robot_id,
                    telemetry_use_case=self._telemetry_uc,
                    loop=self._loop,
                )
                adapter.start()
                self._data_adapters[command.robot_id] = adapter
        if self._factsheet_adapter is not None:
            asyncio.get_running_loop().run_in_executor(
                None, self._factsheet_adapter.fetch_and_record, command.robot_id
            )
        if self._mission_dispatcher is not None and not command.claim_reported:
            # A client that does not answer the claim yet gets the grace window instead.
            task = asyncio.get_running_loop().create_task(
                self._reconcile_when_settled(command.robot_id, since)
            )
            self._reconcile_tasks.add(task)
            task.add_done_callback(self._reconcile_tasks.discard)
        return robot

    async def _reconcile_when_settled(self, robot_id: str, since: datetime) -> None:
        """Give a robot without a claim time to report its runs, then settle the ones it did not."""
        try:
            await asyncio.sleep(_RECONCILE_GRACE_S)
            async with transactional_scope(self._session_factory) as s:
                await reconcile_robot_runs(
                    PostgresMissionRunRepositoryAdapter(s),
                    self._mission_dispatcher,
                    TransactionBoundEventPublisher(s, self._bus),
                    robot_id,
                    since,
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a background sweep must not take the loop down
            logger.exception("run_reconciliation_failed", robot_id=robot_id)

    async def record_offline(self, command: RecordOfflineCommand):
        async with transactional_scope(self._session_factory) as s:
            repo = PostgresRobotRepositoryAdapter(s)
            service = RobotConnectivityService(
                repo=repo, events=TransactionBoundEventPublisher(s, self._bus)
            )
            robot = await service.record_offline(command)
            if robot is not None:
                for kind in ("pose", "battery"):
                    payload = self._bus.latched(robot_topic(command.robot_id, kind))
                    if payload is not None:
                        await repo.save_telemetry(command.robot_id, kind, payload)
        if self._mission_state_uc is not None:
            await self._mission_state_uc.handle_robot_offline(
                HandleRobotOfflineCommand(robot_id=command.robot_id)
            )
        with self._adapters_lock:
            adapter = self._data_adapters.pop(command.robot_id, None)
        if adapter is not None:
            # Run in a thread so Zenoh subscriber teardown (sub.undeclare calls)
            # does not block the asyncio loop while the router is simultaneously
            # forwarding liveliness DELETE events for other robots going offline.
            asyncio.get_running_loop().run_in_executor(None, adapter.close)
        return robot

    async def aclose(self) -> None:
        """Stop the telemetry adapters and any reconcile still waiting out its grace window."""
        self.shutdown()
        tasks = list(self._reconcile_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def shutdown(self) -> None:
        with self._adapters_lock:
            pending = list(self._data_adapters.values())
            self._data_adapters.clear()
        for adapter in pending:
            try:
                adapter.close()
            except Exception:  # noqa: BLE001
                logger.warning("data_adapter_close_error")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # The one place the running combination is written down, for a look at a partner's logs.
        logger.info(
            "backend_starting",
            backend=_pkg_version("leitstand-backend"),
            robot_contract=_pkg_version("leitstand-robot-contract"),
        )
        engine: AsyncEngine = create_engine(settings)
        session_factory = create_session_factory(engine)
        bus = EventBus()
        state_view = EventBusBackedRobotStateView(bus)
        factsheet_view = EventBusBackedRobotFactsheetView(bus)

        if settings.auto_migrate or not settings.zenoh_disabled:
            try:
                await ping(engine)
            except OperationalError as e:
                logger.error(
                    "dependency_unreachable",
                    dependency="postgres",
                    host=engine.url.host,
                    port=engine.url.port,
                    database=engine.url.database,
                    error_class=type(e).__name__,
                    error=str(e.orig) if e.orig else str(e),
                )
                raise SystemExit(1) from None

        if settings.auto_migrate:
            await asyncio.to_thread(_run_migrations, settings.database_url_str)
            logger.info("migrations_applied")

        # Seeding needs a database, not Zenoh, so it shares the ping's condition.
        # A persisted factsheet has to be readable before dispatch either way.
        if settings.auto_migrate or not settings.zenoh_disabled:
            await _seed_event_bus_from_db(session_factory, bus)

        # Marking offline is Zenoh's concern: with no subscriber to follow, the flags would lie.
        if not settings.zenoh_disabled:
            await _bulk_mark_offline(session_factory)

        loop = asyncio.get_running_loop()

        mission_dispatcher: MissionDispatcher = _NullMissionDispatcher()
        connectivity_uc: _SessionScopedConnectivityUseCase | None = None
        connectivity_adapter: ZenohRobotConnectivityAdapter | None = None
        mission_state_adapter: ZenohMissionStateAdapter | None = None
        robot_status_projector: RobotStatusProjector | None = None
        z_ctx = None
        z_session = None

        if settings.zenoh_disabled:
            logger.warning("zenoh_disabled_active")
        else:
            z_ctx = zenoh_session(settings)
            try:
                z_session = await z_ctx.__aenter__()
            except Exception as e:  # noqa: BLE001 - Zenoh raises generic exceptions
                logger.error(
                    "dependency_unreachable",
                    dependency="zenoh",
                    endpoint=settings.zenoh_endpoint,
                    error_class=type(e).__name__,
                    error=str(e),
                )
                raise SystemExit(1) from None

            telemetry_uc = RobotTelemetryService(events=bus)
            factsheet_uc = _SessionScopedFactsheetUseCase(session_factory, bus)
            mission_state_uc = _SessionScopedMissionStateUseCase(session_factory, bus)
            mission_dispatcher = ZenohMissionDispatcherAdapter(z_session)
            factsheet_adapter = ZenohRobotFactsheetAdapter(z_session, factsheet_uc, loop)
            mission_state_adapter = ZenohMissionStateAdapter(z_session, mission_state_uc, loop)
            mission_state_adapter.start()
            connectivity_uc = _SessionScopedConnectivityUseCase(
                session_factory=session_factory,
                bus=bus,
                z_session=z_session,
                telemetry_use_case=telemetry_uc,
                loop=loop,
                factsheet_adapter=factsheet_adapter,
                mission_state_uc=mission_state_uc,
                mission_dispatcher=mission_dispatcher,
            )
            # Start the projector and latch initial statuses before connectivity, so it
            # does not miss the liveliness online burst replayed on subscribe.
            robot_status_projector = RobotStatusProjector(
                subscriber=bus,
                publisher=bus,
                read_model=SessionScopedRobotStatusReadModel(session_factory, state_view),
                loop=loop,
            )
            robot_status_projector.start()
            await robot_status_projector.recompute_all()
            connectivity_adapter = ZenohRobotConnectivityAdapter(
                session=z_session,
                use_case=connectivity_uc,
                loop=loop,
            )
            connectivity_adapter.start()

        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.zenoh_session = z_session
        app.state.event_bus = bus
        app.state.state_view = state_view
        app.state.factsheet_view = factsheet_view
        app.state.mission_dispatcher = mission_dispatcher
        app.state.coverage_planner, coverage_planner_client = _build_coverage_planner(settings)
        app.state.connectivity_uc = connectivity_uc
        app.state.connectivity_adapter = connectivity_adapter
        app.state.robot_status_projector = robot_status_projector

        # Nothing here dials the model: the agent is built from configuration alone, so an
        # unreachable model on its separate box can never fail startup.
        app.state.chat_agent = None
        chat_mcp_client = None
        if settings.chat_enabled:
            try:
                origin = AgentOrigin(
                    model=settings.llm_model, prompt_version=system_prompt_version()
                )
                domain_mcp, chat_mcp_client = build_domain_mcp(app, origin)
                app.state.chat_agent = build_chat_agent(settings, domain_mcp)
                logger.info(
                    "chat_enabled", model=settings.llm_model, base_url=settings.llm_base_url
                )
            except Exception:  # noqa: BLE001 - chat must never take the backend down
                logger.exception("chat_init_failed_continuing_without_chat")
                app.state.chat_agent = None
        else:
            logger.info("chat_disabled")

        logger.info("leitstand_started", endpoint=settings.zenoh_endpoint)
        if settings.auth_bearer_token is None:
            logger.warning("auth_disabled", reason="no auth_bearer_token; every caller is accepted")

        try:
            yield
        finally:
            if coverage_planner_client is not None:
                try:
                    await coverage_planner_client.aclose()
                except Exception:  # noqa: BLE001
                    logger.warning("coverage_planner_client_close_error")
            if chat_mcp_client is not None:
                try:
                    await chat_mcp_client.aclose()
                except Exception:  # noqa: BLE001
                    logger.warning("chat_mcp_client_close_error")
            if robot_status_projector is not None:
                try:
                    await robot_status_projector.aclose()
                except Exception:  # noqa: BLE001
                    logger.warning("robot_status_projector_close_error")
            if mission_state_adapter is not None:
                try:
                    mission_state_adapter.close()
                except Exception:  # noqa: BLE001
                    logger.warning("mission_state_adapter_close_error")
            if connectivity_adapter is not None:
                try:
                    connectivity_adapter.close()
                except Exception:  # noqa: BLE001
                    logger.warning("connectivity_adapter_close_error")
            if connectivity_uc is not None:
                await connectivity_uc.aclose()
            if z_ctx is not None:
                try:
                    await z_ctx.__aexit__(None, None, None)
                except Exception:  # noqa: BLE001
                    logger.warning("zenoh_close_error")
            await engine.dispose()
            logger.info("leitstand_stopped")

    app = FastAPI(
        title="Leitstand API", version=_pkg_version("leitstand-backend"), lifespan=lifespan
    )
    # Bound here rather than only in lifespan so that authentication, which every request needs,
    # does not depend on startup having run.
    app.state.settings = settings
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(CredentialContextMiddleware)

    if settings.cors_origins:
        # No allow_credentials: this API authenticates with a bearer header, and asking for
        # cookie credentials would only widen what a browser will send cross-origin.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # asyncpg can leak ConnectionError unwrapped on initial connect, so register both.
    app.add_exception_handler(OperationalError, _db_unavailable)
    app.add_exception_handler(ConnectionError, _db_unavailable)

    # Health stays open: an orchestrator probes it and holds no credential. Everything under /api/v1
    # is gated here rather than route by route, so a new router is closed by default.
    protected = [Depends(get_current_user)]
    app.include_router(health_router)
    app.include_router(fields_router, dependencies=protected)
    app.include_router(robots_router, dependencies=protected)
    app.include_router(missions_router, dependencies=protected)
    app.include_router(coverage_router, dependencies=protected)
    app.include_router(runs_router, dependencies=protected)
    app.include_router(sites_router, dependencies=protected)
    app.include_router(users_router, dependencies=protected)
    app.include_router(ws_router)
    app.include_router(chat_router, dependencies=protected)
    return app
