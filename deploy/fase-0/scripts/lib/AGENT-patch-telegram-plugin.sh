#!/bin/bash
# chmod +x /home/<agent>/workspace/scripts/lib/<agent>-patch-telegram-plugin.sh
#
# Aplica el server.ts modificado al plugin de Telegram antes de iniciar el servicio.
# Resistente a cambios de versión del plugin. Si no encuentra el fichero, sale sin error.
PLUGIN_SERVER=$(find /home/<agent>/claude/.claude/plugins/cache/claude-plugins-official/telegram -name "server.ts" -maxdepth 3 2>/dev/null | head -1)
if [ -n "$PLUGIN_SERVER" ]; then
    cp /home/<agent>/workspace/scripts/lib/<agent>-telegram-server.ts "$PLUGIN_SERVER"
fi
