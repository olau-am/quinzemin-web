"""
Snapshot de regresión del main.py original (antes de separar motor y presentación).

Ejecuta legacy/main.py (copia congelada de quinzemin/src) en los 3 modos × 2 vistas sobre los
datos congelados de legacy/workdir/data/ y guarda, por combinación:

  tests/fixtures/legacy/{mode}_{view}.csv            columnas por área (sin geometría) + cusec
  tests/fixtures/legacy/{mode}_{view}_cobertura.csv  % de población con acceso
  tests/fixtures/legacy/{mode}_{view}.log            salida por consola
  tests/fixtures/legacy/runs.json                    parámetros y tiempos

Uso (desde la raíz del repo):
  PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/snapshot_legacy.py            # todo
  PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/snapshot_legacy.py network distance
  ... snapshot_legacy.py transit:score                                              # una sola

transit en vista score tarda ~47 min (cuello de botella en TransitGraph.service_min_time).
"""
import contextlib
import copy
import io
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT    = Path(__file__).resolve().parent.parent
LEGACY  = ROOT / "legacy"
WORKDIR = LEGACY / "workdir"
OUT     = ROOT / "tests" / "fixtures" / "legacy"

MODES = ["distance", "network", "transit"]
VIEWS = ["count", "score"]


class _Tee(io.TextIOBase):
    def __init__(self, *streams):
        self._streams = streams

    def write(self, s):
        for st in self._streams:
            st.write(s)
            st.flush()
        return len(s)


def _selected(argv: list[str]) -> list[tuple[str, str]]:
    if not argv:
        return [(m, v) for m in MODES for v in VIEWS]
    out = []
    for a in argv:
        mode, _, view = a.partition(":")
        if mode not in MODES or (view and view not in VIEWS):
            sys.exit(f"Argumento no válido: {a!r} (modos {MODES}, vistas {VIEWS})")
        out += [(mode, v) for v in ([view] if view else VIEWS)]
    return out


def main(argv: list[str]) -> None:
    if not (WORKDIR / "data" / "areas.geojson").exists():
        sys.exit(f"Faltan los datos de entrada en {WORKDIR / 'data'} (ver legacy/README.md)")

    sys.path.insert(0, str(LEGACY))
    os.environ["QUINZEMIN_NO_BROWSER"] = "1"
    os.chdir(WORKDIR)                       # main.py usa rutas relativas data/ y docs/

    import folium
    import main as legacy_main  # legacy/main.py
    import population as legacy_population  # legacy/population.py

    import config as legacy_config  # legacy/config.py

    base_cfg = legacy_config.load()
    captured = {}
    _GeoJson = folium.GeoJson

    def _capture(data, *a, **kw):           # main() no devuelve las áreas: se capturan aquí
        captured["areas"] = data
        return _GeoJson(data, *a, **kw)

    folium.GeoJson = _capture
    OUT.mkdir(parents=True, exist_ok=True)
    runs_path = OUT / "runs.json"
    runs = json.loads(runs_path.read_text(encoding="utf-8")) if runs_path.exists() else {}

    for mode, view in _selected(argv):
        key = f"{mode}_{view}"
        cfg = copy.deepcopy(base_cfg)
        cfg["analysis"]["mode"] = mode
        cfg["map"]["view"] = view
        legacy_config.load = lambda c=cfg: copy.deepcopy(c)
        captured.clear()

        print(f"\n=== {key} ===", file=sys.__stdout__, flush=True)
        log = io.StringIO()
        t0 = time.perf_counter()
        with contextlib.redirect_stdout(_Tee(sys.__stdout__, log)):
            legacy_main.main()
        elapsed = time.perf_counter() - t0

        areas = captured["areas"]
        df = areas.drop(columns="geometry")
        df.insert(0, "cusec", legacy_population._area_codes(areas, cfg["city"]["population"]["area_code"]))
        df.to_csv(OUT / f"{key}.csv", index=False, encoding="utf-8", lineterminator="\n")
        shutil.copy("data/cobertura_poblacion.csv", OUT / f"{key}_cobertura.csv")
        (OUT / f"{key}.log").write_text(log.getvalue(), encoding="utf-8")

        a = cfg["analysis"]
        runs[key] = {
            "mode": mode, "view": view, "seconds": round(elapsed, 1), "areas": len(df),
            "distance_m": a["distance_m"], "time_minutes": a["time_minutes"],
            "decay": a["scoring"]["decay"] if view == "score" else 1.0,
            "max_walk_to_stop_m": a["max_walk_to_stop_m"], "walking_speed_mpm": a["walking_speed_mpm"],
        }
        runs_path.write_text(json.dumps(runs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"=== {key}: {elapsed:.1f} s → {OUT / (key + '.csv')}", file=sys.__stdout__, flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
