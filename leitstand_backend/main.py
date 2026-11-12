"""Entry point."""

from __future__ import annotations

import sys

import uvicorn

from leitstand_backend.infrastructure.factory import create_app
from leitstand_backend.infrastructure.settings import Settings
from leitstand_backend.logging_setup import configure_logging


def main() -> int:
    settings = Settings()
    # Set up once here, because logging is global to the process and create_app may run many times.
    configure_logging(settings.log_level)
    app = create_app(settings)
    uvicorn.run(app, host=settings.http_host, port=settings.http_port, log_config=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
