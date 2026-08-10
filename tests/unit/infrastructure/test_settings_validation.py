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
    """`+/=` cannot ride the WebSocket subprotocol."""
    with pytest.raises(ValidationError, match="RFC 7230"):
        _settings(auth_bearer_token="aB+c/dE=")


def test_a_token_with_a_trailing_newline_is_refused() -> None:
    """A secret read from a file carries one."""
    with pytest.raises(ValidationError, match="RFC 7230"):
        _settings(auth_bearer_token="a1b2c3d4e5f6\n")


def test_a_hex_token_is_accepted() -> None:
    assert _settings(auth_bearer_token="a1b2c3d4e5f6").auth_bearer_token == "a1b2c3d4e5f6"


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
