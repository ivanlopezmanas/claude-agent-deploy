#!/usr/bin/env bash
# chmod +x /home/<agent>/workspace/scripts/lib/self-improve.sh
set -euo pipefail

SECRETS_FILE="/etc/<agent>/secrets.env"
OUTPUT_DIR="/home/<agent>/workspace/docs/improvements"
CLAUDE_BIN="/home/<agent>/claude/.local/bin/claude"
SETTINGS_BG="/home/<agent>/claude/.claude/settings-background.json"
MCP_PG="/home/<agent>/workspace/scripts/hooks/<agent>-mcp-postgres-only.json"
AGENT_ID="<agent>"
TELEGRAM_CHAT_ID="5859748267"

export HOME="/home/<agent>/claude"
export <AGENT>_CONTEXT="cron"

log() {
    echo "$(date '+%Y-%m-%dT%H:%M:%S')  $*"
}

# Load secrets into environment
if [[ -f "$SECRETS_FILE" ]]; then
    set -a
    # shellcheck source=/dev/null
    . "$SECRETS_FILE"
    set +a
fi

# Ensure output directory exists
mkdir -p "$OUTPUT_DIR"

log "<agent>-self-improve start"

# Run from the claude HOME so trust/project config is loaded
cd /home/<agent>/claude

PROMPT="Execute the self-improve agent with the following parameters:
- AGENT_ID: ${AGENT_ID}
- OUTPUT_DIR: ${OUTPUT_DIR}
- TELEGRAM_CHAT_ID: ${TELEGRAM_CHAT_ID}
- LANGUAGE: Spanish"

"$CLAUDE_BIN" --print \
    --agent self-improve \
    --strict-mcp-config \
    --settings "$SETTINGS_BG" \
    --mcp-config "$MCP_PG" \
    "$PROMPT"

log "<agent>-self-improve done"
