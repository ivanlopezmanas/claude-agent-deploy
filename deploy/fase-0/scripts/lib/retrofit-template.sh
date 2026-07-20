#!/usr/bin/env bash
# chmod +x /home/<agent>/workspace/scripts/lib/retrofit-template.sh
#
# retrofit-template.sh — clona ~/template/ si no existe. Idempotente: correrlo
# dos veces no rompe nada, la segunda vez no hace nada.
#
# Uso: retrofit-template.sh [AGENT_HOME]
# Sin argumento usa $HOME — pensado para poder correrlo tanto desde el
# instalador (pasando la ruta explícita, se ejecuta como root vía lxc_exec)
# como directamente por el propio usuario del agente en una instancia ya
# desplegada (backfill manual futuro).
set -euo pipefail

REPO_URL="https://github.com/ivanlopezmanas/claude-agent-deploy.git"
AGENT_HOME="${1:-$HOME}"
TEMPLATE_DIR="${AGENT_HOME}/template"

if [[ -d "${TEMPLATE_DIR}/.git" ]]; then
  echo "[SKIP] ${TEMPLATE_DIR} ya es un clon git, no se toca."
  exit 0
fi

if [[ -e "${TEMPLATE_DIR}" ]]; then
  echo "[ERROR] ${TEMPLATE_DIR} existe pero no es un repo git. Revisar a mano." >&2
  exit 1
fi

git clone "${REPO_URL}" "${TEMPLATE_DIR}"
echo "[OK] ${TEMPLATE_DIR} clonado."
