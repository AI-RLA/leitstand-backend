# leitstand-backend

Backend service for the leitstand fleet-control platform.
Discovers robots as they come online, manages missions and sites,
persists state, and exposes a REST + WebSocket API to operator clients.

## Module layout

```
leitstand_backend/
├── domain/
├── ports/
│   ├── inbound/
│   └── outbound/
├── application/
├── adapters/
│   ├── inbound/
│   │   ├── messaging/
│   │   └── web/
│   └── outbound/
│       └── persistence/
└── infrastructure/
```

Hexagonal (ports-and-adapters) architecture. The codebase is
organised as follows:

- **`domain`** — the business model and its rules: entities,
  value objects, domain errors. Pure Python, with no I/O and no
  dependencies on other layers.
- **`ports`** — abstract interfaces (ABCs) defined by the
  application. Two kinds:
  - `inbound` (driving) ports: interfaces the application
    exposes for external actors to invoke.
  - `outbound` (driven) ports: interfaces the application
    depends on and invokes itself.

  The distinction refers to control flow (who invokes whom),
  not data flow. A read from an external system returns data
  inwards but is reached through an outbound port, because the
  application initiates the call.
- **`application`** — concrete use-case services that implement
  the inbound ports and coordinate domain objects to satisfy
  them. Contains application logic only. Business rules stay in
  `domain` and side effects are delegated to outbound ports.
- **`adapters`** — concrete implementations that translate
  between external technologies and the ports.
  - `inbound` (driving) adapters convert external events (HTTP
    requests, broker messages) into inbound-port invocations.
  - `outbound` (driven) adapters implement outbound ports
    against specific technologies (a database, a
    publish-subscribe broker, and so on).
- **`infrastructure`** — composition root and cross-cutting
  technical concerns (settings, database engine, middleware,
  application factory). The only layer permitted to instantiate
  concrete adapters and wire them to the application.

Dependencies flow inward only: the domain has no I/O, ports have
no implementations, and framework-specific code is confined to
adapters and infrastructure.

### Adapter implementations

| Adapter | Stack |
|---|---|
| `adapters/inbound/web` | FastAPI (REST + WebSocket) |
| `adapters/inbound/messaging` | Zenoh |
| `adapters/outbound/persistence` | Postgres + PostGIS, async SQLAlchemy 2.0, Alembic, GeoAlchemy2 |

## Zenoh integration

The backend runs as a Zenoh client and dials the `zenohd` router
configured by `LEITSTAND_ZENOH_ENDPOINT`. The router runs as a
separate process and is started by the docker-compose stack. For
each registered robot (`<id>` is the robot's slug) it consumes:

| Key | Kind | Purpose |
|---|---|---|
| `leitstand/robot/<id>/online` | liveliness token | PUT marks the robot online, DELETE marks it offline |
| `leitstand/robot/<id>/metadata` | queryable | identity JSON (`{"id": "<id>", ...}`), queried once at registration |
| `leitstand/robot/<id>/pose` | publication | pose telemetry |
| `leitstand/robot/<id>/battery` | publication | battery telemetry |

The backend also dispatches missions and consumes execution state, factsheets, and
pause/resume over the `.../mission/*`, `.../factsheet`, and `.../instant/*` keys; the
`leitstand-robot-contract` repo is the authoritative wire spec for those proto channels.
The robot-side producer of all these keys is `leitstand-robot-client`. Full details
(payload schemas, threading model) are in
`leitstand_backend/adapters/inbound/messaging/zenoh/`.

## HTTP REST API

The running backend serves interactive API docs (FastAPI defaults):

- Swagger UI:   `http://localhost:8080/docs`
- ReDoc:        `http://localhost:8080/redoc`
- OpenAPI spec: `http://localhost:8080/openapi.json`

The committed `openapi.json` at the repo root is the contract the
frontend codegens against. Regenerate with `make openapi` whenever
a route or DTO changes. Commit it alongside the code change.

## Configuration

All env vars are prefixed `LEITSTAND_` and read from process env
or a `.env` file.

| Variable | Default | Purpose |
|---|---|---|
| `LEITSTAND_HTTP_HOST` | `127.0.0.1` | HTTP bind host (set to `0.0.0.0` in the Docker image) |
| `LEITSTAND_HTTP_PORT` | `8080` | HTTP bind port |
| `LEITSTAND_LOG_LEVEL` | `INFO` | Python logging level |
| `LEITSTAND_ZENOH_ENDPOINT` | `tcp/127.0.0.1:7447` | local `zenohd` to dial (client mode) |
| `LEITSTAND_ZENOH_CONFIG` | unset | path to an explicit Zenoh JSON5 config (takes precedence over `_ENDPOINT`) |
| `LEITSTAND_ZENOH_DISABLED` | `false` | skip the Zenoh subscriber entirely (HTTP-only mode) |
| `LEITSTAND_DATABASE_URL` | `postgresql+asyncpg://leitstand:leitstand@localhost:5432/leitstand` | async SQLAlchemy URL |
| `LEITSTAND_AUTO_MIGRATE` | `true` | run `alembic upgrade head` on startup |
| `LEITSTAND_DB_POOL_SIZE` | `10` | SQLAlchemy connection pool size |

## Deployment

```bash
cp .env.example .env  # adjust if needed
docker compose up --build
```

Starts Postgres, the Zenoh router, and the backend container.
API at `http://localhost:8080`, Zenoh router at
`tcp/localhost:7447`. Stop with `docker compose down`.

For active Python development on the backend, see Development
below.

## Development

The sibling `leitstand-robot-contract` repo must be checked out next to this one
(`../leitstand-robot-contract`); `make dev-install` installs it editable before the
backend, and the Docker build reads it via `additional_contexts`.

```bash
python3 -m venv .venv
make dev-install   # editable contract, then the backend + dev deps
make check         # ruff + format-check + tests
make openapi       # regenerate openapi.json
```

`make ci` mirrors what GitLab CI runs.

### Running the backend natively

For fast iteration, run the stateful deps in compose and the backend natively:

```bash
make dev-run       # docker compose up -d postgres zenoh-router, then the backend
```

### Pre-commit hooks

```bash
pipx install pre-commit
pre-commit install
```

Hooks: ruff (check + format), trailing whitespace, EOL fixer,
YAML/TOML/large-file checks. Config in `.pre-commit-config.yaml`.

### Database migrations

```bash
make migrate                          # apply head
make migrate-revision name="add foo"  # autogenerate a new revision
```
