"""Config that cannot work is refused at boot.

Both values reach a boundary narrower than their type. A bad token takes the live fleet stream
down while the rest of the app looks healthy; bad CORS input stops the process starting.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from leitstand_backend.infrastructure.settings import Settings


def _settings(**overrides) -> Settings:
    return Settings(zenoh_disabled=True, auto_migrate=False, **overrides)


def test_a_base64_token_is_refused() -> None:
    """`/` and `=` are not allowed in a WebSocket subprotocol."""
    with pytest.raises(ValidationError, match="RFC 7230"):
        _settings(auth_bearer_token="aB+c/dE=")


def test_a_token_with_a_dollar_is_refused() -> None:
    """nginx expands `$name` inside the directive the proxy renders the token into."""
    with pytest.raises(ValidationError, match="RFC 7230"):
        _settings(auth_bearer_token="abc$host")


def test_a_rejected_token_is_not_echoed_in_the_error() -> None:
    """The validation error is the container's startup output."""
    with pytest.raises(ValidationError) as excinfo:
        _settings(auth_bearer_token="SuperSecret$Token")
    assert "SuperSecret" not in str(excinfo.value)


def test_a_token_with_a_trailing_newline_is_refused() -> None:
    """A secret read from a file carries one."""
    with pytest.raises(ValidationError, match="RFC 7230"):
        _settings(auth_bearer_token="a1b2c3d4e5f6\n")


def test_a_hex_token_is_accepted() -> None:
    token = _settings(auth_bearer_token="a1b2c3d4e5f6").auth_bearer_token
    assert token is not None and token.get_secret_value() == "a1b2c3d4e5f6"


def test_the_token_and_the_llm_key_never_appear_in_repr() -> None:
    s = _settings(auth_bearer_token="deadbeefcafe", llm_api_key="sk-live-key")
    assert "deadbeefcafe" not in repr(s) and "sk-live-key" not in repr(s)


def test_no_token_stays_dev_open() -> None:
    assert _settings().auth_bearer_token is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://a.example", ["http://a.example"]),
        ("http://a.example,http://b.example", ["http://a.example", "http://b.example"]),
        ("http://a.example, http://b.example ", ["http://a.example", "http://b.example"]),
        ('["http://a.example"]', ["http://a.example"]),
    ],
)
def test_cors_origins_parsing(value: str, expected: list[str]) -> None:
    """Comma-separated as well as JSON: both are forms an operator writes."""
    assert _settings(cors_origins=value).cors_origins == expected


# An empty secret file reads as '' (undocumented, verified on pydantic-settings 2.14); neither
# that nor the directory order is pinned upstream, so both are pinned here.


@pytest.fixture
def secrets_dir(tmp_path, monkeypatch):
    monkeypatch.setitem(Settings.model_config, "secrets_dir", str(tmp_path))
    return tmp_path


def test_an_empty_secret_file_leaves_the_setting_unset(secrets_dir) -> None:
    (secrets_dir / "leitstand_auth_bearer_token").write_text("")
    (secrets_dir / "leitstand_llm_api_key").write_text("")

    s = _settings()

    assert s.auth_bearer_token is None
    assert s.llm_api_key is None


def test_a_blank_secret_file_leaves_the_setting_unset(secrets_dir) -> None:
    (secrets_dir / "leitstand_auth_bearer_token").write_text("\n")

    assert _settings().auth_bearer_token is None


def test_a_secret_file_is_read_and_stripped(secrets_dir) -> None:
    (secrets_dir / "leitstand_auth_bearer_token").write_text("a1b2c3d4e5f6\n")
    (secrets_dir / "leitstand_llm_api_key").write_text("sk-test-key\n")

    s = _settings()

    assert s.auth_bearer_token is not None and s.llm_api_key is not None
    assert s.auth_bearer_token.get_secret_value() == "a1b2c3d4e5f6"
    assert s.llm_api_key.get_secret_value() == "sk-test-key"


def test_a_later_secrets_dir_wins(tmp_path, monkeypatch) -> None:
    """The order in settings.py relies on this: a checkout's ./secrets overrides /run/secrets."""
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir(), second.mkdir()
    (first / "leitstand_llm_api_key").write_text("from-first")
    (second / "leitstand_llm_api_key").write_text("from-second")
    monkeypatch.setitem(Settings.model_config, "secrets_dir", [str(first), str(second)])

    key = _settings().llm_api_key
    assert key is not None and key.get_secret_value() == "from-second"


def test_a_url_without_a_password_gets_the_secret_inserted() -> None:
    s = _settings(
        database_url="postgresql+asyncpg://leitstand@postgres:5432/leitstand",
        db_password="s3cret",
    )
    assert s.database_url_str == "postgresql+asyncpg://leitstand:s3cret@postgres:5432/leitstand"


def test_a_url_with_its_own_password_wins() -> None:
    """The Makefile, CI and the integration suite pass full URLs at a scratch database."""
    s = _settings(
        database_url="postgresql+asyncpg://leitstand:inline@localhost:5432/leitstand_runs_test",
        db_password="s3cret",
    )
    assert "inline@" in s.database_url_str and "s3cret" not in s.database_url_str


def test_no_secret_leaves_a_passwordless_url_alone() -> None:
    s = _settings(database_url="postgresql+asyncpg://leitstand@postgres:5432/leitstand")
    assert s.database_url_str == "postgresql+asyncpg://leitstand@postgres:5432/leitstand"


def test_the_db_password_is_read_from_a_secret_file_and_never_shown(secrets_dir) -> None:
    (secrets_dir / "leitstand_db_password").write_text("fr0m-file\n")
    s = _settings(database_url="postgresql+asyncpg://leitstand@postgres:5432/leitstand")
    assert s.database_url_str.endswith("leitstand:fr0m-file@postgres:5432/leitstand")
    assert "fr0m-file" not in repr(s)


def test_an_empty_db_password_file_is_unset(secrets_dir) -> None:
    (secrets_dir / "leitstand_db_password").write_text("")
    assert _settings().db_password is None
