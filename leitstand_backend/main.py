"""Entry point."""

from __future__ import annotations

import sys

import uvicorn

from leitstand_backend.infrastructure.factory import create_app
from leitstand_backend.infrastructure.settings import Settings


def main() -> int:
    settings = Settings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.http_host, port=settings.http_port, log_config=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
