"""
Precálculo de València para la carga instantánea de la web (GitHub Pages).

Genera docs/data/cities/46250/:
  areas.geojson   geometría simplificada en WGS84 (id de área + cusec); se descarga una vez
  result.json     parámetros, servicios, indicadores y resultados por área (sin geometría)

Uso (desde la raíz del repo):
  PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/precompute.py

Parte del bundle de València (cities/46250/ del almacenamiento, secciones censales del INE), que
genera:  PYTHONIOENCODING=utf-8 .venv/Scripts/python -m quinzemin.prepare 46250 --gtfs
"""
import json
import sys
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quinzemin import bundle as qb
from quinzemin import config, engine, storage

INE = "46250"
OUT = ROOT / "docs" / "data" / "cities" / INE
SIMPLIFY_M = 2.0


def _json_default(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return None if np.isnan(o) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    raise TypeError(type(o))


def main() -> None:
    cfg = yaml.safe_load(config.city_config_path(INE).read_text(encoding="utf-8"))
    cfg["analysis"]["mode"] = "network"
    cfg["map"]["view"] = "score"
    b = qb.load(storage.from_env(), INE, transit=False)
    params = qb.params_from_config(cfg)
    res = engine.compute(b, params)
    t = res.table
    OUT.mkdir(parents=True, exist_ok=True)

    # Geometría (simplificada en UTM, guardada en WGS84 con 6 decimales ≈ 0,1 m)
    geo = b.areas[["area_id", "cusec", "geometry"]].copy()
    geo["geometry"] = geo.geometry.simplify(SIMPLIFY_M, preserve_topology=True)
    geo.to_crs(epsg=4326).to_file(OUT / "areas.geojson", driver="GeoJSON", COORDINATE_PRECISION=6)

    svc_ids = [sp.id for sp in params.services]
    cent = b.areas.geometry.centroid.to_crs(epsg=4326)
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

    city = cfg["city"]
    attribution = config.build_attr({"city": {"attributions": b.meta["attributions"]}})
    # El INE, el Ajuntament y el NAP piden indicar la fecha de obtención de los datos
    y, m, d = b.meta["prepared_at"][:10].split("-")
    attribution += f" | Datos obtenidos el {d}/{m}/{y}"
    payload = {
        "city": {"ine": INE, "name": b.meta["name"], "center": b.meta["center"], "zoom": city["zoom"],
                 "attribution": attribution,
                 "emoji_min_zoom": cfg.get("map", {}).get("emoji_min_zoom", 14)},
        "params": {"mode": params.mode, "view": params.view, "distance_m": params.distance_m,
                   "time_minutes": params.time_minutes, "decay": params.decay,
                   "services": [{"id": sp.id, "weight": sp.weight} for sp in params.services]},
        "services": [{"id": s.id, "label": s.label, "emoji": s.emoji}
                     for s in (b.services[i] for i in svc_ids)],
        "stats": res.stats,
        "areas": areas,
    }
    (OUT / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=_json_default),
        encoding="utf-8")
    for f in ("areas.geojson", "result.json"):
        print(f"✓ {OUT / f} ({(OUT / f).stat().st_size / 1e3:.0f} KB)")
    cov = res.stats["coverage"]
    print(f"  Todos los servicios {cov['all']:.1f} % · puntuación media {cov['mean_score']:.1f}")


if __name__ == "__main__":
    main()
