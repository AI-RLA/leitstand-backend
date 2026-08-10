# syntax=docker/dockerfile:1
FROM python:3.11-slim-bookworm

WORKDIR /app

# Source must be present before `pip install .` so setuptools'
# package finder can resolve the leitstand_backend package.
COPY pyproject.toml README.md ./

COPY --from=contract-src . /tmp/contract-src/
RUN pip install --no-cache-dir /tmp/contract-src && rm -rf /tmp/contract-src

COPY leitstand_backend ./leitstand_backend
COPY migrations ./migrations

RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 1000 leitstand \
    && chown -R leitstand:leitstand /app
USER leitstand

# Bind to all interfaces inside the container so the published
# port mapping reaches the process.
ENV LEITSTAND_HTTP_HOST=0.0.0.0

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)" \
    || exit 1

CMD ["python", "-m", "leitstand_backend"]
