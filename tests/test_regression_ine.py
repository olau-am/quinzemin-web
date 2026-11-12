"""
Cambio de cartografía en València: secciones del geoportal municipal (589 polígonos, snapshot del
main.py original) → seccionado nacional del INE (591 secciones, catálogo spain/).

Bundle híbrido: secciones y población del INE + servicios, red y GTFS congelados del snapshot
(legacy/workdir/data). Así la única diferencia con tests/fixtures/legacy/ es la cartografía, y se
comprueba que sus efectos son pequeños y acotados:
  - +1.891 hab. en el análisis (queda fuera solo la sección 17008, 1.360 hab., sin polígono INE);
  - cobertura por servicio a ±1,5 puntos de la de referencia;
  - ≥ 97 % de las secciones comunes con el mismo resultado dentro/fuera del umbral
    (cambian las secciones cuyo polígono, y por tanto su centroide, es distinto).
"""
import copy
from pathlib import Path

import pandas as pd
import pytest
import yaml

from quinzemin import bundle as qb
from quinzemin import engine, prepare, storage

ROOT = Path(__file__).resolve().parent.parent
LEGACY_DATA = ROOT / "legacy" / "workdir" / "data"
SPAIN = ROOT / "data" / "spain"
FIX = ROOT / "tests" / "fixtures" / "legacy"

pytestmark = [
    pytest.mark.data,
    pytest.mark.skipif(not (LEGACY_DATA / "areas.geojson").exists(), reason="faltan legacy/workdir/data"),
    pytest.mark.skipif(not (SPAIN / "municipios.json").exists(),
                       reason="falta el catálogo nacional (python -m quinzemin.catalog)"),
]

COVERAGE_TOL_PP = 1.5
MIN_AGREEMENT = 0.97


@pytest.fixture(scope="module")
def cfg():
    return yaml.safe_load((ROOT / "legacy" / "config.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ine_areas():
    st = storage.LocalStorage(ROOT / "data")
    return prepare.load_areas(st, prepare.municipality(st, "46250"))


@pytest.fixture(scope="module")
def hybrid(cfg, ine_areas):
    leg = qb.load_legacy(cfg, LEGACY_DATA)
    areas, _ = ine_areas
    return engine.Bundle(areas=areas.to_crs(leg.areas.crs), services=leg.services,
                         network=leg.network, transit=leg.transit)


def test_secciones_y_poblacion_ine(ine_areas):
    areas, rep = ine_areas
    assert len(areas) == 591 and areas["cusec"].is_unique
    assert rep["secciones_ine_sin_poligono"] == ["4625017008"]
    assert rep["poblacion_sin_poligono"] == 1360
    assert rep["poblacion_asignada"] == 827_650          # snapshot (geoportal): 825.759
    assert len(rep["secciones_sin_poblacion"]) == 4


@pytest.mark.parametrize("mode", engine.MODES)
def test_cambio_de_cartografia_acotado(cfg, hybrid, mode):
    c = copy.deepcopy(cfg)
    c["analysis"]["mode"], c["map"]["view"] = mode, "score"
    res = engine.compute(hybrid, qb.params_from_config(c))
    cov = res.stats["coverage"]

    ref = pd.read_csv(FIX / f"{mode}_score_cobertura.csv").set_index("servicio")["pct_poblacion"]
    for sid in res.stats["services"]:
        assert cov["per_service"][sid] == pytest.approx(ref[sid], abs=COVERAGE_TOL_PP), sid
    assert cov["all"] == pytest.approx(ref["_todos"], abs=COVERAGE_TOL_PP)
    assert cov["none"] == pytest.approx(ref["_ninguno"], abs=COVERAGE_TOL_PP)

    # Secciones comunes (en el geoportal, las partidas en varios polígonos no se comparan)
    snap = pd.read_csv(FIX / f"{mode}_score.csv", dtype={"cusec": str})
    single = snap[~snap["cusec"].duplicated(keep=False)].set_index("cusec")
    got = res.table.set_index("cusec")
    common = single.index.intersection(got.index)
    assert len(common) >= 575
    for sid in res.stats["services"]:
        agree = (single.loc[common, f"{sid}_ok"] == got.loc[common, f"{sid}_ok"]).mean()
        assert agree >= MIN_AGREEMENT, f"{sid}: {agree:.1%}"
