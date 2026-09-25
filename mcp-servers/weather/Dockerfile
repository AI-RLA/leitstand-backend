# syntax=docker/dockerfile:1
FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# Dependencies in their own layer, so a code change does not reinstall them.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml .
COPY mcp_weather ./mcp_weather
RUN pip install --no-cache-dir --no-deps --no-build-isolation . \
    && useradd --create-home --uid 1000 mcp
USER mcp
# All interfaces, because clients reach the server from other containers.
ENV FASTMCP_HOST=0.0.0.0 FASTMCP_PORT=8091
EXPOSE 8091
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8091/healthz', timeout=3)" \
    || exit 1
CMD ["mcp-weather"]
