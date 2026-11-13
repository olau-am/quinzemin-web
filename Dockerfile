# Imagen única de quinzemin, con dos entradas:
#   API (Cloud Run service): el CMD de abajo (uvicorn)
#   Preparación (Cloud Run Job "quinzemin-prepare"): comando  python -m quinzemin.jobs  + argumento <job_id>
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Sin caché de osmnx en disco (el contenedor es efímero)
    QM_OSM_CACHE=0

WORKDIR /app

# Dependencias primero (se reaprovecha la capa si solo cambia el código)
COPY pyproject.toml README.md ./
RUN mkdir -p quinzemin api && touch quinzemin/__init__.py api/__init__.py \
    && pip install ".[api,prepare]" && pip uninstall -y quinzemin

COPY quinzemin ./quinzemin
COPY api ./api
COPY config ./config
RUN pip install --no-deps .

# Usuario sin privilegios
RUN useradd --create-home --uid 10001 app
USER app

# Cloud Run indica el puerto en $PORT (8080 por defecto)
CMD ["sh", "-c", "exec uvicorn api.app:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
