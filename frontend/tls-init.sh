#!/bin/sh
# Si no hay cert TLS montado, genera uno self-signed para que nginx arranque.
# Se reemplaza por un cert real dejando fullchain.pem + privkey.pem en el
# volumen ./tls del host y reiniciando (ver tls/README.md).
set -e
D=/etc/nginx/tls
if [ ! -s "$D/fullchain.pem" ] || [ ! -s "$D/privkey.pem" ]; then
  echo "[tls-init] sin cert -> generando self-signed (CN=${PAM_HOSTNAME:-pam.local})"
  mkdir -p "$D"
  openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
    -keyout "$D/privkey.pem" -out "$D/fullchain.pem" \
    -subj "/CN=${PAM_HOSTNAME:-pam.local}" \
    -addext "subjectAltName=DNS:${PAM_HOSTNAME:-pam.local},DNS:localhost,IP:127.0.0.1" \
    >/dev/null 2>&1
fi
