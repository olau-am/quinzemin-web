"""
Trabajos de preparación de municipios (sin base de datos: todo en el almacenamiento).

  jobs/{id}.json          estado: queued → running → done | error, con progreso (stage, pct, msg)
  locks/prepare-{ine}     lock (crear-si-no-existe) para no preparar dos veces la misma ciudad

  create(st, ine, ...)    crea el trabajo y el lock; error si ya se está preparando
  launch(job_id)          local: hilo en el mismo proceso · gcs: ejecución del Cloud Run Job
  run(job_id)             lo que hace el trabajo (entrada del Job: python -m quinzemin.jobs <id>)

Con facturación por petición, Cloud Run no da CPU después de responder: por eso la preparación
(8–20 min, casi todo esperando a Overpass) es un Cloud Run Job aparte y no un hilo de la API.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback
import uuid
from datetime import UTC, datetime, timedelta

from quinzemin import storage as qs

LOCK_TTL = timedelta(hours=2)        # un lock más antiguo se considera abandonado (Job caído)


class Busy(RuntimeError):
    """Ya hay una preparación en curso para ese municipio."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _key(job_id: str) -> str:
    return f"jobs/{job_id}.json"


def _lock_key(ine: str) -> str:
    return f"locks/prepare-{ine}.json"


def get(st: qs.Storage, job_id: str) -> dict:
    return st.read_json(_key(job_id))


def update(st: qs.Storage, job: dict, **kw) -> dict:
    job.update(kw, updated=_now())
    st.write_json(_key(job["id"]), job)
    return job


def create(st: qs.Storage, ine: str, gtfs_feeds: list[str] | None = None, gtfs_auto: bool = False,
           client: str | None = None, kind: str = "prepare") -> dict:
    """kind: "prepare" (municipio nuevo) o "transit" (solo el transporte público de uno ya preparado)."""
    job = {"id": f"{ine}-{uuid.uuid4().hex[:10]}", "ine": ine, "kind": kind, "status": "queued", "stage": "en cola",
           "pct": 0, "msg": "", "gtfs_feeds": gtfs_feeds or [], "gtfs_auto": gtfs_auto,
           "client": client, "created": _now(), "updated": _now()}
    lock = {"job": job["id"], "created": job["created"]}
    if not st.create_if_absent(_lock_key(ine), _json(lock)):
        current = st.read_json(_lock_key(ine))
        age = datetime.now(UTC) - datetime.fromisoformat(current["created"])
        if age < LOCK_TTL:
            raise Busy(current["job"])
        st.write_json(_lock_key(ine), lock)          # lock abandonado: se reemplaza
    st.write_json(_key(job["id"]), job)
    return job


def _json(o) -> bytes:
    import json
    return json.dumps(o).encode()


def release(st: qs.Storage, ine: str, job_id: str) -> None:
    try:
        if st.read_json(_lock_key(ine)).get("job") == job_id:
            st.delete(_lock_key(ine))
    except FileNotFoundError:
        pass
    except Exception:  # noqa: BLE001 — un lock que no se puede borrar caduca solo (LOCK_TTL)
        pass


def created_since(st: qs.Storage, since: datetime) -> list[dict]:
    """Trabajos creados desde una fecha (límites diarios globales y por cliente)."""
    out = []
    for key in st.list("jobs/"):
        try:
            j = st.read_json(key)
        except Exception:  # noqa: BLE001
            continue
        if datetime.fromisoformat(j["created"]) >= since:
            out.append(j)
    return out


def active(st: qs.Storage) -> list[dict]:
    """Preparaciones en curso (locks vigentes)."""
    out = []
    for key in st.list("locks/"):
        try:
            lock = st.read_json(key)
        except Exception:  # noqa: BLE001
            continue
        if datetime.now(UTC) - datetime.fromisoformat(lock["created"]) < LOCK_TTL:
            out.append(lock)
    return out


# ----------------------------------------------------------------------
# Ejecución
# ----------------------------------------------------------------------

def run(job_id: str, st: qs.Storage | None = None) -> dict:
    from quinzemin import config, prepare

    st = st or qs.from_env()
    job = get(st, job_id)
    update(st, job, status="running", stage="empezando", pct=1)
    last = [0.0]

    def progress(stage: str, pct: int, msg: str = "") -> None:
        print(f"[{pct:3d} %] {stage}{': ' + msg if msg else ''}", flush=True)
        # No escribir en el almacenamiento más de una vez cada 2 s
        if time.monotonic() - last[0] > 2 or pct >= 100:
            last[0] = time.monotonic()
            update(st, job, stage=stage, pct=pct, msg=msg)

    city_gtfs = config.city_config_path(job["ine"]).exists()
    try:
        if job.get("kind") == "transit":
            meta = prepare.update_transit(job["ine"], st, refs=job.get("gtfs_feeds") or None,
                                          auto=job.get("gtfs_auto", False), city_gtfs=city_gtfs,
                                          progress=progress)
            meta.setdefault("seconds", 0)
        else:
            meta = prepare.prepare(job["ine"], st, progress=progress, gtfs=city_gtfs,
                                   gtfs_feeds=job.get("gtfs_feeds") or None, gtfs_auto=job.get("gtfs_auto", False))
        update(st, job, status="done", stage="listo", pct=100, msg=f"{meta['seconds']} s",
               result={"name": meta["name"], "population": meta["population"],
                       "services": len(meta["services"]), "has_transit": meta["has_transit"]})
    except Exception as e:  # noqa: BLE001 — cualquier fallo queda registrado en el trabajo
        traceback.print_exc()
        update(st, job, status="error", stage="error", msg=f"{type(e).__name__}: {e}"[:500])
    finally:
        release(st, job["ine"], job_id)
    return job


def launch(job_id: str, st: qs.Storage | None = None) -> None:
    """Arranca el trabajo: en un hilo (almacenamiento local) o como ejecución del Cloud Run Job (gcs)."""
    if os.environ.get("QM_STORAGE", "local") != "gcs":
        threading.Thread(target=run, args=(job_id, st), daemon=True, name=f"prepare-{job_id}").start()
        return
    from google.cloud import run_v2

    project = os.environ.get("QM_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        import google.auth
        project = google.auth.default()[1]
    name = f"projects/{project}/locations/{os.environ['QM_REGION']}/jobs/{os.environ['QM_PREPARE_JOB']}"
    req = run_v2.RunJobRequest(name=name, overrides=run_v2.RunJobRequest.Overrides(
        container_overrides=[run_v2.RunJobRequest.Overrides.ContainerOverride(args=[job_id])]))
    run_v2.JobsClient().run_job(request=req)        # no se espera a que termine


def main(argv: list[str] | None = None) -> None:
    """
    Entrada del Cloud Run Job (comando: quinzemin-job), con un único argumento:
      <job_id>   ejecuta ese trabajo (lo lanza la API con la anulación de argumentos)
      catalog    genera el catálogo nacional spain/ en el almacenamiento (una vez, a mano)
    """
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        sys.exit("Uso: quinzemin-job <job_id> | catalog")
    if argv[0] == "catalog":
        from quinzemin import catalog, gtfs_feeds
        st = qs.from_env()
        catalog.build(st)
        gtfs_feeds.refresh_catalog(st)
        return
    sys.exit(0 if run(argv[0])["status"] == "done" else 1)


if __name__ == "__main__":
    main()
