#!/bin/bash
#
# install-agent.sh — Provisioning completo de un agente Claude Code en un LXC de Proxmox.
#
# Materializa el checklist §1.8 de seccion-1-infraestructura.md (pasos 1-48), sustituyendo
# los placeholders <agent>/<AGENT>/<Agent> de todos los templates del directorio de deploy.
#
# Ejecutar EN EL HOST Proxmox (no dentro del LXC). Requiere: pct, pveam, rsync.
#
# Idempotente: guarda el progreso en /tmp/install-<agent>-<vmid>.checkpoint y salta los
# pasos ya completados al re-ejecutar. En caso de fallo, para con un mensaje claro; no hace
# rollback automático.

set -euo pipefail

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

# El fichero de checkpoint se fija una vez recogidos AGENT_NAME y VMID (sección 1).
CHECKPOINT_FILE=""

checkpoint_done() { echo "$1=done" >> "${CHECKPOINT_FILE}"; }
is_done()         { grep -q "^$1=done" "${CHECKPOINT_FILE}" 2>/dev/null; }

# run_step ID DESC COMANDO [ARGS...]
# Ejecuta el comando solo si el paso no está marcado como hecho. Para pasos complejos,
# define una función step_XX_*() y pásala como comando.
run_step() {
  local id=$1; local desc=$2; shift 2
  if is_done "$id"; then
    echo "${YELLOW}[SKIP]${RESET} $id — $desc"
    return 0
  fi
  echo ""
  echo "${BOLD}==> [$id] $desc${RESET}"
  if "$@"; then
    checkpoint_done "$id"
    log_ok "$id"
  else
    log_fail "$id falló. Corrígelo y re-ejecuta el script (los pasos completados se saltan)."
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
ask DEPLOY_SRC        "Directorio con los templates"    "/root/docs/claude-code-os/new/deploy"
ask PROXMOX_TEMPLATE  "Template LXC"                    "local:vztmpl/debian-12-standard_12.12-1_amd64.tar.zst"

# Validar que el directorio de templates existe
if [[ ! -d "${DEPLOY_SRC}" ]]; then
  log_fail "El directorio de templates no existe: ${DEPLOY_SRC}"
  exit 1
fi

# Fijar el fichero de checkpoint ahora que tenemos AGENT_NAME y VMID
CHECKPOINT_FILE="/tmp/install-${AGENT_NAME}-${VMID}.checkpoint"
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
  Checkpoint        : ${CHECKPOINT_FILE}
EOF

if [[ -f "${CHECKPOINT_FILE}" ]]; then
  echo ""
  log_warn "Ya existe un checkpoint para este agente. Pasos ya completados:"
  sed 's/=done//' "${CHECKPOINT_FILE}" | sed 's/^/    /'
  log_warn "Esos pasos se saltarán. Borra ${CHECKPOINT_FILE} para empezar de cero."
fi

echo ""
read -rp "¿Continuar? [s/N] " CONFIRM
if [[ ! "${CONFIRM}" =~ ^[sSyY]$ ]]; then
  echo "Abortado."
  exit 0
fi

# Crear el fichero de checkpoint si no existe (touch idempotente)
touch "${CHECKPOINT_FILE}"

# =============================================================================
# SECCIÓN 2 — Fase A: LXC base (pasos 1-5)
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
  lxc_exec "export DEBIAN_FRONTEND=noninteractive && apt install -y curl unzip python3 python3-pip python3-psycopg2 python3-pytest postgresql apparmor apparmor-utils"
}

run_step STEP_01 "Crear LXC en Proxmox"                       step_01_create_lxc
run_step STEP_02 "Arrancar LXC y esperar a que esté up"       step_02_start_lxc
run_step STEP_03 "Habilitar SSH root + DNS"                   step_03_ssh_dns
run_step STEP_04 "apt update + upgrade"                       step_04_apt_update
run_step STEP_05 "Instalar dependencias del sistema"         step_05_deps

# =============================================================================
# SECCIÓN 3 — Fase B: Usuario, estructura y base de datos (pasos 6-13)
# =============================================================================

step_06_user_dirs() {
  lxc_exec "id ${AGENT_NAME} >/dev/null 2>&1 || useradd -m -s /usr/sbin/nologin -u 1000 ${AGENT_NAME}"
  lxc_exec "mkdir -p /home/${AGENT_NAME}/{claude,workspace/{docs/{improvements,incidentes,planes,tareas},tests,scripts/{hooks,lib}},apps/{bin,lib,share},data/{postgresql,cache},logs,tmp}"
  lxc_exec "chown -R ${AGENT_NAME}:${AGENT_NAME} /home/${AGENT_NAME}"
}

step_07_postgres_relocate() {
  lxc_exec "usermod -aG ${AGENT_NAME} postgres"
  lxc_exec "chmod 750 /home/${AGENT_NAME}/data"
  lxc_exec "mkdir -p /home/${AGENT_NAME}/data/postgresql"
  lxc_exec "chown postgres:postgres /home/${AGENT_NAME}/data/postgresql"
  lxc_exec "
    set -e
    PG_VERSION=\$(ls /etc/postgresql/ | head -1)
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
    fi
  "
  lxc_exec "systemctl is-active postgresql"
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
  lxc_exec "sudo -u postgres psql -v ON_ERROR_STOP=1 -f /tmp/init-db.sql"
  # Verificar tablas
  lxc_exec "sudo -u postgres psql -d agents -c '\\dt'"
  # Limpiar el SQL con el password en claro (host y LXC)
  rm -f "${tmp_sql}"
  lxc_exec "rm -f /tmp/init-db.sql"
}

step_09_node() {
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

step_10_bun() {
  lxc_exec "
    set -e
    export BUN_INSTALL=/home/${AGENT_NAME}/apps
    curl -fsSL https://bun.sh/install | bash
    chown ${AGENT_NAME}:${AGENT_NAME} /home/${AGENT_NAME}/apps/bin/bun
    /home/${AGENT_NAME}/apps/bin/bun --version
  "
}

step_11_mcp_postgres() {
  lxc_exec "
    set -e
    NPM_CONFIG_PREFIX=/home/${AGENT_NAME}/apps /home/${AGENT_NAME}/apps/bin/npm install -g @modelcontextprotocol/server-postgres
    chown -R ${AGENT_NAME}:${AGENT_NAME} /home/${AGENT_NAME}/apps
    ls /home/${AGENT_NAME}/apps/bin/mcp-server-postgres
  "
}

step_12_claude_code() {
  lxc_exec "su -s /bin/bash ${AGENT_NAME} -c 'export HOME=/home/${AGENT_NAME}/claude && curl -fsSL https://claude.ai/install.sh | bash'"
  lxc_exec "chown -R ${AGENT_NAME}:${AGENT_NAME} /home/${AGENT_NAME}/claude"
  lxc_exec "/home/${AGENT_NAME}/claude/.local/bin/claude --version"
}

step_13_oauth() {
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
run_step STEP_09 "Instalar Node.js"                            step_09_node
run_step STEP_10 "Instalar Bun"                                step_10_bun
run_step STEP_11 "Instalar MCP PostgreSQL"                     step_11_mcp_postgres
run_step STEP_12 "Instalar Claude Code"                        step_12_claude_code
run_step STEP_13 "OAuth interactivo de Claude Code (manual)"   step_13_oauth

# =============================================================================
# SECCIÓN 4 — Fase C: Secretos (pasos 14-15)
# =============================================================================

step_14_secrets_file() {
  lxc_exec "mkdir -p /etc/${AGENT_NAME}"
  lxc_exec "touch /etc/${AGENT_NAME}/secrets.env"
  lxc_exec "chmod 640 /etc/${AGENT_NAME}/secrets.env"
  lxc_exec "chown root:${AGENT_NAME} /etc/${AGENT_NAME}/secrets.env"
}

step_15_secrets_fill() {
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

run_step STEP_14 "Crear /etc/${AGENT_NAME}/secrets.env"        step_14_secrets_file
run_step STEP_15 "Rellenar secrets.env (secretos)"            step_15_secrets_fill

# =============================================================================
# SECCIÓN 5 — Fase D: Plugin Telegram (pasos 16-18)
# =============================================================================

step_16_telegram_plugin() {
  lxc_exec "su -s /bin/bash ${AGENT_NAME} -c '
    export HOME=/home/${AGENT_NAME}/claude
    export PATH=/home/${AGENT_NAME}/apps/bin:/home/${AGENT_NAME}/claude/.local/bin:/usr/local/bin:/usr/bin:/bin
    claude plugin install telegram@claude-plugins-official'"
  lxc_exec "chown -R ${AGENT_NAME}:${AGENT_NAME} /home/${AGENT_NAME}/claude/.claude/plugins"
  lxc_exec "su -s /bin/bash ${AGENT_NAME} -c 'HOME=/home/${AGENT_NAME}/claude PATH=/home/${AGENT_NAME}/apps/bin:/home/${AGENT_NAME}/claude/.local/bin:/usr/local/bin:/usr/bin:/bin claude plugin list'"
}

step_17_channel_dir() {
  lxc_exec "mkdir -p /home/${AGENT_NAME}/claude/.claude/channels/telegram"
  lxc_exec "chown -R ${AGENT_NAME}:${AGENT_NAME} /home/${AGENT_NAME}/claude/.claude/channels"
  lxc_exec "grep -q 'TELEGRAM_BOT_TOKEN' /etc/${AGENT_NAME}/secrets.env"
}

step_18_trust() {
  lxc_exec "python3 - <<'PYEOF'
import json
path = '/home/${AGENT_NAME}/claude/.claude.json'
try:
    with open(path) as f:
        d = json.load(f)
except FileNotFoundError:
    d = {}
d.setdefault('projects', {}).setdefault('/home/${AGENT_NAME}/claude', {})['hasTrustDialogAccepted'] = True
with open(path, 'w') as f:
    json.dump(d, f, indent=2)
print('OK')
PYEOF"
  lxc_exec "chown ${AGENT_NAME}:${AGENT_NAME} /home/${AGENT_NAME}/claude/.claude.json"
}

run_step STEP_16 "Instalar plugin Telegram"                    step_16_telegram_plugin
run_step STEP_17 "Preparar directorio del canal Telegram"      step_17_channel_dir
run_step STEP_18 "Aceptar trust de /home/${AGENT_NAME}/claude" step_18_trust

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

  # Renombrar ficheros que tengan <agent> en su nombre (de hojas a raíz: -depth).
  find "${DEPLOY_TMP}/" -depth -name "*<agent>*" | while read -r f; do
    mv "$f" "$(echo "$f" | sed "s|<agent>|${AGENT_NAME}|g")"
  done

  # Inyectar la sección de onboarding al final del CLAUDE.md (solo en la copia temporal).
  local claude_md="${DEPLOY_TMP}/fase-0/CLAUDE.md"
  if [[ -f "${claude_md}" ]]; then
    cat >> "${claude_md}" <<'ONBOARDING'

---

## Primer arranque — LEER ESTO ANTES DE RESPONDER

Este es el primer arranque del agente. El CLAUDE.md aún no contiene datos del usuario propietario.

**Antes de responder al primer mensaje**, el agente debe:
1. Avisar al usuario que es la primera sesión y que va a hacer unas preguntas de configuración
2. Preguntar de forma conversacional (no como formulario): nombre completo, fecha de nacimiento, familia cercana (pareja, hijos con nombres y fechas), nivel técnico, principales usos del agente
3. Preguntar el tono deseado: formal/informal, idioma preferido, estilo de respuesta
4. Una vez recogida la información, actualizar este CLAUDE.md con los datos reales (Edit tool)
5. Borrar esta sección "Primer arranque" del CLAUDE.md una vez completado el onboarding
6. Confirmar al usuario que la configuración está guardada

El agente NO debe responder preguntas normales hasta completar este flujo de onboarding.
ONBOARDING
  fi
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

step_19_to_26_workspace() {
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

  # .mcp.json — registra el servidor MCP de PostgreSQL en el HOME de Claude Code.
  # Sin este fichero el agente arranca sin acceso a la BD vía MCP.
  local tmp_mcp="/tmp/mcp-${AGENT_NAME}-${VMID}.json"
  umask 077
  cat > "${tmp_mcp}" <<MCPEOF
{
  "mcpServers": {
    "postgres": {
      "command": "/home/${AGENT_NAME}/apps/bin/mcp-server-postgres",
      "args": ["postgresql://${AGENT_NAME}:${PG_PASSWORD}@localhost:5432/agents"]
    }
  }
}
MCPEOF
  pct push "${VMID}" "${tmp_mcp}" "${AH}/claude/.mcp.json" --perms 600
  lxc_exec "chown ${AGENT_NAME}:${AGENT_NAME} ${AH}/claude/.mcp.json"
  rm -f "${tmp_mcp}"

  # Agentes y skills (fase-2) — placeholders ya sustituidos por prepare_deploy_tmp
  push_dir_contents "fase-2/agents" "${AH}/claude/.claude/agents"
  push_dir_contents "fase-2/skills" "${AH}/claude/.claude/skills"

  # Permisos de ejecución a scripts y hooks
  lxc_exec "chmod +x ${AH}/workspace/scripts/hooks/*.py 2>/dev/null || true"
  lxc_exec "chmod +x ${AH}/workspace/scripts/lib/*.sh 2>/dev/null || true"

  # Propietario final
  lxc_exec "chown -R ${AGENT_NAME}:${AGENT_NAME} /home/${AGENT_NAME}"
}

run_step STEP_19_TO_26 "Copiar workspace y harness al LXC"     step_19_to_26_workspace

# =============================================================================
# SECCIÓN 7 — Fase F: Servicios y seguridad (pasos 27-36)
# =============================================================================

# Los pasos 27-29 leen de DEPLOY_TMP, que prepare_deploy_tmp() construye en /tmp.
# Si la sección anterior fue saltada por el checkpoint (re-ejecución) y /tmp fue
# limpiado entre intentos, DEPLOY_TMP puede no existir — lo regeneramos aquí.
[[ -d "${DEPLOY_TMP}" ]] || prepare_deploy_tmp

step_27_systemd_units() {
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

step_28_sudoers() {
  local src="${DEPLOY_TMP}/fase-0/etc/sudoers.d/${AGENT_NAME}"
  if [[ ! -f "${src}" ]]; then
    log_fail "No existe el sudoers template: ${src}"
    return 1
  fi
  pct push "${VMID}" "${src}" "/etc/sudoers.d/${AGENT_NAME}" --perms 440
  lxc_exec "chmod 440 /etc/sudoers.d/${AGENT_NAME}"
  lxc_exec "visudo -cf /etc/sudoers.d/${AGENT_NAME}"
}

step_29_apparmor() {
  local src="${DEPLOY_TMP}/fase-1/apparmor/home.${AGENT_NAME}.claude"
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

step_30_enable_start() {
  lxc_exec "systemctl daemon-reload"
  lxc_exec "systemctl enable claude-telegram.service heartbeat.timer midnight.timer"
  lxc_exec "systemctl start claude-telegram.service"
}

step_31_verify_service() {
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

run_step STEP_27 "Copiar y registrar unit files systemd"      step_27_systemd_units
run_step STEP_28 "Crear sudoers del agente"                   step_28_sudoers
run_step STEP_29 "Cargar perfil AppArmor (modo complain)"     step_29_apparmor
run_step STEP_30 "daemon-reload + enable + start"             step_30_enable_start
run_step STEP_31 "Verificar servicio arrancado"               step_31_verify_service

# =============================================================================
# SECCIÓN 8 — Fase G: Pairing Telegram (pasos 37-39)
# =============================================================================

step_32_pairing() {
  manual_box
  cat <<EOF
${BOLD}Emparejar Telegram (pairing)${RESET}

El servicio ya está corriendo y el bot espera el pairing. Pasos:

1. Envía cualquier mensaje al bot de Telegram → recibirás un código de 6 caracteres.

2. Para hacer el pairing se necesita una sesión interactiva con el plugin activo.
   Detén el servicio, lanza una sesión manual, empareja y reinicia. En OTRA terminal
   del host Proxmox:

   ${GREEN}pct exec ${VMID} -- systemctl stop claude-telegram.service${RESET}

   ${GREEN}pct exec ${VMID} -- su -s /bin/bash ${AGENT_NAME} -c '\\
     export HOME=/home/${AGENT_NAME}/claude; \\
     export PATH=/home/${AGENT_NAME}/apps/bin:/home/${AGENT_NAME}/claude/.local/bin:/usr/local/bin:/usr/bin:/bin; \\
     claude --channels plugin:telegram@claude-plugins-official'${RESET}

   En la sesión interactiva que se abre, ejecuta:
       ${GREEN}/telegram:access pair <código>${RESET}
   Sal con Ctrl+C. Luego reinicia el servicio:

   ${GREEN}pct exec ${VMID} -- systemctl start claude-telegram.service${RESET}

3. Envía "hola" desde Telegram — el agente debe responder.

Nota: el pairing es exclusivo del setup humano. El agente en runtime nunca debe
invocar /telegram:access pair (el hook lo prohíbe).
EOF
  echo ""
  read -rp "Pulsa ENTER cuando hayas completado el pairing y el agente responda..."
  lxc_exec "systemctl is-active claude-telegram.service" | grep -q '^active' || {
    log_warn "El servicio no está active tras el pairing. Arráncalo: pct exec ${VMID} -- systemctl start claude-telegram.service"
  }
  return 0
}

run_step STEP_32 "Emparejar Telegram (manual)"                step_32_pairing

# =============================================================================
# SECCIÓN 9 — Verificación final (pasos 42-48)
# =============================================================================

step_33_final_checks() {
  echo ""
  log_info "Verificaciones automáticas:"

  echo ""
  log_info "[pytest] Suite de tests del harness:"
  lxc_exec "su -s /bin/bash ${AGENT_NAME} -c 'python3 -m pytest /home/${AGENT_NAME}/workspace/tests/ -q'" \
    || log_warn "pytest reportó fallos o el harness aún no está completo (§4 F0-F5). Revisar."

  echo ""
  log_info "[postgres] Conteo de agent_memory (criterio duro — debe responder sin error):"
  if lxc_exec "su -s /bin/bash ${AGENT_NAME} -c 'set -a; . /etc/${AGENT_NAME}/secrets.env; set +a; psql \"\$POSTGRES_CONNECTION_STRING\" -tAc \"SELECT COUNT(*) FROM agent_memory WHERE agent_id='\''${AGENT_NAME}'\'';\"'"; then
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

run_step STEP_33 "Verificación final"                         step_33_final_checks

# =============================================================================
# SECCIÓN 10 — Mensaje final
# =============================================================================

echo ""
log_info "============================================================"
log_ok   "Instalación de '${AGENT_NAME}' completada (pasos automatizados)."
log_info "============================================================"
cat <<EOF

  ${BOLD}Resumen${RESET}
  - Agente   : ${AGENT_NAME}  (LXC vmid ${VMID}, hostname ${LXC_HOSTNAME})
  - IP       : ${IP_ADDRESS}
  - Servicio : claude-telegram.service
  - Checkpoint: ${CHECKPOINT_FILE}

  ${BOLD}Acciones manuales pendientes / a confirmar${RESET}
  1. AppArmor: si está en modo COMPLAIN, tras ejercitar el agente sin denegaciones:
       pct exec ${VMID} -- aa-enforce /etc/apparmor.d/home.${AGENT_NAME}.claude
  2. Verificaciones desde Telegram: /context, /skills, /reset (ver arriba).
  3. Onboarding: en el primer mensaje, el agente preguntará los datos del propietario
     y reescribirá su CLAUDE.md (sección "Primer arranque").
  4. Filtro anti-injection (§1.8 paso 22b): el userprompt-hook arranca en fail-open
     hasta que exista /home/${AGENT_NAME}/apps/bin/clean. Implementar según §1.8 paso 22b.
  5. Crons (Fase H) NO instalados — diferidos a §8 (proactividad por core_task).
  6. Secretos opcionales (UPTIME_KUMA_PUSH_URL, ${AGENT_UPPER}_INBOX_TOKEN, MQTT_BROKER,
     PGBACKREST_*) no se han escrito: añádelos a /etc/${AGENT_NAME}/secrets.env cuando
     despliegues sus secciones (§8.3, §9.9, §11.2, §11.4).

  Para re-ejecutar este script: salta automáticamente los pasos ya completados.
  Para empezar de cero: borra ${CHECKPOINT_FILE}.

EOF
