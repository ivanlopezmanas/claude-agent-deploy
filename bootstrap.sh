#!/bin/bash
set -euo pipefail

REPO_URL="https://github.com/ivanlopezmanas/claude-agent-deploy.git"
DEST="/tmp/claude-agent-deploy"

if ! command -v pct &>/dev/null; then
  echo "[ERROR] Ejecutar en el host Proxmox."
  exit 1
fi

if ! command -v git &>/dev/null; then
  apt-get install -y git
fi

rm -rf "${DEST}"
git clone --depth 1 "${REPO_URL}" "${DEST}"
bash "${DEST}/deploy/install-agent.sh"
