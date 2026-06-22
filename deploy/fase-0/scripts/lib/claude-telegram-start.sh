#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/lib/claude-telegram-start.sh
#
# Wrapper pty del servicio principal: systemd no tiene terminal y Claude Code
# requiere TTY. Abre un par maestro/esclavo pty, hace fork, conecta el esclavo
# como terminal del proceso hijo y ejecuta Claude con el plugin de Telegram.
# El proceso padre lee stdout del pty y lo escribe en el log.
#
# Diferencia respecto al wrapper antiguo: usa --settings con RUTA AL FICHERO
# (/home/<agent>/claude/.claude/settings.json), no JSON inline. El settings.json del
# nuevo diseño ya registra los hooks; no se manipula aquí.
import os
import pty
import select
import sys

LOG_PATH = '/home/<agent>/logs/claude-telegram.log'
SETTINGS_PATH = '/home/<agent>/claude/.claude/settings.json'
CLAUDE_BIN = '/home/<agent>/claude/.local/bin/claude'

master_fd, slave_fd = pty.openpty()

pid = os.fork()
if pid == 0:
    os.setsid()
    import fcntl
    import termios
    fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
    for fd in (0, 1, 2):
        if fd != slave_fd:
            os.dup2(slave_fd, fd)
    os.close(master_fd)
    if slave_fd > 2:
        os.close(slave_fd)
    env = dict(os.environ)
    env['HOME'] = '/home/<agent>/claude'
    env['PATH'] = ('/home/<agent>/apps/bin:/home/<agent>/claude/.local/bin:'
                   '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin')
    env['<AGENT>_CONTEXT'] = 'main'
    env['TERM'] = 'xterm-256color'
    env['COLUMNS'] = '220'
    env['LINES'] = '50'
    os.execvpe(CLAUDE_BIN,
               ['claude',
                '--channels', 'plugin:telegram@claude-plugins-official',
                '--settings', SETTINGS_PATH],
               env)
    sys.exit(1)
else:
    os.close(slave_fd)
    while True:
        try:
            r, _, _ = select.select([master_fd], [], [], 1.0)
            if r:
                try:
                    data = os.read(master_fd, 4096)
                    with open(LOG_PATH, 'ab') as f:
                        f.write(data)
                except OSError:
                    break
        except (OSError, select.error):
            break
    os.close(master_fd)
    _, status = os.waitpid(pid, 0)
    sys.exit(os.waitstatus_to_exitcode(status))
