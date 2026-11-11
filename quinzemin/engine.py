"""
Motor de accesibilidad: compute(bundle, params) → Result, sin nada de presentación.

Sale del bucle de quinzemin/src/main.py (copia en legacy/main.py) y reproduce sus columnas:

  dist_{id}_m  metros (distance/network) o minutos (transit) hasta el servicio más cercano
  nombre_{id}  su nombre ("—" si no hay)
  {id}_ok      dentro del umbral (distance_m o time_minutes)
  cred_{id}    crédito 0–1 para la puntuación (1 hasta el umbral, cae a 0 en umbral × decay)
  score        nº de servicios dentro del umbral
  puntuacion   100 · Σ peso·crédito / Σ peso
  poblacion, prioridad = poblacion · (100 − puntuacion) / 100
  snap_m, rodeo_{id}  (solo network)

Los indicadores agregados (cobertura de población, % por tramo, cortes de prioridad) se
calculan aquí, en `Result.stats`, para que el navegador no tenga que duplicar la lógica.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from quinzemin import population

MODES = ("distance", "network", "transit")
VIEWS = ("count", "score")

# Tramos de la vista "score": (límite inferior, etiqueta), de mejor a peor. No son equidistantes:
# en una ciudad compacta casi todas las áreas superan 80; interesa distinguir quién llega a todo
# (100) y cuánto le falta al resto. Colores (RdYlGn) en la capa de presentación.
SCORE_BANDS = [(100, "100 · todos"), (90, "90 – 100"), (75, "75 – 90"), (50, "50 – 75"), (0, "< 50")]
N_PRIO_CLASSES = 5
NO_NAME = "—"


# ----------------------------------------------------------------------
# Entradas y salida
# ----------------------------------------------------------------------

@dataclass
class Service:
    id: str
    label: str
    features: gpd.GeoDataFrame          # en el CRS UTM del bundle; columna "name"
    emoji: str = ""
    polygon_access: bool = False        # network: se llega a cualquier punto del polígono (parques)


@dataclass
class Bundle:
    """Datos ya preparados de una ciudad. Todo en el mismo CRS UTM (areas.crs)."""
    areas: gpd.GeoDataFrame             # columnas area_id, cusec y, si hay, poblacion (float)
    services: dict[str, Service]
    network: object | None = None       # quinzemin.network.WalkNetwork
    transit: object | None = None       # quinzemin.gtfs.TransitGraph
    meta: dict = field(default_factory=dict)


@dataclass
class ServiceParams:
    id: str
    weight: float = 1.0
    distance_m: float | None = None     # umbral propio (distance/network); None = el global


@dataclass
class Params:
    mode: str = "network"
    view: str = "score"
    distance_m: float = 800
    time_minutes: float = 15
    decay: float = 2.0                  # solo cuenta en la vista score
    walking_speed_mpm: float = 80
    max_walk_to_stop_m: float = 500
    max_snap_m: float = 500
    park_access_m: float = 20
    services: list[ServiceParams] | None = None   # None = todos los del bundle con peso 1

    @property
    def effective_decay(self) -> float:
        return float(self.decay) if self.view == "score" else 1.0


@dataclass
class Result:
    table: pd.DataFrame                 # una fila por área, en el orden de bundle.areas
    stats: dict
    params: Params


# ----------------------------------------------------------------------
# Piezas
# ----------------------------------------------------------------------

def credit(raw, threshold: float, decay: float) -> np.ndarray:
    """
    Crédito 0–1 de un servicio para cada área: 1 hasta el umbral y caída lineal
    hasta 0 en umbral × decay (decay ≤ 1 → criterio binario dentro/fuera).
    `raw` y `threshold` en las mismas unidades (metros o segundos).
    """
    raw = np.asarray(raw, dtype=float)
    if decay <= 1:
        return (raw <= threshold).astype(float)
    return np.clip((decay * threshold - raw) / ((decay - 1) * threshold), 0.0, 1.0)


def band_index(score) -> np.ndarray:
    """Puntuación 0–100 → índice en SCORE_BANDS (0 = mejor tramo)."""
    lows = np.array([lo for lo, _ in SCORE_BANDS], dtype=float)
    s = np.asarray(score, dtype=float)
    return np.argmax(s[..., None] >= lows, axis=-1)


def prio_breaks(values) -> np.ndarray:
    """Cortes por cuantiles (N_PRIO_CLASSES clases) de la prioridad > 0, sin repetir valores."""
    v = np.asarray(values, dtype=float)
    v = v[v > 0]
    if not len(v):
        return np.array([0.0])
    qs = np.quantile(v, np.linspace(0, 1, N_PRIO_CLASSES + 1)[1:-1])
    return np.unique(np.round(qs))


def _names(gdf) -> np.ndarray:
    if "name" not in gdf.columns:
        return np.full(len(gdf), NO_NAME, dtype=object)
    s = gdf["name"]
    ok = s.notna() & (s.astype(str).str.strip() != "")
    return np.where(ok, s.astype(str), NO_NAME).astype(object)


def _pick(names: np.ndarray, idx: np.ndarray) -> list[str]:
    return [names[i] if i >= 0 else NO_NAME for i in idx]


# ----------------------------------------------------------------------
# Cálculo
# ----------------------------------------------------------------------

def compute(bundle: Bundle, params: Params) -> Result:
    if params.mode not in MODES:
        raise ValueError(f"Modo no válido: {params.mode}")
    if params.view not in VIEWS:
        raise ValueError(f"Vista no válida: {params.view}")
    if params.mode == "network" and bundle.network is None:
        raise ValueError("Esta ciudad no tiene red peatonal preparada")
    if params.mode == "transit" and bundle.transit is None:
        raise ValueError("Esta ciudad no tiene datos GTFS de transporte público")

    areas     = bundle.areas
    n         = len(areas)
    centroids = areas.geometry.centroid
    cpts      = centroids.to_numpy()
    mode      = params.mode
    decay     = params.effective_decay
    budget_s  = params.time_minutes * 60
    reach_s   = budget_s * max(decay, 1.0)   # con decaimiento se explora hasta budget × decay
    walk_mps  = params.walking_speed_mpm / 60

    has_pop = "poblacion" in areas.columns and float(areas["poblacion"].sum()) > 0
    pop = areas["poblacion"].astype(float) if has_pop else None

    out = pd.DataFrame(index=areas.index)
    out["area_id"] = areas["area_id"].to_numpy()
    out["cusec"] = areas["cusec"].to_numpy() if "cusec" in areas.columns else None
    if has_pop:
        out["poblacion"] = pop.round().astype(int)

    diag: dict = {}
    net = bundle.network
    if mode == "network":
        orig_node, orig_snap = net.snap(cpts)
        out["snap_m"] = np.round(orig_snap, 1)
        diag["snap_m"] = {"origen": _pcts(orig_snap)}
    reach = None
    if mode == "transit":
        reach = bundle.transit.reachable_matrix(cpts, reach_s, walk_mps, params.max_walk_to_stop_m)

    svc_params = params.services or [ServiceParams(sid) for sid in bundle.services]
    score    = np.zeros(n, dtype=int)
    weighted = np.zeros(n)
    weights  = 0.0
    per_svc_areas = {}

    for sp in svc_params:
        sid  = sp.id
        svc  = bundle.services.get(sid)
        thr  = sp.distance_m if sp.distance_m is not None else params.distance_m
        gdf  = svc.features if svc is not None else None
        dist_col, nombre_col, ok_col = f"dist_{sid}_m", f"nombre_{sid}", f"{sid}_ok"
        raw = None

        if gdf is not None and len(gdf):
            names = _names(gdf)
            if mode == "network":
                feat, dnode, doff = net.destinations(
                    gdf, polygon_access=svc.polygon_access,
                    access_m=params.park_access_m, max_snap_m=params.max_snap_m,
                )
                diag.setdefault("snap_m", {})[sid] = _pcts(doff)
                diag.setdefault("dropped", {})[sid] = int(net.last_dropped)
                node_dist, node_feat = net.nearest_service(feat, dnode, doff)
                d    = node_dist[orig_node] + orig_snap
                fidx = node_feat[orig_node]
                out[dist_col]   = np.where(np.isfinite(d), np.round(d), 99999).astype(int)
                out[nombre_col] = _pick(names, fidx)
                out[ok_col]     = out[dist_col] <= thr
                raw = d
                # Índice de rodeo: distancia por la red / distancia en línea recta al mismo servicio
                euclid = np.full(n, np.nan)
                has = fidx >= 0
                euclid[has] = shapely.distance(gdf.geometry.to_numpy()[fidx[has]], cpts[has])
                with np.errstate(divide="ignore", invalid="ignore"):
                    out[f"rodeo_{sid}"] = np.round(np.where(euclid > 1, d / euclid, np.nan), 2)
            elif mode == "transit":
                secs, fidx = bundle.transit.service_min_times(
                    reach, gdf.geometry.to_numpy(), walk_mps, reach_s, params.max_walk_to_stop_m)
                out[dist_col]   = np.where(np.isfinite(secs), np.round(secs / 60), 9999).astype(int)
                out[nombre_col] = _pick(names, fidx)
                out[ok_col]     = secs <= budget_s
                raw, thr = secs, budget_s
            else:
                # Distancia euclídea: matriz áreas × elementos; en empate, el primer elemento
                dmat = shapely.distance(cpts[:, None], gdf.geometry.to_numpy()[None, :])
                dmat[np.isnan(dmat)] = np.inf          # elementos sin geometría
                fidx = np.argmin(dmat, axis=1)
                dist = np.round(dmat[np.arange(n), fidx]).astype(int)
                out[dist_col]   = dist
                out[nombre_col] = _pick(names, fidx)
                out[ok_col]     = dist <= thr
                raw = dist.astype(float)
        else:
            out[dist_col]   = None
            out[nombre_col] = NO_NAME
            out[ok_col]     = False

        score += out[ok_col].astype(int).to_numpy()
        per_svc_areas[sid] = int(out[ok_col].sum())
        # El servicio cuenta en el denominador aunque falten sus datos (crédito 0)
        c = credit(raw, thr, decay) if raw is not None else np.zeros(n)
        out[f"cred_{sid}"] = np.round(c, 3)
        weighted += sp.weight * c
        weights  += sp.weight

    out["score"] = score
    out["puntuacion"] = np.round(100 * weighted / weights, 1) if weights else 0.0
    if has_pop:
        out["prioridad"] = np.round(pop.to_numpy() * (100 - out["puntuacion"].to_numpy()) / 100).astype(int)

    stats = _stats(out, pop, params, [sp.id for sp in svc_params], bundle)
    stats["areas_ok"] = per_svc_areas
    stats["diagnostics"] = diag
    return Result(table=out, stats=stats, params=params)


def _pcts(arr) -> dict:
    arr = np.asarray(arr, dtype=float)
    if not len(arr):
        return {"n": 0}
    p50, p90, p99 = np.percentile(arr, [50, 90, 99])
    return {"n": len(arr), "p50": round(p50, 1), "p90": round(p90, 1), "p99": round(p99, 1),
            "max": round(float(arr.max()), 1)}


def _stats(out: pd.DataFrame, pop, params: Params, svc_ids: list[str], bundle: Bundle) -> dict:
    n_svc = len(svc_ids)
    stats: dict = {"areas": len(out), "services": svc_ids}
    if pop is None:
        stats["population"] = None
        return stats

    total = float(pop.sum())
    share = pop.to_numpy() / total * 100
    score_view = params.view == "score"
    ok = {sid: out[f"{sid}_ok"] for sid in svc_ids}
    cov = population.coverage(pop, ok, out["puntuacion"] if score_view else None)
    stats["population"] = {"total": total, "year": bundle.meta.get("population_year")}
    stats["coverage"] = cov

    if score_view:
        band = band_index(out["puntuacion"].to_numpy())
        stats["bands"] = [
            {"min": lo, "label": label, "pct": float(share[band == i].sum()), "areas": int((band == i).sum())}
            for i, (lo, label) in enumerate(SCORE_BANDS)
        ]
        cuts = prio_breaks(out["prioridad"])
        cls = np.searchsorted(cuts, out["prioridad"].to_numpy(), side="right")
        edges = [0.0, *cuts.tolist()]
        stats["priority"] = {
            "breaks": cuts.tolist(),
            "classes": [
                {"min": edges[c], "max": edges[c + 1] if c + 1 < len(edges) else None,
                 "areas": int((cls == c).sum())}
                for c in range(len(cuts) + 1)
            ],
        }
    else:
        s = out["score"].to_numpy()
        stats["counts"] = [
            {"score": k, "pct": float(share[s == k].sum()), "areas": int((s == k).sum())}
            for k in range(n_svc, -1, -1)
        ]
    return stats
