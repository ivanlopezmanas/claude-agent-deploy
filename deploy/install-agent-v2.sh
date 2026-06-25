#!/bin/bash
#
# install-agent-v2.sh — Provisioning completo de un agente Claude Code en un LXC de Proxmox.
#
# VERSION 2 — Flujo secuencial (STEP_01 a STEP_28):
#   STEP_01-05: LXC base (create, start, SSH, apt, deps).
#   STEP_06-08: Usuario, PostgreSQL, init-db.
#   STEP_09-10: Claude Code + OAuth.
#   STEP_11-15: Secrets, canal Telegram, trust, workspace.
#   STEP_16-20: Node.js, Bun, MCP, plugin Telegram, access.json.
#   STEP_21-22: systemd, sudoers.  STEP_23: AppArmor (comentado).
#   STEP_24-25: enable/start + verificar servicio.
#   STEP_26: Verificación Telegram (manual).
#   STEP_27: Verificación final (pytest, postgres, heartbeat).
#   STEP_28: Onboarding (último) — inyecta bloque en CLAUDE.md.
#
# Ejecutar EN EL HOST Proxmox (no dentro del LXC). Requiere: pct, pveam, rsync.
#
# En caso de fallo, para con un mensaje claro; no hace rollback automático.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# =============================================================================
# SECCIÓN 0 — Cabecera y utilidades
# =============================================================================

# ---- Colores (ANSI directo, sin tput) ----
RED=$'\033[31m'
GREEN=$'\033[32m'
YELLOW=$'\033[33m'
BOLD=$'\033[1m'
RESET=$'\033[0m'

log_info() { echo "${BOLD}$*${RESET}"; }
log_ok()   { echo "${GREEN}[OK]${RESET}   $*"; }
log_warn() { echo "${YELLOW}[WARN]${RESET} $*"; }
log_fail() { echo "${RED}[FAIL]${RESET} $*" >&2; }

# run_step ID DESC COMANDO [ARGS...]
# Ejecuta el comando. Para pasos complejos, define una función step_XX_*() y pásala como comando.
run_step() {
  local id=$1; local desc=$2; shift 2
  echo ""
  echo "${BOLD}==> [$id] $desc${RESET}"
  if "$@"; then
    log_ok "$id"
  else
    log_fail "$id falló. Corrígelo y re-ejecuta el script."
    exit 1
  fi
}

# Ejecuta un comando DENTRO del LXC como root.
lxc_exec() {
  pct exec "${VMID}" -- bash -c "$1"
}

# Caja visual para pasos manuales.
manual_box() {
  echo ""
  echo "${YELLOW}+--------------------------------------------------------------------+${RESET}"
  echo "${YELLOW}| ${BOLD}PASO MANUAL${RESET}${YELLOW}                                                        |${RESET}"
  echo "${YELLOW}+--------------------------------------------------------------------+${RESET}"
}

# =============================================================================
# SECCIÓN 1 — Recogida de parámetros
# =============================================================================

log_info ""
log_info "============================================================"
log_info "  install-agent.sh — Provisioning de un agente Claude Code"
log_info "============================================================"
echo ""
echo "Responde a las preguntas. Los valores por defecto van [entre corchetes]."
echo ""

# ---- AGENT_NAME ----
while :; do
  read -rp "Nombre del agente (minúsculas, sin espacios): " AGENT_NAME
  if [[ -z "${AGENT_NAME}" ]]; then
    log_warn "No puede estar vacío."
  elif [[ ! "${AGENT_NAME}" =~ ^[a-z][a-z0-9_-]*$ ]]; then
    log_warn "Usa solo minúsculas, dígitos, guion y guion bajo; empieza por letra."
  else
    break
  fi
done

# Derivados
AGENT_UPPER="${AGENT_NAME^^}"
AGENT_TITLE="${AGENT_NAME^}"

# ---- VMID ----
while :; do
  read -rp "vmid del LXC: " VMID
  if [[ -z "${VMID}" ]]; then
    log_warn "No puede estar vacío."
  elif [[ ! "${VMID}" =~ ^[0-9]+$ ]]; then
    log_warn "El vmid debe ser numérico."
  else
    break
  fi
done

ask() {
  # ask VARNAME "Pregunta" "default"
  local __var=$1; local __prompt=$2; local __default=${3:-}
  local __input
  while :; do
    if [[ -n "${__default}" ]]; then
      read -rp "${__prompt} [${__default}]: " __input
      __input="${__input:-${__default}}"
    else
      read -rp "${__prompt}: " __input
    fi
    if [[ -z "${__input}" ]]; then
      log_warn "No puede estar vacío."
    else
      printf -v "${__var}" '%s' "${__input}"
      break
    fi
  done
}

ask_secret() {
  # ask_secret VARNAME "Pregunta"  — entrada oculta (read -s)
  local __var=$1; local __prompt=$2; local __input
  while :; do
    read -rsp "${__prompt}: " __input; echo ""
    if [[ -z "${__input}" ]]; then
      log_warn "No puede estar vacío."
    else
      printf -v "${__var}" '%s' "${__input}"
      break
    fi
  done
}

ask LXC_HOSTNAME    "Hostname del LXC"                  "ClaudeAgent${AGENT_TITLE}"
ask RAM_MB          "RAM en MB"                         "5120"
ask CORES           "Número de cores"                   "5"
ask SWAP_MB         "Swap en MB"                        "2048"
ask DISK_GB         "Disco en GB"                       "20"
ask STORAGE         "Storage de Proxmox"                "local-lvm"
ask BRIDGE          "Bridge de red"                     "vmbr0"
ask IP_ADDRESS      "IP del LXC (formato 192.168.1.X/24)"  ""
ask GATEWAY         "Gateway"                           "192.168.1.1"
ask DNS             "DNS"                               "8.8.8.8"
ask_secret PG_PASSWORD        "Password de PostgreSQL (oculto)"
ask_secret TELEGRAM_BOT_TOKEN "Bot token de Telegram (oculto)"
ask TELEGRAM_CHAT_ID  "Chat ID del propietario"         ""
ask OWNER_NAME        "Nombre del propietario (para la BD)"  ""
read -rp "Clave pública SSH del propietario (opcional, ENTER para omitir): " OWNER_SSH_KEY
ask DEPLOY_SRC        "Directorio con los templates"    "${SCRIPT_DIR}"
ask PROXMOX_TEMPLATE  "Template LXC"                    "local:vztmpl/debian-12-standard_12.12-1_amd64.tar.zst"

# Validar que el directorio de templates existe
if [[ ! -d "${DEPLOY_SRC}" ]]; then
  log_fail "El directorio de templates no existe: ${DEPLOY_SRC}"
  exit 1
fi

DEPLOY_TMP="/tmp/deploy-${AGENT_NAME}-${VMID}"

# Borrar ficheros temporales con secretos al salir (éxito o error)
cleanup_tmp() {
  rm -f "/tmp/init-db-${AGENT_NAME}-${VMID}.sql" 2>/dev/null || true
  rm -f "/tmp/secrets-${AGENT_NAME}-${VMID}.env" 2>/dev/null || true
  rm -f "/tmp/mcp-${AGENT_NAME}-${VMID}.json"   2>/dev/null || true
}
trap cleanup_tmp EXIT

# ---- Resumen ----
echo ""
log_info "------------------------------------------------------------"
log_info "  Resumen de la instalación"
log_info "------------------------------------------------------------"
cat <<EOF
  Agente            : ${AGENT_NAME}  (UPPER=${AGENT_UPPER}, Title=${AGENT_TITLE})
  vmid              : ${VMID}
  Hostname          : ${LXC_HOSTNAME}
  Recursos          : ${CORES} cores, ${RAM_MB} MB RAM, ${SWAP_MB} MB swap, ${DISK_GB} GB disco
  Storage / Bridge  : ${STORAGE} / ${BRIDGE}
  Red               : ip=${IP_ADDRESS} gw=${GATEWAY} dns=${DNS}
  Telegram chat_id  : ${TELEGRAM_CHAT_ID}
  Owner name        : ${OWNER_NAME}
  PG password       : (oculto, ${#PG_PASSWORD} caracteres)
  Bot token         : (oculto, ${#TELEGRAM_BOT_TOKEN} caracteres)
  Templates (src)   : ${DEPLOY_SRC}
  Template LXC      : ${PROXMOX_TEMPLATE}
EOF

echo ""
read -rp "¿Continuar? [s/N] " CONFIRM
if [[ ! "${CONFIRM}" =~ ^[sSyY]$ ]]; then
  echo "Abortado."
  exit 0
fi

# =============================================================================
# SECCIÓN 2 — LXC base (STEP_01-05)
# =============================================================================

step_01_create_lxc() {
  pct create "${VMID}" "${PROXMOX_TEMPLATE}" \
    --hostname "${LXC_HOSTNAME}" \
    --cores "${CORES}" \
    --memory "${RAM_MB}" \
    --swap "${SWAP_MB}" \
    --rootfs "${STORAGE}:${DISK_GB}" \
    --net0 "name=eth0,bridge=${BRIDGE},ip=${IP_ADDRESS},gw=${GATEWAY}" \
    --nameserver "${DNS}" \
    --unprivileged 1 \
    --features nesting=1 \
    --ostype debian
}

step_02_start_lxc() {
  pct start "${VMID}"
  echo "Esperando a que el LXC responda a 'pct exec'..."
  local i
  for i in $(seq 1 30); do
    if pct exec "${VMID}" -- true 2>/dev/null; then
      log_ok "LXC operativo (intento ${i})."
      return 0
    fi
    sleep 2
  done
  log_fail "El LXC no respondió tras 30 intentos."
  return 1
}

step_03_ssh_dns() {
  lxc_exec "sed -i 's/#PasswordAuthentication yes/PasswordAuthentication yes/' /etc/ssh/sshd_config"
  lxc_exec "grep -q '^PermitRootLogin yes' /etc/ssh/sshd_config || echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config"
  lxc_exec "systemctl restart ssh"
  # §1.8 paso 3: clave SSH del propietario (opcional)
  if [[ -n "${OWNER_SSH_KEY}" ]]; then
    lxc_exec "mkdir -p /root/.ssh && chmod 700 /root/.ssh"
    lxc_exec "grep -qxF '${OWNER_SSH_KEY}' /root/.ssh/authorized_keys 2>/dev/null || echo '${OWNER_SSH_KEY}' >> /root/.ssh/authorized_keys"
    lxc_exec "chmod 600 /root/.ssh/authorized_keys"
    log_ok "Clave pública SSH añadida a /root/.ssh/authorized_keys."
  else
    log_warn "Sin clave SSH del propietario. Acceso al LXC solo vía pct exec o contraseña root."
  fi
}

step_04_apt_update() {
  lxc_exec "export DEBIAN_FRONTEND=noninteractive && apt update && apt upgrade -y"
}

step_05_deps() {
  lxc_exec "export DEBIAN_FRONTEND=noninteractive && apt install -y curl unzip python3 python3-pip python3-psycopg2 python3-pytest postgresql apparmor apparmor-utils locales sudo"
  lxc_exec "grep -q '^en_US.UTF-8' /etc/locale.gen 2>/dev/null || echo 'en_US.UTF-8 UTF-8' >> /etc/locale.gen"
  lxc_exec "locale-gen en_US.UTF-8 && update-locale LANG=en_US.UTF-8"
}

run_step STEP_01 "Crear LXC en Proxmox"                       step_01_create_lxc
run_step STEP_02 "Arrancar LXC y esperar a que esté up"       step_02_start_lxc
run_step STEP_03 "Habilitar SSH root + DNS"                   step_03_ssh_dns
run_step STEP_04 "apt update + upgrade"                       step_04_apt_update
run_step STEP_05 "Instalar dependencias del sistema"         step_05_deps

# =============================================================================
# SECCIÓN 3 — Usuario, base de datos y setup del agente (STEP_06-15)
# =============================================================================

step_06_user_dirs() {
  lxc_exec "id ${AGENT_NAME} >/dev/null 2>&1 || useradd -m -s /usr/sbin/nologin -u 1000 ${AGENT_NAME}"
  lxc_exec "mkdir -p /home/${AGENT_NAME}/{claude,workspace/{docs/{improvements,incidentes,planes,tareas},tests,scripts/{hooks,lib}},apps/{bin,lib,share},data/{postgresql,cache},logs,tmp}"
  lxc_exec "chown -R ${AGENT_NAME}:${AGENT_NAME} /home/${AGENT_NAME}"
}

step_07_postgres_relocate() {
  # Asegurar locale en_US.UTF-8 disponible (necesario para pg_createcluster).
  # Idempotente: si STEP_05 ya lo instaló, esto es un no-op.
  lxc_exec "export DEBIAN_FRONTEND=noninteractive && apt install -y locales 2>/dev/null || true"
  lxc_exec "grep -q '^en_US.UTF-8' /etc/locale.gen 2>/dev/null || echo 'en_US.UTF-8 UTF-8' >> /etc/locale.gen"
  lxc_exec "locale-gen en_US.UTF-8 && update-locale LANG=en_US.UTF-8"

  lxc_exec "usermod -aG ${AGENT_NAME} postgres"
  lxc_exec "chmod 750 /home/${AGENT_NAME}/data"
  lxc_exec "mkdir -p /home/${AGENT_NAME}/data/postgresql"
  lxc_exec "chown postgres:postgres /home/${AGENT_NAME}/data/postgresql"
  lxc_exec "
    set -e
    # Detectar versión desde los binarios instalados (robusto: no desaparece con pg_dropcluster)
    PG_VERSION=\$(ls /usr/lib/postgresql/ | grep -E '^[0-9]+$' | sort -n | tail -1)
    if [[ -z \"\${PG_VERSION}\" ]]; then
      echo '[ERROR] No se encontró versión de PostgreSQL instalada en /usr/lib/postgresql/'
      exit 1
    fi
    echo "PostgreSQL versión detectada: \${PG_VERSION}"
    # Idempotencia: si el cluster ya apunta al datadir correcto, solo asegurar que esté up
    CURRENT_DATADIR=\$(pg_lsclusters 2>/dev/null | awk \"/^\${PG_VERSION}[[:space:]].*main/ {print \\\$6}\")
    if [[ \"\${CURRENT_DATADIR}\" == '/home/${AGENT_NAME}/data/postgresql' ]]; then
      echo 'Cluster ya en el datadir del agente; arrancando.'
      systemctl start postgresql 2>/dev/null || true
    else
      systemctl stop postgresql 2>/dev/null || true
      # Eliminar cluster existente en cualquier estado parcial
      if pg_lsclusters 2>/dev/null | grep -q \"^\${PG_VERSION}[[:space:]].*main\"; then
        pg_dropcluster --stop \${PG_VERSION} main 2>/dev/null || true
      fi
      # Limpiar datos parciales en el destino
      rm -rf /home/${AGENT_NAME}/data/postgresql/*
      pg_createcluster -d /home/${AGENT_NAME}/data/postgresql \${PG_VERSION} main
      systemctl start postgresql
      # postgresql.service es un oneshot wrapper en Debian: termina inmediatamente
      # y los clusters corren como postgresql@<ver>-main.service. Verificamos el cluster real.
      sleep 2
      if ! pg_lsclusters 2>/dev/null | grep -q '\bmain\b.*online'; then
        echo '[ERROR] El cluster main no quedó online tras systemctl start postgresql'
        pg_lsclusters 2>/dev/null || true
        journalctl -u \"postgresql@\${PG_VERSION}-main\" -n 20 --no-pager 2>/dev/null || true
        exit 1
      fi
    fi
  "
  # Verificar con pg_lsclusters (no systemctl is-active, que es un oneshot en Debian)
  lxc_exec "pg_lsclusters 2>/dev/null | grep -q '\bmain\b.*online' && echo 'Cluster online OK'"
}

step_08_init_db() {
  # Generar init-db.sql con placeholders sustituidos en el host, en un fichero temporal.
  local src_sql="${DEPLOY_SRC}/fase-0/init-db.sql"
  local tmp_sql="/tmp/init-db-${AGENT_NAME}-${VMID}.sql"
  if [[ ! -f "${src_sql}" ]]; then
    log_fail "No existe ${src_sql}"
    return 1
  fi
  # Sustituir <agent>/<AGENT>/<Agent> y el placeholder de password.
  sed -e "s|<AGENT>|${AGENT_UPPER}|g" \
      -e "s|<Agent>|${AGENT_TITLE}|g" \
      -e "s|<agent>|${AGENT_NAME}|g" \
      "${src_sql}" > "${tmp_sql}"
  # Sustituir placeholders con perl (soporta caracteres especiales en valores).
  PG_PASSWORD="${PG_PASSWORD}" perl -i -pe 's/\Q<SUSTITUIR_PASSWORD>\E/$ENV{PG_PASSWORD}/g' "${tmp_sql}"
  TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID}" perl -i -pe 's/\Q<owner_chat_id>\E/$ENV{TELEGRAM_CHAT_ID}/g' "${tmp_sql}"
  OWNER_NAME="${OWNER_NAME}" perl -i -pe 's/\Q<owner_name>\E/$ENV{OWNER_NAME}/g' "${tmp_sql}"

  # Copiar al LXC y ejecutar
  pct push "${VMID}" "${tmp_sql}" /tmp/init-db.sql --perms 600
  lxc_exec "chown postgres:postgres /tmp/init-db.sql"
  lxc_exec "su -s /bin/bash postgres -c 'psql -v ON_ERROR_STOP=1 -f /tmp/init-db.sql'"
  # Verificar tablas
  lxc_exec "su -s /bin/bash postgres -c 'psql -d agents -c \"\\\\dt\"'"
  # Limpiar el SQL con el password en claro (host y LXC)
  rm -f "${tmp_sql}"
  lxc_exec "rm -f /tmp/init-db.sql"
}

step_16_node() {
  lxc_exec "
    set -e
    NODE_VERSION='v22.11.0'
    cd /tmp
    rm -rf node-\${NODE_VERSION}-linux-x64
    curl -fsSL \"https://nodejs.org/dist/\${NODE_VERSION}/node-\${NODE_VERSION}-linux-x64.tar.xz\" | tar -xJ
    install -m 755 node-\${NODE_VERSION}-linux-x64/bin/node /home/${AGENT_NAME}/apps/bin/node
    cp -r node-\${NODE_VERSION}-linux-x64/lib/node_modules /home/${AGENT_NAME}/apps/lib/
    ln -sf /home/${AGENT_NAME}/apps/lib/node_modules/npm/bin/npm-cli.js /home/${AGENT_NAME}/apps/bin/npm
    chown -R ${AGENT_NAME}:${AGENT_NAME} /home/${AGENT_NAME}/apps
    /home/${AGENT_NAME}/apps/bin/node --version
  "
}

step_17_bun() {
  lxc_exec "
    set -e
    export BUN_INSTALL=/home/${AGENT_NAME}/apps
    curl -fsSL https://bun.sh/install | bash
    chown ${AGENT_NAME}:${AGENT_NAME} /home/${AGENT_NAME}/apps/bin/bun
    /home/${AGENT_NAME}/apps/bin/bun --version
    # Symlink en PATH del sistema para que Claude Code encuentre bun al lanzar plugins.
    # El PATH por defecto del LXC es /sbin:/bin:/usr/sbin:/usr/bin — sin /home/nox/apps/bin.
    ln -sf /home/${AGENT_NAME}/apps/bin/bun /usr/bin/bun
  "
}

step_18_mcp_postgres() {
  lxc_exec "
    set -e
    export PATH=/home/${AGENT_NAME}/apps/bin:\$PATH
    NPM_CONFIG_PREFIX=/home/${AGENT_NAME}/apps /home/${AGENT_NAME}/apps/bin/npm install -g @modelcontextprotocol/server-postgres
    chown -R ${AGENT_NAME}:${AGENT_NAME} /home/${AGENT_NAME}/apps
    ls /home/${AGENT_NAME}/apps/bin/mcp-server-postgres
  "
}

step_20_telegram_access() {
  # Escribe el access.json definitivo con allowlist (sin pairing).
  lxc_exec "cat > /home/${AGENT_NAME}/claude/.claude/channels/telegram/access.json <<'EOF'
{\"dmPolicy\":\"allowlist\",\"allowFrom\":[\"${TELEGRAM_CHAT_ID}\"],\"groups\":{},\"pending\":{}}
EOF"
  lxc_exec "chown ${AGENT_NAME}:${AGENT_NAME} /home/${AGENT_NAME}/claude/.claude/channels/telegram/access.json"
  log_ok "access.json escrito con dmPolicy:allowlist y chat_id ${TELEGRAM_CHAT_ID}."
}

step_09_claude_code() {
  lxc_exec "su -s /bin/bash ${AGENT_NAME} -c 'export HOME=/home/${AGENT_NAME}/claude && curl -fsSL https://claude.ai/install.sh | bash'"
  lxc_exec "chown -R ${AGENT_NAME}:${AGENT_NAME} /home/${AGENT_NAME}/claude"
  lxc_exec "/home/${AGENT_NAME}/claude/.local/bin/claude --version"
}

step_10_oauth() {
  manual_box
  cat <<EOF
${BOLD}OAuth interactivo de Claude Code${RESET}

Claude Code requiere autenticación OAuth manual (navegador). NO se puede automatizar.

Abre OTRA terminal en el host Proxmox y ejecuta:

  ${GREEN}pct exec ${VMID} -- su -s /bin/bash ${AGENT_NAME} -c '\\
    export HOME=/home/${AGENT_NAME}/claude; \\
    export PATH=/home/${AGENT_NAME}/apps/bin:/home/${AGENT_NAME}/claude/.local/bin:/usr/local/bin:/usr/bin:/bin; \\
    cd /home/${AGENT_NAME}/claude; \\
    claude'${RESET}

En la sesión interactiva:
  1. Selecciona "Yes, I trust this folder" para /home/${AGENT_NAME}/claude
  2. Completa el OAuth en el navegador con la cuenta Claude Pro
  3. Sal con Ctrl+C una vez autenticado

Importante: ejecútalo como el usuario ${AGENT_NAME} (el comando de arriba ya lo hace con su),
para que la sesión OAuth quede con el uid correcto (User=${AGENT_NAME} en el servicio).
EOF
  echo ""
  read -rp "Pulsa ENTER cuando hayas completado el OAuth..."
  # Verificar que existe el directorio .claude con contenido
  if lxc_exec "test -d /home/${AGENT_NAME}/claude/.claude && [ -n \"\$(ls -A /home/${AGENT_NAME}/claude/.claude 2>/dev/null)\" ]"; then
    log_ok "Existe /home/${AGENT_NAME}/claude/.claude con contenido."
    return 0
  else
    log_fail "No se encontró /home/${AGENT_NAME}/claude/.claude con contenido. ¿Completaste el OAuth?"
    return 1
  fi
}

run_step STEP_06 "Crear usuario y estructura de directorios"   step_06_user_dirs
run_step STEP_07 "Configurar PostgreSQL (PGDATA en el home)"   step_07_postgres_relocate
run_step STEP_08 "Inicializar base de datos"                   step_08_init_db
# STEP_16 (Node.js), STEP_17 (Bun) y STEP_18 (MCP) se ejecutan después del OAuth,
# junto con la instalación del plugin Telegram y el access.json.
run_step STEP_09 "Instalar Claude Code"                        step_09_claude_code
run_step STEP_10 "OAuth interactivo de Claude Code (manual)"   step_10_oauth

# =============================================================================
# SECCIÓN 4 — Fase C: Secretos (pasos 14-15)
# =============================================================================

step_11_secrets_file() {
  lxc_exec "mkdir -p /etc/${AGENT_NAME}"
  lxc_exec "touch /etc/${AGENT_NAME}/secrets.env"
  lxc_exec "chmod 640 /etc/${AGENT_NAME}/secrets.env"
  lxc_exec "chown root:${AGENT_NAME} /etc/${AGENT_NAME}/secrets.env"
}

step_12_secrets_fill() {
  # Escribir secrets.env SIN que los secretos pasen por stdout/argv visibles.
  # Generamos el fichero localmente con heredoc y lo empujamos con pct push.
  local tmp_secrets="/tmp/secrets-${AGENT_NAME}-${VMID}.env"
  umask 077
  cat > "${tmp_secrets}" <<EOF
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}
POSTGRES_CONNECTION_STRING=postgresql://${AGENT_NAME}:${PG_PASSWORD}@localhost:5432/agents
EOF
  pct push "${VMID}" "${tmp_secrets}" "/etc/${AGENT_NAME}/secrets.env" --perms 640
  lxc_exec "chown root:${AGENT_NAME} /etc/${AGENT_NAME}/secrets.env"
  rm -f "${tmp_secrets}"
  # Verificar (sin imprimir el contenido): solo que las claves obligatorias están
  lxc_exec "grep -q '^TELEGRAM_BOT_TOKEN=' /etc/${AGENT_NAME}/secrets.env && grep -q '^POSTGRES_CONNECTION_STRING=' /etc/${AGENT_NAME}/secrets.env"
}

run_step STEP_11 "Crear /etc/${AGENT_NAME}/secrets.env"        step_11_secrets_file
run_step STEP_12 "Rellenar secrets.env (secretos)"            step_12_secrets_fill

# =============================================================================
# SECCIÓN 5 — Fase D: Plugin Telegram (pasos 16-18)
# =============================================================================

step_19_telegram_plugin() {
  lxc_exec "su -s /bin/bash ${AGENT_NAME} -c '
    export HOME=/home/${AGENT_NAME}/claude
    export PATH=/home/${AGENT_NAME}/apps/bin:/home/${AGENT_NAME}/claude/.local/bin:/usr/local/bin:/usr/bin:/bin
    claude plugin install telegram@claude-plugins-official'"
  lxc_exec "chown -R ${AGENT_NAME}:${AGENT_NAME} /home/${AGENT_NAME}/claude/.claude/plugins"
  lxc_exec "su -s /bin/bash ${AGENT_NAME} -c 'HOME=/home/${AGENT_NAME}/claude PATH=/home/${AGENT_NAME}/apps/bin:/home/${AGENT_NAME}/claude/.local/bin:/usr/local/bin:/usr/bin:/bin claude plugin list'"
}

step_13_channel_dir() {
  # v2: solo crea el directorio. El access.json se escribe en step_20_telegram_access
  # (STEP_20), después de instalar el plugin Telegram.
  lxc_exec "mkdir -p /home/${AGENT_NAME}/claude/.claude/channels/telegram"
  lxc_exec "chown -R ${AGENT_NAME}:${AGENT_NAME} /home/${AGENT_NAME}/claude/.claude/channels"
  lxc_exec "grep -q 'TELEGRAM_BOT_TOKEN' /etc/${AGENT_NAME}/secrets.env"
}

step_14_trust() {
  # Configura .claude.json: acepta trust y pre-registra los MCPs.
  # Los servidores van aquí (no en .mcp.json) para evitar el diálogo interactivo
  # de aprobación que bloquea el servicio al arrancar sin TTY.
  lxc_exec "python3 - <<'PYEOF'
import json
path = '/home/${AGENT_NAME}/claude/.claude.json'
try:
    with open(path) as f:
        d = json.load(f)
except FileNotFoundError:
    d = {}
proj = d.setdefault('projects', {}).setdefault('/home/${AGENT_NAME}/claude', {})
proj['hasTrustDialogAccepted'] = True
proj['mcpServers'] = {
    'postgres': {
        'command': '/home/${AGENT_NAME}/apps/bin/mcp-server-postgres',
        'args': ['postgresql://${AGENT_NAME}:${PG_PASSWORD}@localhost:5432/agents']
    },
    'context7': {
        'command': 'npx',
        'args': ['-y', '@upstash/context7-mcp@latest']
    }
}
with open(path, 'w') as f:
    json.dump(d, f, indent=2)
print('OK')
PYEOF"
  lxc_exec "chown ${AGENT_NAME}:${AGENT_NAME} /home/${AGENT_NAME}/claude/.claude.json"
}

run_step STEP_13 "Preparar directorio del canal Telegram"      step_13_channel_dir
run_step STEP_14 "Aceptar trust de /home/${AGENT_NAME}/claude" step_14_trust
# STEP_19 (plugin Telegram) se instala después de bun (STEP_17) — el plugin requiere bun.

# =============================================================================
# SECCIÓN 6 — Fase E: Workspace y harness (pasos 19-26)
# =============================================================================

# Construye el árbol de deploy en DEPLOY_TMP con los placeholders sustituidos.
prepare_deploy_tmp() {
  rm -rf "${DEPLOY_TMP}"
  mkdir -p "${DEPLOY_TMP}"
  rsync -a "${DEPLOY_SRC}/" "${DEPLOY_TMP}/"

  # Sustitución global de placeholders en el CONTENIDO de todos los ficheros.
  # Orden importante: <AGENT> y <Agent> antes que <agent> (todos son distintos
  # literalmente, pero mantenemos el orden por claridad).
  find "${DEPLOY_TMP}/" -type f -print0 | xargs -0 sed -i \
    -e "s|<AGENT>|${AGENT_UPPER}|g" \
    -e "s|<Agent>|${AGENT_TITLE}|g" \
    -e "s|<agent>|${AGENT_NAME}|g"

  # Nota v2: el bloque de onboarding NO se inyecta aquí.
  # Se añade al CLAUDE.md del LXC en step_20_telegram_access, después del OAuth y de
  # instalar bun/MCP, para que el primer mensaje de Telegram dispare el onboarding.
}

# Empuja un fichero del DEPLOY_TMP al LXC, creando el directorio destino primero.
push_file() {
  # push_file SRC_REL DEST_ABS
  local src="${DEPLOY_TMP}/$1"
  local dest="$2"
  if [[ ! -f "${src}" ]]; then
    log_warn "No existe (se omite): ${src}"
    return 0
  fi
  lxc_exec "mkdir -p \"$(dirname "${dest}")\""
  pct push "${VMID}" "${src}" "${dest}"
}

# Empuja todos los ficheros de un directorio del DEPLOY_TMP a un directorio del LXC.
push_dir_contents() {
  # push_dir_contents SRC_REL_DIR DEST_DIR
  local srcdir="${DEPLOY_TMP}/$1"
  local destdir="$2"
  if [[ ! -d "${srcdir}" ]]; then
    log_warn "No existe directorio (se omite): ${srcdir}"
    return 0
  fi
  lxc_exec "mkdir -p \"${destdir}\""
  local f rel
  # Recorre recursivamente, preservando subrutas relativas.
  while IFS= read -r -d '' f; do
    rel="${f#"${srcdir}"/}"
    lxc_exec "mkdir -p \"$(dirname "${destdir}/${rel}")\""
    pct push "${VMID}" "${f}" "${destdir}/${rel}"
  done < <(find "${srcdir}" -type f -print0)
}

step_15_workspace() {
  prepare_deploy_tmp

  local AH="/home/${AGENT_NAME}"

  # CLAUDE.md (con onboarding ya añadido)
  push_file "fase-0/CLAUDE.md" "${AH}/claude/CLAUDE.md"

  # scripts/lib (fase-0, fase-1, fase-futura)
  push_dir_contents "fase-0/scripts/lib"      "${AH}/workspace/scripts/lib"
  push_dir_contents "fase-1/scripts/lib"      "${AH}/workspace/scripts/lib"
  push_dir_contents "fase-futura/scripts/lib" "${AH}/workspace/scripts/lib"

  # scripts/hooks (fase-1, fase-2)
  push_dir_contents "fase-1/scripts/hooks" "${AH}/workspace/scripts/hooks"
  push_dir_contents "fase-2/scripts/hooks" "${AH}/workspace/scripts/hooks"

  # tests (fase-2)
  push_dir_contents "fase-2/tests" "${AH}/workspace/tests"

  # init-db.sql también al workspace (referencia; ya con placeholders sustituidos,
  # pero el password placeholder original — el real ya se usó en STEP_08).
  push_file "fase-0/init-db.sql" "${AH}/workspace/scripts/lib/init-db.sql"

  # settings.json y settings-background.json
  push_file "fase-1/settings.json"            "${AH}/claude/.claude/settings.json"
  push_file "fase-1/settings-background.json" "${AH}/claude/.claude/settings-background.json"

  # El servidor MCP de postgres va en .claude.json (step_14_trust), no en .mcp.json.
  # .mcp.json dispara un diálogo interactivo de aprobación que bloquea el servicio.

  # Agentes y skills (fase-2) — placeholders ya sustituidos por prepare_deploy_tmp
  push_dir_contents "fase-2/agents" "${AH}/claude/.claude/agents"
  push_dir_contents "fase-2/skills" "${AH}/claude/.claude/skills"

  # Permisos de ejecución a scripts y hooks
  lxc_exec "chmod +x ${AH}/workspace/scripts/hooks/*.py 2>/dev/null || true"
  lxc_exec "chmod +x ${AH}/workspace/scripts/lib/*.sh 2>/dev/null || true"

  # Propietario final
  lxc_exec "chown -R ${AGENT_NAME}:${AGENT_NAME} /home/${AGENT_NAME}"
}

run_step STEP_15 "Copiar workspace y harness al LXC"     step_15_workspace

# =============================================================================
# SECCIÓN 7 — Fase F: Servicios y seguridad (pasos 27-36)
# =============================================================================

# Los pasos 27-29 leen de DEPLOY_TMP. Si la sección anterior no se ejecutó,
# o el árbol está incompleto (p.ej. borrado manual), lo regeneramos.
# Comprobamos ficheros clave, no solo que el directorio exista.
if [[ ! -f "${DEPLOY_TMP}/fase-0/etc/sudoers.d/agent" ]] || \
   [[ ! -f "${DEPLOY_TMP}/fase-0/systemd/claude-telegram.service" ]]; then
  log_info "DEPLOY_TMP incompleto o ausente — regenerando..."
  prepare_deploy_tmp
fi

step_21_systemd_units() {
  local sd="${DEPLOY_TMP}/fase-0/systemd"
  local unit
  for unit in claude-telegram.service heartbeat.timer heartbeat.service midnight.timer midnight.service; do
    if [[ -f "${sd}/${unit}" ]]; then
      pct push "${VMID}" "${sd}/${unit}" "/etc/systemd/system/${unit}" --perms 644
    else
      log_fail "Falta el unit file: ${sd}/${unit}"
      return 1
    fi
  done
}

step_22_sudoers() {
  local src="${DEPLOY_TMP}/fase-0/etc/sudoers.d/agent"
  if [[ ! -f "${src}" ]]; then
    log_fail "No existe el sudoers template: ${src}"
    return 1
  fi
  pct push "${VMID}" "${src}" "/etc/sudoers.d/${AGENT_NAME}" --perms 440
  lxc_exec "chmod 440 /etc/sudoers.d/${AGENT_NAME}"
  lxc_exec "visudo -cf /etc/sudoers.d/${AGENT_NAME}"
}

step_23_apparmor() {
  local src="${DEPLOY_TMP}/fase-1/apparmor/apparmor-profile"
  if [[ ! -f "${src}" ]]; then
    log_fail "No existe el perfil AppArmor: ${src}"
    return 1
  fi
  pct push "${VMID}" "${src}" "/etc/apparmor.d/home.${AGENT_NAME}.claude" --perms 644
  # AppArmor puede no estar disponible en LXC unprivileged sin namespacing en el host.
  # Degradamos a warning para no abortar una instalación por lo demás correcta.
  if lxc_exec "apparmor_parser -r /etc/apparmor.d/home.${AGENT_NAME}.claude" 2>/dev/null; then
    lxc_exec "aa-complain /etc/apparmor.d/home.${AGENT_NAME}.claude" 2>/dev/null || true
    echo ""
    log_warn "AppArmor cargado en modo COMPLAIN (loguea pero NO bloquea)."
    log_warn "Ejercita el agente (Telegram, /reset, heartbeat) y revisa denegaciones con:"
    log_warn "    pct exec ${VMID} -- journalctl -k | grep -i apparmor"
    log_warn "Solo cuando no haya denegaciones legítimas, pasa a enforce manualmente:"
    log_warn "    pct exec ${VMID} -- aa-enforce /etc/apparmor.d/home.${AGENT_NAME}.claude"
  else
    echo ""
    log_warn "AppArmor no disponible o no soportado en este LXC (LXC unprivileged sin namespacing)."
    log_warn "El perfil está copiado en /etc/apparmor.d/ pero NO está activo."
    log_warn "Para activarlo: habilita AppArmor namespacing en el host Proxmox y re-ejecuta este paso."
  fi
}

step_24_enable_start() {
  lxc_exec "systemctl daemon-reload"
  lxc_exec "systemctl enable claude-telegram.service heartbeat.timer midnight.timer"
  lxc_exec "systemctl start claude-telegram.service heartbeat.timer midnight.timer"
}

step_25_verify_service() {
  echo ""
  lxc_exec "systemctl status claude-telegram.service --no-pager" || true
  echo ""
  log_info "Últimas líneas del log del servicio:"
  lxc_exec "journalctl -u claude-telegram.service -n 80 --no-pager" || true
  # Criterio: el servicio debe estar activo (running). El pairing aún no está hecho.
  if lxc_exec "systemctl is-active claude-telegram.service" | grep -q '^active'; then
    log_ok "claude-telegram.service está active."
    return 0
  else
    log_fail "claude-telegram.service NO está active. Revisa el log de arriba."
    return 1
  fi
}

# =============================================================================
# SECCIÓN 4 — Servicios y arranque (STEP_16-28)
# =============================================================================
run_step STEP_16 "Instalar Node.js"                            step_16_node
run_step STEP_17 "Instalar Bun"                                step_17_bun
run_step STEP_18 "Instalar MCP PostgreSQL"                     step_18_mcp_postgres
run_step STEP_19 "Instalar plugin Telegram"                    step_19_telegram_plugin
run_step STEP_20 "Configurar acceso Telegram (allowlist)"  step_20_telegram_access

run_step STEP_21 "Copiar y registrar unit files systemd"      step_21_systemd_units
run_step STEP_22 "Crear sudoers del agente"                   step_22_sudoers
# run_step STEP_23 "Cargar perfil AppArmor (modo complain)"     step_23_apparmor  # Pendiente: requiere namespacing AppArmor en host PVE
run_step STEP_24 "daemon-reload + enable + start"             step_24_enable_start
run_step STEP_25 "Verificar servicio arrancado"               step_25_verify_service

# =============================================================================
# SECCIÓN 8 — Fase G: Pairing Telegram (pasos 37-39)
# =============================================================================

step_26_telegram_verify() {
  # v2: el access.json ya tiene dmPolicy:allowlist (STEP_20).
  manual_box
  cat <<EOF
${BOLD}Verificar Telegram${RESET}

El acceso está configurado con allowlist (sin pairing).

Envía cualquier mensaje al bot desde Telegram y comprueba que el agente responde.

Si no responde en 30 segundos, revisa los logs:
   ${GREEN}pct exec ${VMID} -- journalctl -u claude-telegram.service -n 50${RESET}
   ${GREEN}pct exec ${VMID} -- tail -50 /home/${AGENT_NAME}/logs/claude-telegram.log${RESET}
EOF
  echo ""
  read -rp "Pulsa ENTER cuando el agente haya respondido correctamente..."
  lxc_exec "systemctl is-active claude-telegram.service" | grep -q '^active' || {
    log_warn "El servicio no está active. Arráncalo: pct exec ${VMID} -- systemctl start claude-telegram.service"
  }
  return 0
}

run_step STEP_26 "Emparejar Telegram (manual)"                step_26_telegram_verify

# =============================================================================
# SECCIÓN 9 — Verificación final (pasos 42-48)
# =============================================================================

step_27_final_checks() {
  echo ""
  log_info "Verificaciones automáticas:"

  echo ""
  log_info "[pytest] Suite de tests del harness:"
  lxc_exec "su -s /bin/bash ${AGENT_NAME} -c 'cd /home/${AGENT_NAME} && python3 -m pytest /home/${AGENT_NAME}/workspace/tests/ -q'" \
    || log_warn "pytest reportó fallos o el harness aún no está completo (§4 F0-F5). Revisar."

  echo ""
  log_info "[postgres] Conteo de agent_memory (criterio duro — debe responder sin error):"
  if lxc_exec "su -s /bin/bash ${AGENT_NAME} -c 'set -a; . /etc/${AGENT_NAME}/secrets.env; set +a; psql \"\$POSTGRES_CONNECTION_STRING\" -tAc \"SELECT COUNT(*) FROM agent_memory;\"'"; then
    log_ok "agent_memory accesible con el usuario ${AGENT_NAME}."
  else
    log_fail "No se puede consultar agent_memory. Verifica: connection string en secrets.env, RLS, usuario ${AGENT_NAME} en la BD."
    return 1
  fi

  echo ""
  log_info "[timers] heartbeat.timer debe figurar con NEXT calculado:"
  lxc_exec "systemctl list-timers heartbeat.timer --no-pager" || true

  echo ""
  log_info "[heartbeat] Disparar el oneshot manualmente una vez:"
  lxc_exec "systemctl start heartbeat.service" || log_warn "heartbeat.service falló al arrancar."
  lxc_exec "systemctl status heartbeat.service --no-pager" || true
  lxc_exec "journalctl -u heartbeat.service -n 50 --no-pager" || true

  echo ""
  log_info "Verificaciones MANUALES pendientes desde Telegram:"
  cat <<EOF
    - /context  → debe responder con el uso de contexto
    - /skills   → debe listar las skills disponibles
    - /reset    → el servicio debe reiniciarse y responder
    - tail -f /home/${AGENT_NAME}/logs/${AGENT_NAME}-permissions.log durante una interacción
EOF
  return 0
}

run_step STEP_27 "Verificación final"                         step_27_final_checks

# =============================================================================
# SECCIÓN 10 — Onboarding (último paso)
# =============================================================================

step_28_onboarding() {
  # Inyecta el bloque de onboarding en el CLAUDE.md del LXC.
  # Este es el último paso: el sistema ya está verificado y funcionando.
  # El próximo mensaje del usuario disparará el onboarding automáticamente.
  lxc_exec "cat >> /home/${AGENT_NAME}/claude/CLAUDE.md <<'ONBOARDING'

---

## Primer arranque — LEER ESTO ANTES DE RESPONDER

Este es el primer arranque del agente. El CLAUDE.md aún no contiene datos del usuario propietario.

**Antes de responder al primer mensaje**, el agente debe:
1. Avisar al usuario que es la primera sesión y que va a hacer unas preguntas de configuración
2. Preguntar de forma conversacional (no como formulario): nombre completo, fecha de nacimiento, familia cercana (pareja, hijos con nombres y fechas), nivel técnico, principales usos del agente
3. Preguntar el tono deseado: formal/informal, idioma preferido, estilo de respuesta
4. Una vez recogida la información, actualizar este CLAUDE.md con los datos reales (Edit tool)
5. Borrar esta sección \"Primer arranque\" del CLAUDE.md una vez completado el onboarding
6. Confirmar al usuario que la configuración está guardada

El agente NO debe responder preguntas normales hasta completar este flujo de onboarding.
ONBOARDING"
  lxc_exec "chown ${AGENT_NAME}:${AGENT_NAME} /home/${AGENT_NAME}/claude/CLAUDE.md"
  log_ok "Bloque de onboarding añadido a CLAUDE.md."
  manual_box
  cat <<EOF
${BOLD}Onboarding listo${RESET}

El CLAUDE.md incluye ahora la sección de onboarding.
El próximo mensaje que envíes al agente disparará el proceso de configuración inicial.
EOF
  echo ""
  read -rp "Pulsa ENTER para terminar..."
}

run_step STEP_28 "Inyectar onboarding en CLAUDE.md (último paso)" step_28_onboarding

# =============================================================================
# SECCIÓN 11 — Mensaje final
# =============================================================================

echo ""
log_info "============================================================"
log_ok   "Instalación v2 de '${AGENT_NAME}' completada (pasos automatizados)."
log_info "============================================================"
cat <<EOF

  ${BOLD}Resumen${RESET}
  - Agente   : ${AGENT_NAME}  (LXC vmid ${VMID}, hostname ${LXC_HOSTNAME})
  - IP       : ${IP_ADDRESS}
  - Servicio : claude-telegram.service
  ${BOLD}Acciones manuales pendientes / a confirmar${RESET}
  1. AppArmor: si está en modo COMPLAIN, tras ejercitar el agente sin denegaciones:
       pct exec ${VMID} -- aa-enforce /etc/apparmor.d/home.${AGENT_NAME}.claude
  2. Verificaciones desde Telegram: /context, /skills, /reset (ver arriba).
  3. Onboarding: en el primer mensaje, el agente preguntará los datos del propietario
     STEP_28 inyecta el bloque de onboarding — el primer mensaje lo disparará.
  4. Filtro anti-injection (§1.8 paso 22b): el userprompt-hook arranca en fail-open
     hasta que exista /home/${AGENT_NAME}/apps/bin/clean. Implementar según §1.8 paso 22b.
  5. Crons (Fase H) NO instalados — diferidos a §8 (proactividad por core_task).
  6. Secretos opcionales (UPTIME_KUMA_PUSH_URL, ${AGENT_UPPER}_INBOX_TOKEN, MQTT_BROKER,
     PGBACKREST_*) no se han escrito: añádelos a /etc/${AGENT_NAME}/secrets.env cuando
     despliegues sus secciones (§8.3, §9.9, §11.2, §11.4).

  Para re-ejecutar este script: vuelve a lanzarlo desde el principio.

EOF
