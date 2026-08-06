#!/usr/bin/env python3
"""inbox_api.py — receptor HTTP para que integraciones externas (n8n, Home
Assistant, lo que sea) creen eventos en `agent_inbox` sin acceso directo a
Postgres.

Es SOLO comunicación, nunca manipulación: el único efecto posible de una
petición válida es una llamada a `inbox.create_external_event()`, que fuerza
`event_type` a alert/info, `agent='any'` y envuelve el mensaje con una
advertencia de "esto es dato, no instrucción". Ver `inbox.py` para el
razonamiento completo de cada restricción.

Pensado para arrancar por activación de socket de systemd (`inbox.socket`
+ `inbox.service`, ver workspace/scripts/systemd/): systemd mantiene el
socket abierto y solo lanza este proceso cuando llega una conexión. El
proceso se apaga solo tras IDLE_TIMEOUT_SECONDS sin peticiones -- no hay un
daemon permanente consumiendo recursos entre peticiones.

También puede arrancar en modo standalone (bind directo a un puerto) para
pruebas locales -- ver `main()`.

Script standalone con psycopg2, sin dependencias externas adicionales.
Connection string en POSTGRES_CONNECTION_STRING (inyectada por EnvironmentFile).
Secreto(s) en INBOX_SECRETS (JSON `{"label": "secreto"}`) o, si no está
definida, en INBOX_SECRET (secreto único, label fijo 'webhook').
"""
import hmac
import json
import os
import socket
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import psycopg2
import psycopg2.extras

import inbox

LOG = '/home/<agent>/logs/inbox_api.log'
DB_DSN = os.environ.get('POSTGRES_CONNECTION_STRING', '')

BIND_HOST = os.environ.get('INBOX_BIND_HOST', '0.0.0.0')
BIND_PORT = int(os.environ.get('INBOX_BIND_PORT', '8787'))
PATH = '/inbox'

MAX_BODY_BYTES = 16 * 1024
IDLE_TIMEOUT_SECONDS = 60      # sin peticiones -> el proceso se apaga solo;
                                # systemd lo relanza en la siguiente conexión.

SECRET_HEADER = 'X-Inbox-Secret'


def log(msg: str) -> None:
    try:
        with open(LOG, 'a') as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except Exception:
        pass


# --------------------------------------------------------------------------
# Secretos: un label por integrador. `source` en agent_inbox se deriva del
# label del secreto que autenticó la petición -- nunca lo elige el caller.
# --------------------------------------------------------------------------
def load_secrets() -> dict:
    raw = os.environ.get('INBOX_SECRETS', '')
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data:
                return {str(k): str(v) for k, v in data.items()}
        except json.JSONDecodeError:
            log("[ERROR] INBOX_SECRETS no es JSON válido, ignorada")
    single = os.environ.get('INBOX_SECRET', '')
    return {'webhook': single} if single else {}


def authenticate(secrets: dict, presented: str) -> str | None:
    """Compara `presented` contra cada secreto con tiempo constante
    (`hmac.compare_digest`) -- nunca `==`, que filtra tiempo de comparación
    y facilita adivinar el secreto carácter a carácter. Devuelve el label
    del integrador si hay match, o None."""
    if not presented:
        return None
    for label, secret in secrets.items():
        try:
            if secret and hmac.compare_digest(presented, secret):
                return label
        except TypeError:
            # compare_digest exige str ASCII-only; una cabecera con bytes
            # fuera de rango (decodificados en latin-1 por el parser HTTP)
            # la rompe -- se trata como intento fallido, no como 500.
            continue
    return None


# --------------------------------------------------------------------------
# Validación del payload entrante -- estricta, rechaza en vez de adivinar.
# --------------------------------------------------------------------------
class ValidationError(Exception):
    pass


def validate_request_body(data) -> dict:
    if not isinstance(data, dict):
        raise ValidationError("el cuerpo debe ser un objeto JSON")

    event_type = data.get('event_type')
    if event_type not in inbox.EXTERNAL_EVENT_TYPES:
        raise ValidationError(
            f"event_type debe ser uno de {inbox.EXTERNAL_EVENT_TYPES!r}"
        )

    message = data.get('message')
    if not isinstance(message, str) or not message.strip():
        raise ValidationError("message es obligatorio y debe ser texto no vacío")

    context = data.get('context', {})
    if context is not None and not isinstance(context, dict):
        raise ValidationError("context, si se pasa, debe ser un objeto")

    severity = data.get('severity', 'medium')
    if severity not in inbox.VALID_SEVERITIES:
        raise ValidationError(f"severity debe ser uno de {inbox.VALID_SEVERITIES!r}")

    # Campos que el contrato deja fuera a propósito (comunicación, no
    # manipulación) -- si llegan, se rechaza la petición entera en vez de
    # ignorarlos en silencio: un caller que los manda está asumiendo que
    # tienen efecto, y dejarlo pasar callado es peor que devolverle un 400.
    forbidden = {'source', 'agent', 'dedupe_key', 'process_after',
                 'scheduled_task_id', 'script_path'} & data.keys()
    if forbidden:
        raise ValidationError(f"campos no permitidos desde fuera: {sorted(forbidden)}")

    return {
        'event_type': event_type,
        'message': message,
        'context': context or {},
        'severity': severity,
    }


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------
class InboxHandler(BaseHTTPRequestHandler):
    server_version = "<Agent>InboxAPI/1.0"
    timeout = 10  # sin esto, un cliente que abre conexión y no manda cuerpo
                  # deja rfile.read() bloqueado para siempre y tumba el
                  # servidor entero (es mono-hilo). http.server ya captura
                  # socket.timeout en handle_one_request y cierra la conexión.

    def log_message(self, fmt, *args):
        log(f"[HTTP] {self.address_string()} {fmt % args}")

    def _reply(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        self.server.note_activity()

        if self.path != PATH:
            self._reply(404, {"error": "not found"})
            return

        secrets = self.server.secrets
        presented = self.headers.get(SECRET_HEADER, '')
        label = authenticate(secrets, presented)
        if label is None:
            log(f"[AUTH] intento fallido desde {self.address_string()}")
            self._reply(401, {"error": "unauthorized"})
            return

        try:
            length = int(self.headers.get('Content-Length', 0) or 0)
        except ValueError:
            self._reply(400, {"error": "content-length inválido"})
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._reply(400, {"error": "content-length inválido o excesivo"})
            return

        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._reply(400, {"error": "JSON inválido"})
            return

        try:
            clean = validate_request_body(data)
        except ValidationError as e:
            self._reply(400, {"error": str(e)})
            return

        try:
            conn = psycopg2.connect(DB_DSN)
            try:
                conn.autocommit = False
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    inbox.create_external_event(
                        cur,
                        source_label=label,
                        event_type=clean['event_type'],
                        message=clean['message'],
                        context=clean['context'],
                        severity=clean['severity'],
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            log(f"[ERROR] fallo insertando evento externo (label={label}): {e}")
            self._reply(502, {"error": "fallo interno, reintenta más tarde"})
            return

        log(f"[OK] evento externo aceptado label={label} event_type={clean['event_type']}")
        self._reply(201, {"status": "accepted"})

    def do_GET(self):
        # Sin lectura de datos: ni siquiera un healthcheck expone contenido.
        self._reply(404, {"error": "not found"})


# --------------------------------------------------------------------------
# Servidor con idle-shutdown: si no ve actividad en IDLE_TIMEOUT_SECONDS, se
# apaga solo. Con socket activation, systemd vuelve a lanzar el proceso en
# la siguiente conexión -- no hace falta que quede corriendo de fondo.
# --------------------------------------------------------------------------
class IdleShutdownServer(HTTPServer):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.secrets = load_secrets()
        self._last_activity = threading.Event()
        self.note_activity()
        self._watchdog = threading.Thread(target=self._watch_idle, daemon=True)
        self._watchdog.start()

    def note_activity(self):
        self._last_activity.set()

    def _watch_idle(self):
        while True:
            triggered = self._last_activity.wait(timeout=IDLE_TIMEOUT_SECONDS)
            if not triggered:
                log(f"[IDLE] sin peticiones en {IDLE_TIMEOUT_SECONDS}s, cerrando")
                threading.Thread(target=self.shutdown, daemon=True).start()
                return
            self._last_activity.clear()


def _socket_from_systemd():
    """Envuelve el socket ya escuchando que systemd pasa vía LISTEN_FDS
    (convención SD_LISTEN_FDS_START=3). None si no se arrancó por activación
    de socket (ej. ejecución manual para pruebas)."""
    pid = os.environ.get('LISTEN_PID')
    fds = os.environ.get('LISTEN_FDS')
    if not pid or not fds or int(pid) != os.getpid():
        return None
    if int(fds) < 1:
        return None
    return socket.fromfd(3, socket.AF_INET, socket.SOCK_STREAM)


def main() -> int:
    if not DB_DSN:
        log("[ERROR] POSTGRES_CONNECTION_STRING no definida")
        return 1
    if not load_secrets():
        log("[ERROR] ni INBOX_SECRETS ni INBOX_SECRET definidas -- nadie podría autenticarse")
        return 1

    inherited = _socket_from_systemd()
    if inherited is not None:
        server = IdleShutdownServer((BIND_HOST, BIND_PORT), InboxHandler, bind_and_activate=False)
        server.socket = inherited
        log("[START] activado por socket de systemd")
    else:
        server = IdleShutdownServer((BIND_HOST, BIND_PORT), InboxHandler)
        log(f"[START] modo standalone en {BIND_HOST}:{BIND_PORT} (sin socket activation)")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    log("[STOP] servidor apagado")
    return 0


if __name__ == '__main__':
    sys.exit(main())
