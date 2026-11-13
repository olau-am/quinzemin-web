"""
Formato de intercambio con el navegador y utilidades de parámetros.

  params_to_dict / params_from_dict   Params ↔ dict JSON
  params_hash(ine, params, version)   clave de caché de un cálculo (runs/{ine}/{hash}.json)
  to_payload(bundle, result)          JSON que pinta la web (sin geometría)
  to_yaml(bundle, params)             config.yaml para reproducir el cálculo en local

El payload lo usan igual la API (POST /api/runs) y el precálculo de València (scripts/precompute.py),
así que docs/app.js solo conoce un formato.
"""
from __future__ import annotations

import hashlib
import json
import math

import numpy as np
import yaml

from quinzemin import config
from quinzemin.engine import Bundle, Params, Result, ServiceParams

PAYLOAD_VERSION = 1


def params_to_dict(p: Params) -> dict:
    return {
        "mode": p.mode, "view": p.view, "distance_m": p.distance_m, "time_minutes": p.time_minutes,
        "decay": p.decay, "walking_speed_mpm": p.walking_speed_mpm,
        "max_walk_to_stop_m": p.max_walk_to_stop_m, "max_snap_m": p.max_snap_m,
        "park_access_m": p.park_access_m,
        "services": [{"id": s.id, "weight": s.weight, "distance_m": s.distance_m} for s in p.services or []],
    }


def params_from_dict(d: dict) -> Params:
    d = dict(d)
    services = [ServiceParams(s["id"], float(s.get("weight", 1)), s.get("distance_m"))
                for s in d.pop("services", None) or []]
    return Params(**d, services=services or None)


def normalize(p: Params) -> Params:
    """Misma clave de caché para parámetros equivalentes (p. ej. decay no cuenta en la vista count)."""
    d = params_to_dict(p)
    if p.view == "count":
        d["decay"] = 1.0
    if p.mode == "transit":
        d["distance_m"] = 0               # en transit no se usa
        for s in d["services"]:
            s["distance_m"] = None
    else:
        d["time_minutes"] = 0
    for k in ("distance_m", "time_minutes", "decay", "walking_speed_mpm", "max_walk_to_stop_m",
              "max_snap_m", "park_access_m"):
        d[k] = float(d[k])
    for s in d["services"]:
        s["weight"] = float(s["weight"])
    return params_from_dict(d)


def params_hash(ine: str, p: Params, bundle_version: str) -> str:
    """sha256 de los parámetros normalizados + municipio + versión del bundle (si se vuelve a preparar, cambia)."""
    key = {"ine": ine, "bundle": bundle_version, "payload": PAYLOAD_VERSION, "params": params_to_dict(normalize(p))}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:24]


def _clean(o):
    """Tipos numpy → JSON; NaN/inf → None."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if math.isnan(o) or math.isinf(o) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def attribution_html(meta: dict) -> str:
    html = config.build_attr({"city": {"attributions": meta.get("attributions") or []}})
    if meta.get("prepared_at"):
        y, m, d = meta["prepared_at"][:10].split("-")
        html += f" | Datos obtenidos el {d}/{m}/{y}"
    return html


def to_payload(bundle: Bundle, result: Result, emoji_min_zoom: int = 14) -> dict:
    t, p, meta = result.table, result.params, bundle.meta
    svc_ids = result.stats["services"]
    cent = bundle.areas.geometry.centroid.to_crs(epsg=4326)
    areas = {}
    for i, r in t.iterrows():
        areas[r["area_id"]] = {
            "cusec": r["cusec"],
            "c": [round(cent[i].y, 6), round(cent[i].x, 6)],     # centroide (lat, lon) para los emojis
            "puntuacion": r["puntuacion"],
            "score": r["score"],
            "poblacion": r.get("poblacion"),
            "prioridad": r.get("prioridad"),
            # por servicio: [dentro del umbral, distancia (m) o tiempo (min), nombre]
            "svc": {s: [bool(r[f"{s}_ok"]), r[f"dist_{s}_m"], r[f"nombre_{s}"]] for s in svc_ids},
        }
    services = [{"id": s, "label": bundle.services[s].label if s in bundle.services else s,
                 "emoji": bundle.services[s].emoji if s in bundle.services else ""} for s in svc_ids]
    return _clean({
        "version": PAYLOAD_VERSION,
        "city": {"ine": meta.get("ine"), "name": meta.get("name"), "center": meta.get("center"),
                 "zoom": meta.get("zoom", 13), "attribution": attribution_html(meta),
                 "emoji_min_zoom": emoji_min_zoom},
        "params": params_to_dict(p),
        "services": services,
        "stats": result.stats,
        "areas": areas,
    })


def to_yaml(bundle: Bundle, p: Params) -> str:
    """config.yaml para repetir el cálculo en local:
         python -m quinzemin.prepare {ine}
         python -m quinzemin.cli --ine {ine} --config este.yaml"""
    meta = bundle.meta
    cfg = {
        "city": {"ine": meta.get("ine"), "name": meta.get("name"), "center": meta.get("center"),
                 "zoom": meta.get("zoom", 13), "attributions": meta.get("attributions") or []},
        "analysis": {
            "mode": p.mode, "distance_m": p.distance_m, "time_minutes": p.time_minutes,
            "walking_speed_mpm": p.walking_speed_mpm, "max_walk_to_stop_m": p.max_walk_to_stop_m,
            "scoring": {"decay": p.decay},
            "network": {"max_snap_m": p.max_snap_m, "park_access_m": p.park_access_m},
        },
        "map": {"view": p.view},
        "services": [
            {"id": s.id, "label": bundle.services[s.id].label if s.id in bundle.services else s.id,
             "emoji": bundle.services[s.id].emoji if s.id in bundle.services else "", "weight": s.weight,
             **({"distance_m": s.distance_m} if s.distance_m is not None else {})}
            for s in p.services or []
        ],
    }
    head = (f"# quinzemin · {meta.get('name')} ({meta.get('ine')}) · datos preparados el "
            f"{(meta.get('prepared_at') or '')[:10]}\n"
            f"# Reproducir en local:\n"
            f"#   python -m quinzemin.prepare {meta.get('ine')}\n"
            f"#   python -m quinzemin.cli --ine {meta.get('ine')} --config config.yaml\n")
    return head + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
