FROM ghcr.io/astral-sh/uv:0.11.8@sha256:3b7b60a81d3c57ef471703e5c83fd4aaa33abcd403596fb22ab07db85ae91347 AS uv
FROM nousresearch/hermes-agent@sha256:9774f4f39a9bb8c2f68ce728ed5e99ddbad282163be56764afacf88ed952b784
RUN apt-get update && apt-get install -y --no-install-recommends --reinstall tini \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /opt/distribution/
COPY core /opt/distribution/core
ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PROJECT_ENVIRONMENT=/opt/workspace-env \
    UV_CACHE_DIR=/tmp/uv-cache \
    UV_PYTHON=3.12 \
    IDE_WORKSPACE_ENV=/opt/workspace-env
RUN uv sync --frozen --no-dev --project /opt/distribution \
    && sha256sum /opt/distribution/uv.lock | cut -d ' ' -f1 > /opt/workspace-env/.lock-sha \
    && chmod -R a+rX /opt/python /opt/workspace-env /opt/distribution \
    && rm -rf /tmp/uv-cache
COPY orchestrator /opt/distribution/orchestrator
COPY researcher /opt/distribution/researcher
COPY image /opt/distribution/image
ENV HOME=/workspace/host \
    HERMES_HOME=/workspace/host \
    HERMES_REAL_HOME=/workspace/host \
    HERMES_WRITE_SAFE_ROOT=/workspace \
    IDE_WORKSPACE=/workspace/research \
    XDG_RUNTIME_DIR=/tmp/hermes-runtime \
    PATH=/opt/workspace-env/bin:/opt/hermes/.venv/bin:/usr/local/bin:/usr/bin:/bin
USER 10000:10000
WORKDIR /workspace
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/hermes/.venv/bin/python", "/opt/distribution/image/runtime.py"]
