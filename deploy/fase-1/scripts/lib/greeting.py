#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/lib/greeting.py
"""greeting.py — mensaje de bienvenida generado por Haiku al primer turno de sesión.

Lanzado en background por sessionstart-hook.py. Corre aislado del canal
principal (§7.2 — ver call_isolated_agent en common.py): no tiene el plugin de
Telegram cargado, así que envía el mensaje con una llamada HTTP directa a la
Bot API en vez de la tool de reply. FAIL-OPEN: cualquier fallo termina en
silencio, nunca bloquea ni retrasa la sesión.
"""
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, "/home/<agent>/workspace/scripts/lib")
from common import call_isolated_agent

PROMPT = (
    "Genera un saludo breve (una sola frase) para arrancar una sesión de trabajo, "
    "en español, con humor sutil. Sin emojis de más, sin frases genéricas tipo "
    "'¡Hola!' o '¿en qué puedo ayudarte?'."
)


def tg_send(token, chat_id, text):
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage", data=data
            ),
            timeout=8,
        )
    except Exception:
        pass


def main():
    text = call_isolated_agent(PROMPT, model="haiku", timeout=30)
    if not text:
        return
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if token and chat_id:
        tg_send(token, chat_id, text)


if __name__ == "__main__":
    main()
