# MCP servers

External [MCP](https://modelcontextprotocol.io/) servers that give the Leitstand AI agents tools
beyond the backend's own API. The AI assistant in the chat is the first agent to use them. Each
folder holds one server.

## Running

`docker compose up -d --build` and `make dev-run` start every server together with the backend.
To run a server by itself, see its README.

## Adding a server

1. Create `mcp-servers/<name>/`, starting from a copy of `weather/`. A server shares no code with
   the others and never imports the backend, so it could move to its own repository unchanged.
2. Give each tool annotations that match what it does, for example `readOnlyHint: True` for one
   that only reads.
3. Register it in the backend:
   - a compose service `mcp-<name>` in `docker-compose.yaml`,
   - a `docker compose up -d --build mcp-<name>` line and its URL variable in the `dev-run` target
     of the `Makefile`,
   - an entry in `config/mcp_servers.json`. Its keys and the rules for the name are in the backend
     README, "External MCP servers".

## Adding a tool

Add the tool to the server as its README describes, then add its name to the server's
`allowed_tools` in `config/mcp_servers.json`, unless a pattern there already matches it. A tool
nothing in that list matches is not offered.

## Checks

`make ruff` and `make format-check` in the backend lint every server with the backend's settings.
