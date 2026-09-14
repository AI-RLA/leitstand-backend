"""Test-wide fixtures.

The suite must not read a developer's ``.env`` or secrets: a local token would turn every route 401.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from leitstand_backend.infrastructure.settings import Settings


@pytest.fixture(autouse=True, scope="session")
def _settings_ignore_local_config() -> Iterator[None]:
    before = {k: Settings.model_config.get(k) for k in ("env_file", "secrets_dir")}
    Settings.model_config.update(env_file=None, secrets_dir=None)
    try:
        yield
    finally:
        Settings.model_config.update(before)
