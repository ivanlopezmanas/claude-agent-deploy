#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/lib/chronicler.py
"""chronicler.py — SessionEnd. Reemplaza a session-summarizer.py.

Al finalizar una sesión:
1. Distila el transcript con distill-transcript.py.
2. Lanza el agente the-chronicler con claude --print para extraer memorias.
3. Inserta las memorias en agent_memory vía psycopg2 (POSTGRES_CONNECTION_STRING).
4. Genera material para self-improve en /home/<agent>/workspace/docs/improvements/.
5. Notifica a Telegram el resumen de la sesión.

Guards: solo en contexto main; ignora subagentes (sdk-cli) y reentradas (<AGENT>_HOOK_RUNNING).
La columna de texto del hecho es 'content' (§1.2).
"""

import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime

LOG = '/home/<agent>/logs/<agent>-session-end.log'
DISTILL_SCRIPT = '/home/<agent>/workspace/scripts/lib/distill-transcript.py'
CLAUDE_BIN = '/home/<agent>/claude/.local/bin/claude'
SETTINGS_BG = '/home/<agent>/claude/.claude/settings-background.json'
MCP_PG = '/home/<agent>/workspace/scripts/hooks/<agent>-mcp-postgres-only.json'
IMPROVEMENTS_DIR = '/home/<agent>/workspace/docs/improvements'
DB_DSN = os.environ.get('POSTGRES_CONNECTION_STRING', '')
VALID_CATEGORIES = {'user', 'feedback', 'project', 'reference'}


def log(msg):
    try:
        with open(LOG, 'a') as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except Exception:
        pass


def tg_send(token, chat_id, text):
    try:
        data = urllib.parse.urlencode({'chat_id': chat_id, 'text': text}).encode()
        req = urllib.request.Request(
            f'https://api.telegram.org/bot{token}/sendMessage', data=data
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log(f"[WARN] tg_send: {e}")


def distill_transcript(path):
    result = subprocess.run(
        [sys.executable, DISTILL_SCRIPT, path],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        log(f"[ERROR] distill rc={result.returncode}: {result.stderr[:200]}")
        return None
    return result.stdout.strip()


def call_chronicler(transcript_text):
    prompt = (
        "Analiza el siguiente transcript de sesión de <Agent> y devuelve SOLO un array JSON "
        "con las memorias relevantes. Cada objeto: {category, content, keywords, importance}. "
        "La primera línea debe ser [ y la última ].\n\n"
        f"{transcript_text}"
    )
    env = os.environ.copy()
    env['<AGENT>_CONTEXT'] = 'background'
    env['<AGENT>_HOOK_RUNNING'] = '1'
    env['HOME'] = '/home/<agent>/claude'

    result = subprocess.run(
        [CLAUDE_BIN, '--print',
         '--agent', 'the-chronicler',
         '--strict-mcp-config',
         '--settings', SETTINGS_BG,
         '--mcp-config', MCP_PG,
         prompt],
        capture_output=True, text=True, timeout=120, env=env
    )
    if result.returncode != 0:
        log(f"[ERROR] claude --print rc={result.returncode}: {result.stderr[:200]}")
        return None
    return result.stdout.strip()


def parse_memories(raw):
    raw = raw.strip()
    start = raw.find('[')
    end = raw.rfind(']')
    if start == -1 or end == -1:
        log(f"[WARN] no JSON array found in output: {raw[:200]}")
        return []
    try:
        memories = json.loads(raw[start:end + 1])
        return memories if isinstance(memories, list) else []
    except json.JSONDecodeError as e:
        log(f"[WARN] JSON parse error: {e} — raw: {raw[start:start+200]}")
        return []


def insert_memories(session_id, memories):
    import psycopg2
    inserted = 0
    errors = 0
    if not DB_DSN:
        log("[ERROR] POSTGRES_CONNECTION_STRING no definida")
        return 0, 1
    try:
        conn = psycopg2.connect(DB_DSN)
        cur = conn.cursor()
        for m in memories:
            if not isinstance(m, dict):
                continue
            category = m.get('category', '')
            if category not in VALID_CATEGORIES:
                log(f"[WARN] categoría inválida: {category}")
                continue
            keywords = m.get('keywords', [])
            if not isinstance(keywords, list):
                keywords = []
            content = str(m.get('content') or m.get('text') or '').strip()
            if not content:
                continue
            importance = m.get('importance', 3)
            try:
                importance = int(importance)
            except (TypeError, ValueError):
                importance = 3
            try:
                cur.execute(
                    "INSERT INTO agent_memory "
                    "(session_id, category, keywords, content, importance) "
                    "VALUES (%s::uuid, %s, %s, %s, %s)",
                    (session_id, category, keywords, content, importance)
                )
                conn.commit()
                inserted += 1
            except Exception as e:
                conn.rollback()
                log(f"[ERROR] insert: {e}")
                errors += 1
        cur.close()
        conn.close()
    except Exception as e:
        log(f"[ERROR] DB connect: {e}")
        errors += 1
    return inserted, errors


def write_improvement_material(session_id, memories, inserted):
    """Genera material para el agente self-improve."""
    try:
        os.makedirs(IMPROVEMENTS_DIR, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        path = os.path.join(IMPROVEMENTS_DIR, f"session-{stamp}.json")
        with open(path, 'w') as f:
            json.dump({
                "session_id": session_id,
                "ts": datetime.now().isoformat(),
                "memories_inserted": inserted,
                "memories": memories,
            }, f, ensure_ascii=False, indent=2)
        log(f"[OK] material self-improve: {path}")
    except Exception as e:
        log(f"[WARN] write_improvement_material: {e}")


def build_tg_message(memories, inserted, errors):
    if inserted == 0 and errors == 0:
        return "Sesión cerrada. Sin novedades que recordar."
    lines = [f"Sesión cerrada. {inserted} memoria(s) guardada(s):"]
    for m in memories:
        if not isinstance(m, dict):
            continue
        cat = m.get('category', '?')
        kws = ', '.join(str(k) for k in m.get('keywords', [])[:3])
        lines.append(f"· [{cat}] {kws}")
    if errors:
        lines.append(f"⚠️ {errors} error(s) al insertar.")
    return '\n'.join(lines)


def main():
    # Guards
    if os.environ.get('<AGENT>_CONTEXT', 'main') != 'main':
        sys.exit(0)
    if os.environ.get('CLAUDE_CODE_ENTRYPOINT') == 'sdk-cli':
        sys.exit(0)
    if os.environ.get('<AGENT>_HOOK_RUNNING'):
        sys.exit(0)
    os.environ['<AGENT>_HOOK_RUNNING'] = '1'

    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')

    try:
        hook_input = json.loads(sys.stdin.read())
    except Exception as e:
        log(f"[ERROR] parse hook input: {e}")
        sys.exit(0)

    transcript_path = hook_input.get('transcript_path', '')
    session_id = hook_input.get('session_id', '')

    if not transcript_path or not os.path.isfile(transcript_path):
        sys.exit(0)
    if not session_id:
        log("[ERROR] sin session_id")
        sys.exit(0)

    try:
        line_count = sum(1 for _ in open(transcript_path))
    except Exception:
        sys.exit(0)
    if line_count < 10:
        sys.exit(0)

    log(f"[START] session={session_id} lines={line_count}")

    transcript_text = distill_transcript(transcript_path)
    if not transcript_text:
        sys.exit(0)

    raw_output = call_chronicler(transcript_text)
    if raw_output is None:
        log("[ERROR] call_chronicler falló")
        sys.exit(0)

    log(f"[DEBUG] chronicler output: {raw_output[:300]}")

    memories = parse_memories(raw_output)
    log(f"[INFO] {len(memories)} memorias extraídas")

    if not memories:
        if bot_token and chat_id:
            tg_send(bot_token, chat_id, "Sesión cerrada. Sin novedades que recordar.")
        sys.exit(0)

    inserted, errors = insert_memories(session_id, memories)
    log(f"[INFO] inserted={inserted} errors={errors}")

    write_improvement_material(session_id, memories, inserted)

    if bot_token and chat_id:
        tg_send(bot_token, chat_id, build_tg_message(memories, inserted, errors))

    log(f"[END] session={session_id} inserted={inserted}")


main()
