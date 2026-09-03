# syntax=docker/dockerfile:1
ARG F2C_VERSION=v1.2.1

FROM python:3.11-slim-bookworm AS builder
ARG F2C_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake g++ git ca-certificates swig \
      libeigen3-dev libgdal-dev libgeos-dev libtbb-dev libpython3-dev \
      libtinyxml2-dev nlohmann-json3-dev libgtest-dev python3-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "${F2C_VERSION}" \
      https://github.com/Fields2Cover/Fields2Cover.git /src
# v1.2.1 hardcodes -Werror and vendors matplotlib_cpp, which calls Py_SetProgramName, deprecated on
# Python 3.11 and therefore fatal. No build option disables the visualizer, so the flag itself goes.
RUN sed -i 's/-Werror //' /src/CMakeLists.txt
WORKDIR /src/build
RUN cmake -DBUILD_PYTHON=ON -DBUILD_TESTS=OFF -DBUILD_TUTORIALS=OFF -DCMAKE_BUILD_TYPE=Release .. \
    && make -j"$(nproc)" && make install

FROM python:3.11-slim-bookworm
ARG F2C_VERSION
ENV F2C_VERSION="fields2cover ${F2C_VERSION}"
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgdal32 libgeos-c1v5 libtbb12 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/lib/libFields2Cover.so /usr/local/lib/
COPY --from=builder /usr/local/lib/libsteering_functions.so /usr/local/lib/
COPY --from=builder /usr/local/lib/libtinyxml2.so* /usr/local/lib/
COPY --from=builder /usr/local/lib/python3.11/site-packages/fields2cover.py \
                    /usr/local/lib/python3.11/site-packages/_fields2cover_python.so \
                    /usr/local/lib/python3.11/site-packages/
RUN ldconfig
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
RUN useradd --create-home --uid 1000 planner && chown -R planner:planner /app
USER planner
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=3)" \
    || exit 1
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090"]
