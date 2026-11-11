# Imagen única de quinzemin: API (este CMD) y, más adelante, el Job de preparación de ciudades.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY quinzemin ./quinzemin
COPY api ./api
RUN pip install ".[api]"

# Usuario sin privilegios
RUN useradd --create-home --uid 10001 app
USER app

# Cloud Run indica el puerto en $PORT (8080 por defecto)
CMD ["sh", "-c", "exec uvicorn api.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
