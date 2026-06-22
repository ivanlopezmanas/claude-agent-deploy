# Post-Install Checklist

Verificaciones a hacer **tras ejecutar `install-agent.sh`** antes de dar el agente por operativo.

---

## 1. Verificar nombres de tools MCP

`fase-1/settings.json` declara `mcp__postgres__query_data`, `count_rows`, `describe_table`, etc. como herramientas autorizadas.  
El paquete `@modelcontextprotocol/server-postgres` puede exponer nombres distintos (históricamente solo `query`).

**Comprobación:**

```bash
# En el LXC, con el agente arrancado
pct exec <VMID> -- su - <agent> -c "
  cd /home/<agent>/claude &&
  claude --print 'lista las tools mcp disponibles'
"
```

Si los nombres no coinciden con los del allow-list de `settings.json`, actualizar la sección `permissions.allow` en consecuencia.

---

## 2. AppArmor — pasar de complain a enforce

El script instala el perfil en modo **complain** (paso 31). Para activar enforcement real:

```bash
pct exec <VMID> -- aa-enforce /etc/apparmor.d/home.<agent>.claude
```

Verificar primero en `/var/log/syslog` que no haya denegaciones legítimas en modo complain antes de hacer el cambio.

---

## 3. Verificar acceso SSH

El script siempre habilita `PasswordAuthentication yes` y `PermitRootLogin yes`, así que el acceso por contraseña está garantizado.

```bash
ssh root@<IP_LXC>
# introduce la contraseña de root del LXC cuando se pida
```

Si durante la instalación también se añadió una clave pública, verificar que funciona:

```bash
ssh -i ~/.ssh/tu_clave root@<IP_LXC>
```

Si la clave no funciona, revisar:

```bash
pct exec <VMID> -- cat /root/.ssh/authorized_keys
pct exec <VMID> -- sshd -T | grep -E 'pubkeyauthentication|authorizedkeys'
```

---

## 4. Verificar servicio y Telegram

```bash
pct exec <VMID> -- systemctl status claude-telegram.service
pct exec <VMID> -- journalctl -u claude-telegram.service -n 50 --no-pager
```

El pairing de Telegram se hace en el paso 32 (interactivo). Si no se completó durante la instalación, ejecutar manualmente desde dentro del LXC:

```bash
pct exec <VMID> -- su - <agent> -c "
  cd /home/<agent>/claude &&
  ANTHROPIC_BASE_URL=http://localhost:... claude
"
```

---

## 5. Verificar timers heartbeat y midnight

```bash
pct exec <VMID> -- systemctl list-timers --no-pager | grep -E 'heartbeat|midnight'
```

Ambos deben aparecer con próxima ejecución programada.

---

## 6. Smoke test de memoria PostgreSQL

```bash
pct exec <VMID> -- psql -U <agent> -d agents -c "
  SELECT COUNT(*) FROM agent_memory WHERE agent_id='<agent>';
"
```

Debe devolver 0 (sin error), confirmando que la BD y el usuario están bien creados.
