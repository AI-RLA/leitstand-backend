"""A broken entry in the external MCP server list is skipped, and the others still load."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from leitstand_backend.adapters.outbound.llm.domain_mcp import DOMAIN_TOOL_PREFIX
from leitstand_backend.infrastructure.external_mcp import load_external_servers

_WEATHER = {
    "url": "http://mcp-weather:8091/mcp",
    "timeout": 5000,
    "allowed_tools": ["current_weather", "daily_forecast"],
}


def _write(tmp_path: Path, content: object) -> Path:
    path = tmp_path / "mcp_servers.json"
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    return path


def test_a_valid_file_loads_every_entry(tmp_path: Path) -> None:
    second = {
        "url": "https://soil.example/mcp",
        "headers": {"X-Client": "leitstand"},
        "allowed_tools": ["moisture"],
    }
    servers = load_external_servers(
        _write(tmp_path, {"mcpServers": {"weather": _WEATHER, "soil": second}}), DOMAIN_TOOL_PREFIX
    )

    assert sorted(servers) == ["soil", "weather"]
    assert servers["weather"].url == "http://mcp-weather:8091/mcp"
    assert servers["weather"].timeout == 5000
    assert servers["weather"].allowed_tools == ("current_weather", "daily_forecast")
    assert servers["soil"].headers == {"X-Client": "leitstand"}
    assert servers["soil"].timeout == 10_000


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("not json", id="not json"),
        pytest.param(json.dumps({"servers": {}}), id="no mcpServers key"),
        pytest.param(json.dumps({"mcpServers": []}), id="mcpServers not an object"),
        pytest.param("[]", id="top level not an object"),
    ],
)
def test_an_unreadable_file_gives_no_servers(tmp_path: Path, content: str) -> None:
    with capture_logs() as logs:
        servers = load_external_servers(_write(tmp_path, content), DOMAIN_TOOL_PREFIX)

    assert servers == {}
    assert [log["event"] for log in logs] == ["external_mcp_config_unreadable"]


def test_a_missing_file_gives_no_servers(tmp_path: Path) -> None:
    with capture_logs() as logs:
        servers = load_external_servers(tmp_path / "absent.json", DOMAIN_TOOL_PREFIX)

    assert servers == {}
    assert [log["event"] for log in logs] == ["external_mcp_config_unreadable"]


def _load_with_a_valid_neighbour(tmp_path: Path, name: str, entry: dict) -> dict:
    neighbour = {**_WEATHER, "url": "http://mcp-soil:8092/mcp"}
    content = {"mcpServers": {name: entry, "soil": neighbour}}
    return load_external_servers(_write(tmp_path, content), DOMAIN_TOOL_PREFIX)


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"auth": "a-token"}, id="unknown key"),
        pytest.param({"allowed_tools": []}, id="no allowed tools"),
        pytest.param({"allowed_tools": ["bad name"]}, id="tool name with a space"),
        pytest.param({"allowed_tools": ["x" * 40]}, id="tool name too long for the prefix"),
        pytest.param({"timeout": 0}, id="timeout not positive"),
        pytest.param({"url": "mcp-weather:8091/mcp"}, id="url without scheme"),
        pytest.param({"url": "ftp://mcp-weather/mcp"}, id="url with another scheme"),
        pytest.param({"url": "httpfoo://mcp-weather/mcp"}, id="scheme only starting with http"),
        pytest.param({"url": "http://h:1/sse"}, id="sse endpoint"),
    ],
)
def test_an_invalid_entry_is_skipped_and_the_others_load(tmp_path: Path, change: dict) -> None:
    with capture_logs() as logs:
        servers = _load_with_a_valid_neighbour(tmp_path, "weather", {**_WEATHER, **change})

    assert list(servers) == ["soil"]
    assert [(log["event"], log["server"]) for log in logs] == [
        ("external_mcp_entry_invalid", "weather")
    ]


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("leitstandx", id="reserved prefix"),
        pytest.param("a_b", id="underscore"),
        pytest.param("Weather", id="upper case"),
        pytest.param("w" * 25, id="longer than 24"),
    ],
)
def test_an_invalid_server_name_is_skipped(tmp_path: Path, name: str) -> None:
    with capture_logs() as logs:
        servers = _load_with_a_valid_neighbour(tmp_path, name, _WEATHER)

    assert list(servers) == ["soil"]
    assert [(log["event"], log["server"]) for log in logs] == [("external_mcp_entry_invalid", name)]


@pytest.mark.parametrize(
    ("environment", "url"),
    [
        pytest.param({}, "http://mcp-weather:8091/mcp", id="unset takes the default"),
        pytest.param(
            {"MCP_TEST_URL": ""}, "http://mcp-weather:8091/mcp", id="empty takes the default"
        ),
        pytest.param(
            {"MCP_TEST_URL": "http://127.0.0.1:8091/mcp"},
            "http://127.0.0.1:8091/mcp",
            id="set takes the environment",
        ),
    ],
)
def test_a_variable_is_filled_in_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environment: dict, url: str
) -> None:
    monkeypatch.delenv("MCP_TEST_URL", raising=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    entry = {**_WEATHER, "url": "${MCP_TEST_URL:-http://mcp-weather:8091/mcp}"}

    servers = load_external_servers(
        _write(tmp_path, {"mcpServers": {"weather": entry}}), DOMAIN_TOOL_PREFIX
    )

    assert servers["weather"].url == url


def test_an_unset_variable_without_default_skips_only_its_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MCP_TEST_URL", raising=False)

    with capture_logs() as logs:
        servers = _load_with_a_valid_neighbour(
            tmp_path, "weather", {**_WEATHER, "url": "${MCP_TEST_URL}"}
        )

    assert list(servers) == ["soil"]
    assert "MCP_TEST_URL is not set" in logs[0]["reason"]


def test_the_shipped_file_loads_with_the_compose_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_WEATHER_URL", raising=False)
    path = Path(__file__).parents[3] / "config" / "mcp_servers.json"

    servers = load_external_servers(path, DOMAIN_TOOL_PREFIX)

    assert servers["weather"].url == "http://mcp-weather:8091/mcp"


def test_variables_are_filled_in_headers_and_several_to_a_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_TEST_HOST", "mcp-soil")
    monkeypatch.setenv("MCP_TEST_CLIENT", "leitstand")
    entry = {
        **_WEATHER,
        "url": "http://${MCP_TEST_HOST}:${MCP_TEST_PORT:-8092}/mcp",
        "headers": {"X-Client": "${MCP_TEST_CLIENT}"},
    }

    servers = load_external_servers(
        _write(tmp_path, {"mcpServers": {"soil": entry}}), DOMAIN_TOOL_PREFIX
    )

    assert servers["soil"].url == "http://mcp-soil:8092/mcp"
    assert servers["soil"].headers == {"X-Client": "leitstand"}
