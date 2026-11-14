"""
API de quinzemin (FastAPI, Cloud Run).

  GET  /api/health                         estado
  GET  /api/municipalities?q=              buscador de municipios (catálogo nacional)
  GET  /api/cities                         ciudades preparadas
  GET  /api/cities/{ine}                   metadatos de una ciudad (y preparación en curso, si la hay)
  GET  /api/cities/{ine}/areas.geojson     secciones censales (el navegador las descarga una vez)
  GET  /api/cities/{ine}/gtfs-feeds        feeds GTFS del catálogo que cubren el municipio
  POST /api/cities/{ine}/prepare           prepara un municipio (trabajo en segundo plano)
  POST /api/cities/{ine}/transit           añade/sustituye el transporte público de una ciudad preparada
  GET  /api/jobs/{id}                      estado y progreso de un trabajo
  POST /api/runs                           recálculo (o resultado de la caché)
  GET  /api/runs/{ine}/{hash}              resultado guardado (URL compartible)
  GET  /api/runs/{ine}/{hash}/config.yaml  YAML para reproducir el cálculo en local

Límites (ver HANDOFF_WEB.md §5): CORS solo desde la web; por IP 20 recálculos/hora y 2 preparaciones/
día; en total 1 preparación a la vez y 5 ciudades nuevas al día; un cálculo a la vez por instancia
(429 + Retry-After); parámetros acotados con pydantic.

Local:  QM_STORAGE=local QM_DATA_DIR=data .venv/Scripts/python -m uvicorn api.app:app --reload
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import unicodedata
from collections import OrderedDict, defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Literal

import geopandas as gpd
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field, field_validator

from quinzemin import __version__, engine, gtfs_feeds, jobs, prepare, results
from quinzemin import bundle as qb
from quinzemin import storage as qs


log = logging.getLogger("quinzemin.api")


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


ALLOWED_ORIGINS = [o.strip() for o in os.environ.get(
    "QM_ALLOWED_ORIGINS", "https://olau-am.github.io,http://localhost:8000,http://127.0.0.1:8000").split(",")
    if o.strip()]
RUNS_PER_HOUR = _env_int("QM_RUNS_PER_HOUR", 20)
PREPARES_PER_DAY_IP = _env_int("QM_PREPARES_PER_DAY_IP", 2)
NEW_CITIES_PER_DAY = _env_int("QM_NEW_CITIES_PER_DAY", 5)
MAX_ACTIVE_PREPARES = _env_int("QM_MAX_ACTIVE_PREPARES", 1)
BUNDLE_CACHE_SIZE = _env_int("QM_BUNDLE_CACHE", 3)

app = FastAPI(title="quinzemin API", version=__version__,
              description="Accesibilidad a servicios básicos por sección censal (ciudad de los 15 minutos)")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["GET", "POST"],
                   allow_headers=["Content-Type"], expose_headers=["Retry-After", "X-Cache"], max_age=3600)
app.add_middleware(GZipMiddleware, minimum_size=2000)

_store: qs.Storage | None = None


def store() -> qs.Storage:
    global _store
    if _store is None:
        _store = qs.from_env()
    return _store


# ----------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------

def client_id(request: Request) -> str:
    """IP del cliente (Cloud Run la pasa en X-Forwarded-For), guardada solo como hash."""
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or \
        (request.client.host if request.client else "?")
    return hashlib.sha256(f"quinzemin:{ip}".encode()).hexdigest()[:16]


def too_many(msg: str, retry_after: int) -> HTTPException:
    return HTTPException(429, detail=msg, headers={"Retry-After": str(retry_after)})


class _RateLimiter:
    """Ventana deslizante en memoria (por instancia; con máx. 2 instancias es una aproximación)."""

    def __init__(self, limit: int, window_s: int):
        self.limit, self.window = limit, window_s
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> int:
        """0 si se permite (y lo cuenta); si no, segundos hasta que se libere un hueco."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return int(self.window - (now - q[0])) + 1
            q.append(now)
            return 0


_runs_limiter = _RateLimiter(RUNS_PER_HOUR, 3600)
_compute = threading.Semaphore(1)          # un cálculo a la vez por instancia
_bundles: OrderedDict[str, tuple[str, engine.Bundle]] = OrderedDict()
_bundles_lock = threading.Lock()


def city_meta(ine: str) -> dict:
    try:
        return store().read_json(f"cities/{ine}/meta.json")
    except FileNotFoundError:
        raise HTTPException(404, detail=f"El municipio {ine} no está preparado") from None


def get_bundle(ine: str, meta: dict | None = None) -> engine.Bundle:
    """Bundle en memoria (LRU), recargado si la ciudad se ha vuelto a preparar."""
    meta = meta or city_meta(ine)
    version = meta["prepared_at"] + str(len(meta.get("gtfs_sources") or []))
    with _bundles_lock:
        hit = _bundles.get(ine)
        if hit and hit[0] == version:
            _bundles.move_to_end(ine)
            return hit[1]
    b = qb.load(store(), ine)
    with _bundles_lock:
        _bundles[ine] = (version, b)
        _bundles.move_to_end(ine)
        while len(_bundles) > BUNDLE_CACHE_SIZE:
            _bundles.popitem(last=False)
    return b


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


_municipalities: list[dict] | None = None


def municipalities() -> list[dict]:
    global _municipalities
    if _municipalities is None:
        try:
            _municipalities = store().read_json("spain/municipios.json")
        except FileNotFoundError:
            raise HTTPException(503, detail="Falta el catálogo nacional (python -m quinzemin.catalog)") from None
    return _municipalities


def municipality(ine: str) -> dict:
    for m in municipalities():
        if m["ine"] == ine:
            return m
    raise HTTPException(404, detail=f"No existe el municipio {ine}")


def within_limits(m: dict) -> bool:
    return (m.get("poblacion") or 0) <= prepare.MAX_POPULATION and (m.get("km2") or 0) <= prepare.MAX_KM2


def _prepared_set() -> set[str]:
    return {k.split("/")[1] for k in store().list("cities/") if k.endswith("/meta.json")}


# ----------------------------------------------------------------------
# Rutas: estado y catálogo
# ----------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": __version__, "storage": os.environ.get("QM_STORAGE", "local")}


@app.get("/api/municipalities")
def search_municipalities(q: str = "", limit: int = 20) -> list[dict]:
    q = _fold(q.strip())
    limit = max(1, min(limit, 50))
    prepared = _prepared_set()
    if q.isdigit():
        found = [m for m in municipalities() if m["ine"].startswith(q)]
    elif len(q) >= 2:
        starts = [m for m in municipalities() if _fold(m["nombre"]).startswith(q)]
        contains = [m for m in municipalities() if q in _fold(m["nombre"]) and m not in starts]
        found = starts + contains
    else:
        found = [m for m in municipalities() if m["ine"] in prepared]
    return [{**m, "prepared": m["ine"] in prepared, "allowed": within_limits(m)} for m in found[:limit]]


@app.get("/api/cities")
def list_cities() -> list[dict]:
    out = []
    for ine in sorted(_prepared_set()):
        m = store().read_json(f"cities/{ine}/meta.json")
        out.append({k: m.get(k) for k in ("ine", "name", "province", "population", "has_network",
                                          "has_transit", "prepared_at")})
    return out


@app.get("/api/cities/{ine}")
def get_city(ine: str) -> dict:
    _check_ine(ine)
    active = [lk for lk in jobs.active(store()) if lk["job"].startswith(ine + "-")]
    try:
        meta = store().read_json(f"cities/{ine}/meta.json")
    except FileNotFoundError:
        m = municipality(ine)
        return {"ine": ine, "name": m["nombre"], "prepared": False, "allowed": within_limits(m),
                "job": active[0]["job"] if active else None}
    keep = ("ine", "name", "province", "center", "bbox", "population", "population_year", "km2", "services",
            "has_network", "has_transit", "gtfs_sources", "attributions", "prepared_at", "config")
    return {**{k: meta.get(k) for k in keep}, "prepared": True,
            "attribution_html": results.attribution_html(meta),
            "job": active[0]["job"] if active else None}


@app.get("/api/cities/{ine}/areas.geojson")
def city_areas(ine: str) -> Response:
    _check_ine(ine)
    try:
        data = store().read_bytes(f"cities/{ine}/areas.geojson")
    except FileNotFoundError:
        raise HTTPException(404, detail=f"El municipio {ine} no está preparado") from None
    return Response(data, media_type="application/geo+json",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/cities/{ine}/gtfs-feeds")
def city_gtfs_feeds(ine: str) -> list[dict]:
    _check_ine(ine)
    try:
        bbox = store().read_json(f"cities/{ine}/meta.json")["bbox"]
    except FileNotFoundError:
        m = municipality(ine)
        sec = gpd.read_parquet(store().local_path(f"spain/secciones/{m['cpro']}.parquet"))
        bbox = sec[sec["CUMUN"] == ine].to_crs(4326).total_bounds.tolist()
    feeds = gtfs_feeds.feeds_for(store(), bbox, include_inactive=True)
    return [{k: f.get(k) for k in ("id", "provider", "name", "status", "scope", "license_url", "official")}
            | {"nap": gtfs_feeds.is_nap(f), "auto": f["scope"] != "nacional" and f["status"] == "active"}
            for f in feeds]


def _check_ine(ine: str) -> None:
    if not (len(ine) == 5 and ine.isdigit()):
        raise HTTPException(422, detail="El código INE del municipio tiene 5 dígitos")


# ----------------------------------------------------------------------
# Preparación de municipios
# ----------------------------------------------------------------------

class PrepareIn(BaseModel):
    # Ejemplo válido para la página /docs (por defecto mostraría ["string"], que se rechaza)
    model_config = {"json_schema_extra": {"examples": [{"gtfs_feeds": [], "gtfs_auto": False},
                                                       {"gtfs_feeds": ["mdb-795"], "gtfs_auto": True}]}}
    gtfs_feeds: list[str] = Field(default_factory=list, max_length=5,
                                  description="feeds del catálogo (mdb-795) o URLs https")
    gtfs_auto: bool = Field(False, description="añadir los feeds urbanos y regionales que cubren el municipio")

    @field_validator("gtfs_feeds")
    @classmethod
    def _feeds(cls, v):
        for ref in v:
            if not (ref.startswith("mdb-") or ref.startswith("https://")) or len(ref) > 500:
                raise ValueError("Cada feed debe ser un id del catálogo (mdb-…) o una URL https://")
        return v


def _check_feeds(refs: list[str]) -> None:
    for ref in refs:
        try:
            gtfs_feeds.resolve(store(), ref)
        except gtfs_feeds.FeedError as e:
            raise HTTPException(422, detail=str(e)) from None


def _start_job(request: Request, ine: str, body: PrepareIn, kind: str) -> JSONResponse:
    st = store()
    client = client_id(request)
    if len(jobs.active(st)) >= MAX_ACTIVE_PREPARES:
        raise too_many("Ya hay una preparación en curso; inténtalo en unos minutos", 300)
    since = datetime.now(UTC) - timedelta(days=1)
    # Solo cuentan los trabajos que llegaron a lanzarse (un fallo al lanzar no gasta el cupo)
    recent = [j for j in jobs.created_since(st, since) if j.get("launched")]
    if sum(1 for j in recent if j.get("client") == client) >= PREPARES_PER_DAY_IP:
        raise too_many(f"Máximo {PREPARES_PER_DAY_IP} preparaciones al día por persona", 3600)
    if kind == "prepare" and sum(1 for j in recent if j.get("kind", "prepare") == "prepare") >= NEW_CITIES_PER_DAY:
        raise too_many(f"Se ha alcanzado el máximo de {NEW_CITIES_PER_DAY} ciudades nuevas por día", 3600)
    _check_feeds(body.gtfs_feeds)
    try:
        job = jobs.create(st, ine, gtfs_feeds=body.gtfs_feeds, gtfs_auto=body.gtfs_auto, client=client, kind=kind)
    except jobs.Busy as e:
        return JSONResponse({"job": str(e), "status": "running", "detail": "Ya se está preparando"}, 409)
    try:
        jobs.launch(job["id"], st)
        jobs.update(st, job, launched=True)
    except Exception as e:  # noqa: BLE001
        reason = f"{type(e).__name__}: {e}"[:500]
        log.exception("No se pudo lanzar el trabajo %s", job["id"])
        jobs.update(st, job, status="error", stage="error", msg=f"No se pudo lanzar: {reason}")
        jobs.release(st, ine, job["id"])
        raise HTTPException(503, detail=f"No se pudo lanzar la preparación ({job['id']}): {reason}") from None
    return JSONResponse(_public_job(job), 202)


@app.post("/api/cities/{ine}/prepare", status_code=202)
def prepare_city(ine: str, request: Request, body: PrepareIn | None = None) -> JSONResponse:
    _check_ine(ine)
    m = municipality(ine)
    if not within_limits(m):
        raise HTTPException(422, detail=f"{m['nombre']} supera el tamaño máximo "
                                        f"({prepare.MAX_POPULATION:,} hab. y {prepare.MAX_KM2} km²)".replace(",", "."))
    if store().exists(f"cities/{ine}/meta.json"):
        raise HTTPException(409, detail=f"{m['nombre']} ya está preparado")
    return _start_job(request, ine, body or PrepareIn(), "prepare")


@app.post("/api/cities/{ine}/transit", status_code=202)
def city_transit(ine: str, request: Request, body: PrepareIn) -> JSONResponse:
    _check_ine(ine)
    city_meta(ine)
    if not body.gtfs_feeds and not body.gtfs_auto:
        raise HTTPException(422, detail="Indica algún feed GTFS o gtfs_auto")
    return _start_job(request, ine, body, "transit")


def _public_job(job: dict) -> dict:
    return {k: v for k, v in job.items() if k != "client"}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    if not job_id.replace("-", "").isalnum() or len(job_id) > 40:
        raise HTTPException(422, detail="Identificador no válido")
    try:
        return _public_job(jobs.get(store(), job_id))
    except FileNotFoundError:
        raise HTTPException(404, detail="No existe ese trabajo") from None


# ----------------------------------------------------------------------
# Cálculos
# ----------------------------------------------------------------------

class ServiceIn(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    weight: float = Field(1.0, ge=0, le=5)
    distance_m: int | None = Field(None, ge=100, le=3000)


class RunIn(BaseModel):
    ine: str = Field(pattern=r"^\d{5}$")
    mode: Literal["distance", "network", "transit"] = "network"
    view: Literal["count", "score"] = "score"
    distance_m: int = Field(800, ge=100, le=3000)
    time_minutes: int = Field(15, ge=5, le=60)
    decay: float = Field(2.0, ge=1, le=3)
    services: list[ServiceIn] | None = Field(None, max_length=20)


def _params(body: RunIn, meta: dict) -> engine.Params:
    available = {s["id"] for s in meta["services"]}
    if body.mode == "network" and not meta.get("has_network"):
        raise HTTPException(422, detail="Esta ciudad no tiene red peatonal preparada")
    if body.mode == "transit" and not meta.get("has_transit"):
        raise HTTPException(422, detail="Esta ciudad no tiene transporte público (añade un feed GTFS)")
    services = body.services or [ServiceIn(id=s) for s in (x["id"] for x in meta["services"])]
    unknown = [s.id for s in services if s.id not in available]
    if unknown:
        raise HTTPException(422, detail=f"Servicios desconocidos: {', '.join(unknown)}")
    if len({s.id for s in services}) != len(services):
        raise HTTPException(422, detail="Hay servicios repetidos")
    if sum(s.weight for s in services) <= 0:
        raise HTTPException(422, detail="Algún servicio debe tener peso mayor que 0")
    return engine.Params(mode=body.mode, view=body.view, distance_m=body.distance_m,
                         time_minutes=body.time_minutes, decay=body.decay,
                         services=[engine.ServiceParams(s.id, s.weight, s.distance_m) for s in services])


def _run_key(ine: str, h: str) -> str:
    return f"runs/{ine}/{h}.json"


@app.post("/api/runs")
def create_run(body: RunIn, request: Request) -> Response:
    meta = city_meta(body.ine)
    params = _params(body, meta)
    h = results.params_hash(body.ine, params, meta["prepared_at"] + str(len(meta.get("gtfs_sources") or [])))
    key = _run_key(body.ine, h)
    st = store()
    if st.exists(key):
        return Response(st.read_bytes(key), media_type="application/json", headers={"X-Cache": "hit"})
    wait = _runs_limiter.check(client_id(request))
    if wait:
        raise too_many(f"Máximo {RUNS_PER_HOUR} recálculos por hora", wait)
    if not _compute.acquire(blocking=False):
        raise too_many("El servidor está calculando otra petición", 10)
    try:
        t0 = time.perf_counter()
        b = get_bundle(body.ine, meta)
        res = engine.compute(b, params)
        payload = results.to_payload(b, res)
        payload["run"] = {"hash": h, "seconds": round(time.perf_counter() - t0, 2),
                          "created": datetime.now(UTC).isoformat(timespec="seconds")}
    finally:
        _compute.release()
    st.write_json(key, payload, separators=(",", ":"))
    return JSONResponse(payload, headers={"X-Cache": "miss"})


def _stored_run(ine: str, h: str) -> dict:
    _check_ine(ine)
    if not h.isalnum() or len(h) > 64:
        raise HTTPException(422, detail="Identificador no válido")
    try:
        return store().read_json(_run_key(ine, h))
    except FileNotFoundError:
        raise HTTPException(404, detail="No existe ese cálculo (los resultados caducan a los 30 días)") from None


@app.get("/api/runs/{ine}/{h}")
def get_run(ine: str, h: str) -> dict:
    return _stored_run(ine, h)


@app.get("/api/runs/{ine}/{h}/config.yaml", response_class=PlainTextResponse)
def run_config(ine: str, h: str) -> PlainTextResponse:
    payload = _stored_run(ine, h)
    b = get_bundle(ine)
    text = results.to_yaml(b, results.params_from_dict(payload["params"]))
    return PlainTextResponse(text, media_type="application/x-yaml",
                             headers={"Content-Disposition": f'attachment; filename="quinzemin_{ine}_{h[:8]}.yaml"'})
