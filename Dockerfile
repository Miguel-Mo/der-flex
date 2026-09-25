FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip wheel --no-cache-dir --no-deps --wheel-dir /project-wheel .

FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 10001 derflex \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin derflex

COPY requirements-runtime.lock /tmp/install/requirements-runtime.lock
COPY vendor/wheelhouse /tmp/install/wheelhouse
COPY --from=builder /project-wheel /tmp/install/project-wheel
RUN python -m pip install --no-cache-dir --no-index --no-deps --require-hashes \
        --find-links /tmp/install/wheelhouse -r /tmp/install/requirements-runtime.lock \
    && python -m pip install --no-cache-dir --no-index --no-deps \
        /tmp/install/project-wheel/der_flex-0.1.0-py3-none-any.whl \
    && python -m pip check \
    && rm -rf /tmp/install

USER 10001:10001
WORKDIR /app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 --start-period=5s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/ready')"]
CMD ["uvicorn", "der_flex.demo:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
