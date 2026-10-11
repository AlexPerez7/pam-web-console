#!/bin/sh
set -e
echo "[entrypoint] alembic upgrade head"
alembic upgrade head
echo "[entrypoint] starting uvicorn"
# --proxy-headers + --forwarded-allow-ips: nginx (contenedor `web`) corre en
# otra IP de la red de compose, y uvicorn por defecto sólo confía en
# 127.0.0.1 -- sin esto, request.client.host era SIEMPRE la IP de nginx:
# el tope de login por IP quedaba compartido por todo el equipo y el
# `source_ip` del audit_log no servía. Se confía sólo en rangos privados
# (la red de compose); nginx pisa X-Forwarded-For con $remote_addr, así
# que un cliente no puede falsearlo. Ver docs/security.md.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 \
    --proxy-headers \
    --forwarded-allow-ips "${PAM_FORWARDED_ALLOW_IPS:-172.16.0.0/12,10.0.0.0/8,192.168.0.0/16}"
