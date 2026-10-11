# tls/

Cert TLS de nginx. **Solo en el server, no se commitea** (salvo este README
y `.gitkeep`).

| Archivo | Qué es |
|---|---|
| `fullchain.pem` | cert del server + cadena de la CA |
| `privkey.pem` | clave privada (chmod 600) |

Se montan en `/etc/nginx/tls` del contenedor `web`. **Si el dir está vacío,
el contenedor genera un self-signed al arrancar** (para no crash-loopear) —
sirve para probar, con warning del navegador.

## Cert real

Elegir según lo que tenga el MSP:

**A. CA interna (AD Certificate Services)** — lo mejor si existe: emitir un
cert para `pam.<dominio-interno>` desde esa CA. Las máquinas del dominio ya
confían en ella (GPO) → sin warning.

**B. Wildcard comercial** (`*.example.com`) + DNS split-horizon
(`pam.lab.local` → 192.168.10.10 internamente): usar ese cert, confiado
en todos lados.

**C. mkcert** — si no hay (A) ni (B): se genera una CA local y un cert, y se
instala la CA root en las máquinas de los operadores (una vez). Práctico
para un equipo chico sin AD/DNS interno:

```bash
mkcert -install
mkcert -cert-file fullchain.pem -key-file privkey.pem pam.lab.local 192.168.10.10
```

Nunca distribuir `rootCA-key.pem` (quien la tenga puede emitir certificados
que todo el equipo acepta); sólo el `rootCA.pem` público.

**D. self-signed** (el fallback automático): funciona, con warning.

Para cualquiera: dejar `fullchain.pem` + `privkey.pem` aquí y
`sudo systemctl restart pam-web-console`. Sin cambios de código.

## Exponer en la LAN

En `/opt/pam-web-console/.env`:
```
PAM_HTTPS_BIND=0.0.0.0        # o la IP LAN: 192.168.10.10
PAM_HOSTNAME=pam.lab.local
```
`sudo systemctl reload pam-web-console`. Firewall (defensa en profundidad; Docker igual
publica salteándose ufw):
```
sudo ufw allow from 192.168.10.0/24 to any port 443 proto tcp
```

Sin DNS interno, el hostname elegido (`pam.lab.local`) sólo resuelve si
cada máquina del equipo tiene una entrada en su archivo hosts apuntando a
`192.168.10.10` (en Windows: `C:\Windows\System32\drivers\etc\hosts`).
