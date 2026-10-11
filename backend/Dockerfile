# --- build stage: compila scpr_decode (decoder de grabaciones PSM/SCPR,
# ver backend/tools/scpr_decode/) --------------------------------------
FROM debian:bookworm-slim AS scpr_build
RUN apt-get update && apt-get install -y --no-install-recommends \
        g++ cmake make \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY tools/scpr_decode/ .
RUN cmake -B build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build -j"$(nproc)"

# --- imagen final -------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ffmpeg: recodifica a MP4 la salida de scpr_decode (docs/specs/
# recording-player.md) — paquete del sistema, no se compila.
RUN apt-get update && apt-get install -y --no-install-recommends curl age ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=scpr_build /build/build/scpr_decode /usr/local/bin/scpr_decode

COPY requirements.txt .
# pip mismo trae CVEs propios (manejo de índices/sdists en install) -- no
# corre en runtime, pero sí durante el build, así que se actualiza antes de
# instalar el resto (ver docs/security.md).
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .
# uid 1000 = mismo que el primer usuario del host (administrator), para poder
# leer el secret store montado read-only (secrets/age.key a 600).
RUN chmod +x entrypoint.sh \
    && useradd -m -u 1000 pam && chown -R pam:pam /app
USER pam

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=25s \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["./entrypoint.sh"]
