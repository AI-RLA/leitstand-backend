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
| `leitstand/robot/<id>/factsheet` | queryable | capability declaration |
| `leitstand/robot/<id>/mission/_action/send_goal`, `.../cancel_goal` | queryable | dispatch, cancel |
| `leitstand/robot/<id>/mission/_action/pause`, `.../resume` | queryable | pause, resume |
| `leitstand/robot/<id>/mission/state` | publication | execution state |

All payloads except `metadata` are the proto messages of `leitstand-robot-contract`; the robot
side is `leitstand-robot-client-template`.

## HTTP REST API

The running backend serves interactive API docs (FastAPI defaults):

- Swagger UI:   `http://localhost:8080/docs`
- ReDoc:        `http://localhost:8080/redoc`
- OpenAPI spec: `http://localhost:8080/openapi.json`

The committed `openapi.json` at the repo root is the contract the
frontend codegens against. Regenerate with `make openapi` whenever
a route or DTO changes. Commit it alongside the code change.

## Configuration

All env vars are prefixed `LEITSTAND_` and read from process env, a `.env` file, or one
bare-value file per secret under `secrets/` (`/run/secrets` in Docker), in that precedence.

| Variable | Default | Purpose |
|---|---|---|
| `LEITSTAND_HTTP_HOST` | `127.0.0.1` | HTTP bind host (set to `0.0.0.0` in the Docker image) |
| `LEITSTAND_HTTP_PORT` | `8080` | HTTP bind port |
| `LEITSTAND_LOG_LEVEL` | `INFO` | Python logging level |
| `LEITSTAND_ZENOH_ENDPOINT` | `tcp/127.0.0.1:7447` | local `zenohd` to dial (client mode) |
| `LEITSTAND_ZENOH_CONFIG` | unset | path to an explicit Zenoh JSON5 config (takes precedence over `_ENDPOINT`) |
| `LEITSTAND_ZENOH_DISABLED` | `false` | skip the Zenoh subscriber entirely (HTTP-only mode) |
| `LEITSTAND_DATABASE_URL` | `postgresql+asyncpg://leitstand:leitstand@localhost:5432/leitstand` | async SQLAlchemy URL; without a password, `LEITSTAND_DB_PASSWORD` is inserted |
| `LEITSTAND_DB_PASSWORD` | unset | normally `secrets/leitstand_db_password` |
| `LEITSTAND_AUTO_MIGRATE` | `true` | run `alembic upgrade head` on startup |
| `LEITSTAND_DB_POOL_SIZE` | `10` | SQLAlchemy connection pool size |
| `LEITSTAND_AUTH_BEARER_TOKEN` | unset | required on REST and the WS handshake when set; normally `secrets/leitstand_auth_bearer_token` |
| `LEITSTAND_CORS_ORIGINS` | empty | comma-separated or JSON list (middleware added only when non-empty) |
| `LEITSTAND_LLM_BASE_URL` | `http://localhost:8000/v1` | any OpenAI-compatible endpoint |
| `LEITSTAND_LLM_API_KEY` | unset | API key, if the endpoint needs one; normally `secrets/leitstand_llm_api_key` |
| `LEITSTAND_LLM_MODEL` | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` | model name |
| `LEITSTAND_LLM_CONNECT_TIMEOUT_S` | `3` | connect timeout |
| `LEITSTAND_LLM_READ_TIMEOUT_S` | `60` | read timeout |
| `LEITSTAND_LLM_REASONING` | `off` | `off` disables the model's chain of thought |
| `LEITSTAND_CHAT_ENABLED` | `true` | expose `POST /api/v1/chat` |
| `LEITSTAND_CHAT_MAX_TOOL_CALLS` | `10` | max tool calls per turn |
| `LEITSTAND_MCP_SERVERS_FILE` | unset | external MCP servers for the AI assistant, see below. Unset means none |
| `LEITSTAND_CHAT_TIMEZONE` | `Europe/Berlin` | the operators' time zone, in which the AI assistant is told the date and hour |
| `LEITSTAND_COVERAGE_PLANNER_URL` | unset | coverage planner service; unset means coverage planning answers 503 |
| `LEITSTAND_COVERAGE_PLANNER_CONNECT_TIMEOUT_S` | `3` | connect timeout |
| `LEITSTAND_COVERAGE_PLANNER_READ_TIMEOUT_S` | `60` | read timeout |
| `LEITSTAND_COVERAGE_TURN_SAMPLE_M` | `0.25` | point spacing along a planned turn, in metres |
| `LEITSTAND_COVERAGE_LINEAR_CURV_CHANGE` | `200.0` | how fast curvature may change along a turn, in 1/m² |

If the LLM is unreachable, only AI chat assistant fails.

Authentication is one shared token. Every caller resolves to the same operator, and
there is no authorization, so any authenticated caller can dispatch any robot or
delete any field. Real identity (OIDC) and per-user permissions are not built yet (TODO).

## Deployment

The Leitstand is a research prototype. The connection between the backend and the robots is
neither authenticated nor encrypted, and the operator interface has no user accounts. Run all
components on a private network or behind a VPN.

```bash
cp .env.example .env            # adjust if needed
cp -r secrets.example secrets   # dev defaults; fill in for a real deployment
docker compose up -d --build
cd ../leitstand-frontend && docker compose up -d --build   # UI at http://<host>/
```

Starts Postgres, the Zenoh router, the coverage planner, the weather MCP server and the backend.
Zenoh router at `tcp/<host>:7447`. The API is on `127.0.0.1:8080` only (`BACKEND_BIND_ADDR` in
`.env` widens it), and the frontend stack reaches it over the shared `leitstand` network. Stop with
`docker compose down` (`down -v` also deletes the database).

`secrets/` holds one bare-value file per credential, mounted into the containers and never
placed in the environment:

| File | Value | Empty means |
|---|---|---|
| `leitstand_llm_api_key` | the LLM provider's key | chat runs without a key |
| `leitstand_auth_bearer_token` | any string of `A-Za-z0-9._~+-` | the API is open |
| `leitstand_db_password` | the Postgres password; the example holds the dev default | not allowed |

After changing a file, `docker compose up -d --force-recreate` the containers that use it (the
frontend stack reads the bearer too). Postgres reads its password only when it creates the data
directory; on an existing volume run `ALTER USER leitstand PASSWORD '<new>'` via
`docker compose exec postgres psql -U leitstand` before writing the same value into the file.

For active Python development on the backend, see Development
below.

### External MCP servers

The AI assistant can also use tools of other MCP servers, listed in `config/mcp_servers.json` in
the usual `mcpServers` format plus one key of our own, `allowed_tools`. How to write and add a
server: [`mcp-servers/README.md`](mcp-servers/README.md).

```json
{
  "mcpServers": {
    "weather": {
      "url": "${MCP_WEATHER_URL:-http://mcp-weather:8091/mcp}",
      "allowed_tools": ["current_weather", "daily_forecast", "hourly_forecast", "search_places"]
    }
  }
}
```

| Key | Description |
|---|---|
| `<name>` | Server name. The AI assistant sees each tool as `<name>_<tool>`, for example `weather_daily_forecast`. Lower-case letters and digits, at most 24 characters, not starting with `leitstand`. |
| `url` | Streamable HTTP endpoint. |
| `allowed_tools` | Names of the server's tools the AI assistant may use, without the prefix. Tools not listed are not offered. Each name at most 39 characters, 64 including the prefix. |
| `timeout` | Timeout per call in milliseconds. Default 10000. |
| `headers` | HTTP headers sent with each request. |

- Only tools the server marks read-only are offered, because they run without the operator's
  approval. A server's texts reach the model unchanged, so configure only servers you trust.
- An invalid entry is skipped with a log line (`external_mcp_entry_invalid`). While a server is
  down, the AI assistant answers without its tools.

## Development

The sibling `leitstand-robot-contract` repo must be checked out next to this one
(`../leitstand-robot-contract`); `make dev-install` installs it editable before the
backend, and the Docker build reads it via `additional_contexts`.

```bash
python3 -m venv .venv
make dev-install   # editable contract, then the backend + dev deps
make check         # ruff + format-check + tests
make check-integration   # integration tests against the compose Postgres
make openapi       # regenerate openapi.json
```

`make ci` mirrors what CI runs (`.github/workflows/ci.yml`).

The integration tests create, migrate and drop their own database on the Postgres server in
`LEITSTAND_TEST_DATABASE_URL`, by default the compose one. Its login must be allowed to create
databases, so point it at a development server, never at production.

### Running the backend natively

For fast iteration, run the stateful deps in compose and the backend natively:

```bash
make dev-run       # postgres, zenoh-router and the planner in compose, then the backend
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

## Contact

Jannik Jose, jannik.jose@hs-osnabrueck.de

## License

Copyright 2026 Osnabrück University of Applied Sciences.
Apache License 2.0, see [LICENSE](LICENSE).
