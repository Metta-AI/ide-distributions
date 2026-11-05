#!/usr/bin/env bash
# Run pytest inside the pinned Hermes image, with this checkout mounted read-only at /src, so a
# real gateway is the Hermes that hosting runs. The image has no uv and the checkout's .venv is
# for this machine, so both are made under /tmp, which goes away with the container. See the
# README, "Develop and test", for the docker command.
set -euo pipefail

export HOME=/tmp/home UV_INSTALL_DIR=/tmp/uv UV_NO_MODIFY_PATH=1 UV_CACHE_DIR=/tmp/uv-cache
export UV_PROJECT_ENVIRONMENT=/tmp/venv UV_PYTHON_INSTALL_DIR=/tmp/uv-python
export PYTHONDONTWRITEBYTECODE=1 PATH="/tmp/uv:$PATH"
export HERMES_BIN=/opt/hermes/.venv/bin/hermes IDE_HERMES_SRC=/opt/hermes
export GIT_AUTHOR_NAME="IDE model tests" GIT_AUTHOR_EMAIL=ide-model-tests@example.invalid
export GIT_COMMITTER_NAME="IDE model tests" GIT_COMMITTER_EMAIL=ide-model-tests@example.invalid
mkdir -p "$HOME"

curl -LsSf https://astral.sh/uv/install.sh | sh
cd /src
uv sync --frozen
exec uv run --frozen pytest -p no:cacheprovider --basetemp=/tmp/pytest "$@"
