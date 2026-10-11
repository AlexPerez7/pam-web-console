#!/usr/bin/env bash
# Actualiza PAM Web Console en este servidor: git pull + rebuild + verificación.
#
#   cd /opt/pam-web-console && ./infra/deploy.sh
#
# Se detiene ante el primer problema, sin dejar nada a medias:
#   1. PAM_HTTPS_BIND del .env: si es una IP que este host no tiene, el
#      contenedor "web" no podría levantar y la UI quedaría caída.
#   2. Cambios locales en archivos versionados: se detiene y los lista.
#   3. git pull (sólo fast-forward) + docker compose up -d --build.
#   4. Verifica que api, worker y web estén corriendo y que la API y la UI
#      respondan.

set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
BRANCH="main"

log() { echo "[deploy] $*"; }
die() {
  echo "[deploy] ERROR: $*" >&2
  exit 1
}

cd "${APP_DIR}"
[ -f .env ] || die "no hay .env en ${APP_DIR} (copie .env.example)"

# ---------------------------------------------------------------- 1. bind
log "1/4 verificando PAM_HTTPS_BIND"
BIND="$(grep -E '^PAM_HTTPS_BIND=' .env | tail -1 | cut -d= -f2- | tr -d '"' || true)"
BIND="${BIND:-127.0.0.1}"
case "${BIND}" in
  0.0.0.0 | 127.0.0.1) ;;
  *)
    ip -4 -o addr show | grep -qF " ${BIND}/" ||
      die "PAM_HTTPS_BIND=${BIND}, pero este host no tiene esa IP: corríjalo en .env"
    ;;
esac
log "PAM_HTTPS_BIND=${BIND}: ok"

# ------------------------------------------------------- 2. cambios locales
log "2/4 buscando cambios locales en archivos versionados"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  git status --short --untracked-files=no
  die "hay cambios locales en archivos versionados: revíselos antes de actualizar"
fi

# ------------------------------------------------------------ 3. deploy
log "3/4 actualizando el código y reconstruyendo"
git fetch -q origin "${BRANCH}"
git merge -q --ff-only "origin/${BRANCH}" || die "el pull no es fast-forward (¿commits locales?)"
log "código en $(git log --oneline -1 HEAD)"
docker compose up -d --build || die "docker compose up falló (vea el error de arriba)"

# ---------------------------------------------------------- 4. verificar
log "4/4 verificando que todo quede arriba"
CHECK_HOST="${BIND}"
[ "${CHECK_HOST}" = "0.0.0.0" ] && CHECK_HOST="127.0.0.1"
ok=0
for _ in $(seq 1 30); do
  running="$(docker compose ps --status running --services)"
  api="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health || true)"
  ui="$(curl -sk -o /dev/null -w '%{http_code}' "https://${CHECK_HOST}/" || true)"
  if grep -qx api <<<"${running}" && grep -qx worker <<<"${running}" &&
    grep -qx web <<<"${running}" && [ "${api}" = "200" ] && [ "${ui}" = "200" ]; then
    ok=1
    break
  fi
  sleep 2
done

docker compose ps
if [ "${ok}" -ne 1 ]; then
  echo "  api /health -> ${api:-sin respuesta}, UI https://${CHECK_HOST}/ -> ${ui:-sin respuesta}"
  die "el stack no quedó sano: revise 'docker compose logs --tail 50 web api'"
fi
log "OK: api, worker y web arriba; la API y la UI responden"
