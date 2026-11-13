"""
CLI local equivalente al main.py original: calcula y genera un mapa HTML con folium.

  PYTHONIOENCODING=utf-8 .venv/Scripts/python -m quinzemin.cli \\
      --config config/cities/46250.yaml --data legacy/workdir/data \\
      --mode network --view score --out data/mapa.html

Los parámetros que no se indican salen del YAML (analysis.*, map.view, services[].weight).
"""
import argparse
import json
import time
import webbrowser
from pathlib import Path

import pandas as pd

from quinzemin import bundle as qb
from quinzemin import config, engine, population, storage


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="quinzemin: accesibilidad por sección censal")
    ap.add_argument("--config", default=str(config.city_config_path()), help="YAML de la ciudad")
    ap.add_argument("--data", default="data", help="carpeta con los datos descargados (formato del proyecto original)")
    ap.add_argument("--ine", help="usar el bundle preparado de este municipio (python -m quinzemin.prepare INE); "
                                  "con --config, los parámetros salen del YAML (p. ej. el que descarga la web)")
    ap.add_argument("--mode", choices=engine.MODES)
    ap.add_argument("--view", choices=engine.VIEWS)
    ap.add_argument("--distance", type=float, help="umbral en metros (distance/network)")
    ap.add_argument("--minutes", type=float, help="umbral en minutos (transit)")
    ap.add_argument("--decay", type=float, help="decaimiento de la puntuación (1 = binario)")
    ap.add_argument("--out", default="data/mapa.html", help="HTML de salida")
    ap.add_argument("--csv", help="guardar también la tabla por área en este CSV")
    ap.add_argument("--stats", help="guardar los indicadores en este JSON")
    ap.add_argument("--open", action="store_true", help="abrir el mapa en el navegador")
    a = ap.parse_args(argv)

    cfg = config.load(a.config) if (a.config != str(config.city_config_path()) or not a.ine) else None
    if a.ine and cfg is None and config.city_config_path(a.ine).exists():
        cfg = config.load(config.city_config_path(a.ine))
    t0 = time.perf_counter()
    if a.ine:
        b = qb.load(storage.from_env(), a.ine)
        params = qb.params_from_config(cfg) if cfg else qb.default_params(b)
        if cfg is None:
            cfg = {"city": {"attributions": b.meta.get("attributions") or []}, "map": {}}
    else:
        params = qb.params_from_config(cfg)
    for attr, val in (("mode", a.mode), ("view", a.view), ("distance_m", a.distance),
                      ("time_minutes", a.minutes), ("decay", a.decay)):
        if val is not None:
            setattr(params, attr, val)
    if not a.ine:
        b = qb.load_legacy(cfg, a.data, network=params.mode == "network", transit=params.mode == "transit")
    t1 = time.perf_counter()
    res = engine.compute(b, params)
    t2 = time.perf_counter()
    print(f"Carga {t1 - t0:.1f} s · cálculo {t2 - t1:.1f} s ({params.mode}/{params.view}, {len(b.areas)} áreas)")

    for sid, n in res.stats["areas_ok"].items():
        print(f"  {b.services[sid].label if sid in b.services else sid:<24}{n:>5}/{len(b.areas)} áreas")
    cov = res.stats.get("coverage")
    if cov:
        print(f"Población con acceso ({population.fmt_int(cov['total'])} hab.):")
        print(f"  {'Todos los servicios':<24}{population.fmt_pct(cov['all']):>9}")
        print(f"  {'Ningún servicio':<24}{population.fmt_pct(cov['none']):>9}")
        if "mean_score" in cov:
            print(f"  {'Puntuación media':<24}{cov['mean_score']:>7.1f}/100".replace(".", ","))

    from quinzemin.render_folium import render

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    render(b, res, attribution=config.build_attr(cfg),
           emoji_min_zoom=cfg.get("map", {}).get("emoji_min_zoom", 14)).save(str(out))
    print(f"Mapa: {out}")
    if a.csv:
        pd.DataFrame(res.table).to_csv(a.csv, index=False)
        print(f"Tabla: {a.csv}")
    if a.stats:
        Path(a.stats).write_text(json.dumps(res.stats, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"Indicadores: {a.stats}")
    if a.open:
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()
