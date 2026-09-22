# layout-canvas web canvas + gallery.
#
# Hardening mirrors the analog-canvas simulator host: read-only root,
# dropped capabilities, unprivileged uid, private tmpfs for scratch.
# Runtime bindings (gallery path, host, port) come from the environment —
# nothing environment-specific is baked into the image.
#
# Build:   docker build -f deploy/Dockerfile -t layout-canvas:$(git rev-parse --short=12 HEAD) .
# Run:     docker compose -f deploy/compose.yaml up -d
# Verify:  curl http://localhost:8080/api/health && curl http://localhost:8080/api/version
FROM python:3.12-slim

ARG RUN_UID=10001
ARG RUN_GID=10001
RUN groupadd -g "${RUN_GID}" canvas && useradd -m -u "${RUN_UID}" -g "${RUN_GID}" canvas
# git backs gallery sync — community co-building rides on the gallery repo.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY examples/ ./examples/
RUN pip install --no-cache-dir . \
    && mkdir -p /data/gallery \
    && chown -R canvas:canvas /data

USER canvas
ENV LAYOUT_CANVAS_GALLERY=/data/gallery
EXPOSE 8080
# SOURCE_COMMIT is written by the release pipeline; /api/version reads it.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/api/health',timeout=4)"
CMD ["layout-canvas", "web", "--host", "0.0.0.0", "--port", "8080"]
