#!/bin/sh
# Local dev container feature (runs as root at image build): install the Databricks CLI with the
# official install script from the databricks/setup-cli tag v$VERSION (the script hardcodes that
# version) into /usr/local/bin. No credentials: log in inside the container with
# `databricks auth login` (see .devcontainer/README.md).
set -eu

case "${VERSION:-}" in
"" | latest)
  echo "databricks-cli feature: set an exact version (got '${VERSION:-}')" >&2
  exit 1
  ;;
esac

if ! command -v curl >/dev/null || ! command -v unzip >/dev/null; then
  apt-get update
  apt-get install -y --no-install-recommends ca-certificates curl unzip
  rm -rf /var/lib/apt/lists/*
fi

curl -fsSL "https://raw.githubusercontent.com/databricks/setup-cli/v${VERSION}/install.sh" | sh
databricks -v
