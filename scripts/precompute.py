"""
Precálculo de València para la carga instantánea de la web (GitHub Pages).

Genera docs/data/cities/46250/:
  areas.geojson   copia del de bundle (secciones simplificadas en WGS84); se descarga una vez
  result.json     el mismo payload que devuelve POST /api/runs (quinzemin/results.py)

Parte del bundle de València (cities/46250/ del almacenamiento), que genera:
  PYTHONIOENCODING=utf-8 .venv/Scripts/python -m quinzemin.prepare 46250 --gtfs

Uso (desde la raíz del repo):
  PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/precompute.py
"""
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quinzemin import bundle as qb
from quinzemin import config, engine, results, storage

INE = "46250"
OUT = ROOT / "docs" / "data" / "cities" / INE


def main() -> None:
    cfg = yaml.safe_load(config.city_config_path(INE).read_text(encoding="utf-8"))
    cfg["analysis"]["mode"] = "network"
    cfg["map"]["view"] = "score"
    st = storage.from_env()
    b = qb.load(st, INE, transit=False)
    b.meta["zoom"] = cfg["city"].get("zoom", 12)
    res = engine.compute(b, qb.params_from_config(cfg))
    payload = results.to_payload(b, res, emoji_min_zoom=cfg.get("map", {}).get("emoji_min_zoom", 14))

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "areas.geojson").write_bytes(st.read_bytes(f"cities/{INE}/areas.geojson"))
    (OUT / "result.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                                     encoding="utf-8")
    for f in ("areas.geojson", "result.json"):
        print(f"✓ {OUT / f} ({(OUT / f).stat().st_size / 1e3:.0f} KB)")
    cov = res.stats["coverage"]
    print(f"  Todos los servicios {cov['all']:.1f} % · puntuación media {cov['mean_score']:.1f}")


if __name__ == "__main__":
    main()
