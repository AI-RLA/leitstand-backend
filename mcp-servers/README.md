# MCP servers

External [MCP](https://modelcontextprotocol.io/) servers that give the Leitstand AI agents tools
beyond the backend's own API. The AI assistant in the chat is the first agent to use them. Each
folder holds one server.

## Principles

- **Self-contained.** Every server has its own `pyproject.toml`, `requirements.txt`, `Dockerfile`
  and README, shares no code with the others and never imports the backend. A folder can move to
  its own repository unchanged.
- **Read-only.** Agents call external tools without asking the operator, so they are offered only
  tools that declare `annotations={"readOnlyHint": True}` and are listed in `allowed_tools`.
- **Streamable HTTP.** The backend connects to each server over HTTP, one container per server.

## Running

`docker compose up -d --build` and `make dev-run` start every server together with the backend.
To run a server by itself, see its README.

## Adding a server

1. Create `mcp-servers/<name>/`, starting from a copy of `weather/`.
2. Mark every tool read-only, as above.
3. Register it in the backend:
   - a compose service `mcp-<name>` in `docker-compose.yaml`,
   - a `docker compose up -d --build mcp-<name>` line and its URL variable in the `dev-run` target
     of the `Makefile`,
   - an entry in `config/mcp_servers.json`. Its keys and the rules for the name are in the backend
     README, "External MCP servers".

## Adding a tool

Add the tool to the server as its README describes, then add its name to the server's
`allowed_tools` in `config/mcp_servers.json`. A tool missing from that list is not offered.

## Checks

`make ruff` and `make format-check` in the backend lint every server with the backend's settings.
