#!/usr/bin/env bash
# Dev container postCreateCommand: pinned uv, project environment, git safe.directory.
# Runs as remoteUser (vscode) in the workspace folder. The venv goes to $UV_PROJECT_ENVIRONMENT
# (devcontainer.json), outside the bind-mounted workspace.
set -euo pipefail

UV_VERSION="0.13.1" # same as the extractor Dockerfile's uv image

pipx install "uv==${UV_VERSION}"
uv --version
uv sync --locked

# Bind mounts from Windows/macOS show up as owned by another user, so git refuses the repo
# ("dubious ownership"). Trust exactly this workspace, in the container's own ~/.gitconfig.
if ! git config --global --get-all safe.directory | grep -qxF "$PWD"; then
  git config --global --add safe.directory "$PWD"
fi

# No git hook install here: with a bind-mounted repo .git/hooks is shared with the host, and
# installing would overwrite the host's hook with one that points at this container's Python.
# Install it on the machine you commit from (see .devcontainer/README.md, "Git hooks").

databricks -v
gh --version | head -1
docker --version
