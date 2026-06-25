#!/usr/bin/env python3
"""
ticker.py — animates "🔄 <Agent> trabajando.." in a Telegram message.

Launched by userprompt-hook.py as a detached background process.
Killed by stop-hook.py via SIGTERM when the turn ends.

Args: session_id tg_message_id [interval] [max_seconds]
TTL: exits automatically after MAX_SECONDS (watchdog alert shown first).
"""
import os
import sys
import time
import json
import signal
import subprocess
import urllib.request
import urllib.parse

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID', '')

DEFAULT_MAX_SECONDS    = 600
DEFAULT_INTERVAL       = 1.0
WATCHDOG_POLL_TIMEOUT  = 300
WATCHDOG_RESPONSE_FILE = '/tmp/<agent>_watchdog_response'

CALLBACK_RESTART = '<agent>_watchdog_restart'
CALLBACK_CANCEL  = '<agent>_watchdog_cancel'


def build_text(dot_count):
    return '🔄 <Agent> trabajando' + '.' * dot_count


def _api(method, params):
    try:
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(
            f'https://api.telegram.org/bot{BOT_TOKEN}/{method}', data=data
        )
        urllib.request.urlopen(req, timeout=8)
        return True
    except Exception:
        return False


def edit_message(msg_id, text, reply_markup=None):
    params = {'chat_id': CHAT_ID, 'message_id': str(msg_id), 'text': text}
    if reply_markup is not None:
        params['reply_markup'] = json.dumps(reply_markup)
    return _api('editMessageText', params)


def show_watchdog_alert(msg_id):
    keyboard = {'inline_keyboard': [[
        {'text': '✅ Sí, reiniciar', 'callback_data': CALLBACK_RESTART},
        {'text': '❌ No, cancelar',  'callback_data': CALLBACK_CANCEL},
    ]]}
    edit_message(
        msg_id,
        '⚠️ Llevo 10 minutos sin responderte. Parece que estoy colgado. ¿Quieres reiniciarme?',
        reply_markup=keyboard,
    )


def reset_subscription():
    try:
        params = {'timeout': 0, 'allowed_updates': json.dumps([])}
        url = (f'https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?'
               + urllib.parse.urlencode(params))
        urllib.request.urlopen(url, timeout=5)
    except Exception:
        pass


def poll_for_callback(deadline):
    try:
        os.remove(WATCHDOG_RESPONSE_FILE)
    except FileNotFoundError:
        pass
    while time.time() < deadline:
        try:
            with open(WATCHDOG_RESPONSE_FILE) as f:
                result = f.read().strip()
            os.remove(WATCHDOG_RESPONSE_FILE)
            if result in ('restart', 'cancel'):
                return result
        except (FileNotFoundError, OSError):
            pass
        time.sleep(1)
    return 'timeout'


def do_restart():
    subprocess.Popen(
        ['systemctl', 'restart', 'claude-telegram.service'],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def handle_sigterm(signum, frame):
    reset_subscription()
    sys.exit(0)


def run(tg_message_id, interval=DEFAULT_INTERVAL, max_seconds=DEFAULT_MAX_SECONDS):
    while True:
        dot_count = 1
        elapsed = 0.0
        while elapsed < max_seconds:
            time.sleep(interval)
            elapsed += interval
            dot_count = (dot_count % 5) + 1
            edit_message(tg_message_id, build_text(dot_count))

        show_watchdog_alert(tg_message_id)
        deadline = time.time() + WATCHDOG_POLL_TIMEOUT
        result = poll_for_callback(deadline)
        reset_subscription()

        if result == 'restart':
            edit_message(tg_message_id, '🔄 Reiniciando <Agent>...')
            do_restart()
            return
        elif result == 'cancel':
            continue
        else:
            edit_message(tg_message_id, '⚠️ Sin respuesta. <Agent> sigue colgado. Escribe /reset cuando puedas.')
            return


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, handle_sigterm)
    if len(sys.argv) < 3:
        sys.exit(1)
    try:
        tg_msg_id = int(sys.argv[2])
        interval  = float(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_INTERVAL
        max_secs  = float(sys.argv[4]) if len(sys.argv) > 4 else DEFAULT_MAX_SECONDS
    except (ValueError, IndexError):
        sys.exit(1)
    run(tg_msg_id, interval=interval, max_seconds=max_secs)
