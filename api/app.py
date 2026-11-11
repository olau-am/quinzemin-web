"""
API de quinzemin. De momento solo /api/health, para comprobar el despliegue
GitHub → Cloud Build → Cloud Run. El resto de rutas llega en la fase 3 (ver HANDOFF_WEB.md).

Local:  .venv/Scripts/python -m uvicorn api.app:app --reload
"""
from fastapi import FastAPI

from quinzemin import __version__

app = FastAPI(title="quinzemin API", version=__version__)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}
