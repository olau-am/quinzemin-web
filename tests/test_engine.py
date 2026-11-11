"""Tests unitarios del motor (sin datos reales: ciudad sintética de 3 áreas)."""
import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, box

from quinzemin import engine
from quinzemin.engine import Bundle, Params, Service, ServiceParams

CRS = "EPSG:25830"


# ----------------------------------------------------------------------
# credit
# ----------------------------------------------------------------------

def test_credit_binario_con_decay_1():
    np.testing.assert_array_equal(engine.credit([0, 800, 801, 5000], 800, 1.0), [1, 1, 0, 0])


def test_credit_caida_lineal():
    # 1 hasta el umbral, 0,5 a mitad de camino, 0 en umbral × decay y más allá
    np.testing.assert_allclose(engine.credit([0, 800, 1200, 1600, 3000], 800, 2.0), [1, 1, 0.5, 0, 0])


def test_credit_infinito_es_cero():
    assert engine.credit([np.inf], 900, 2.0)[0] == 0
    assert engine.credit([np.inf], 900, 1.0)[0] == 0


# ----------------------------------------------------------------------
# Tramos y prioridad
# ----------------------------------------------------------------------

def test_band_index():
    np.testing.assert_array_equal(engine.band_index([100, 99.9, 90, 89.9, 75, 50, 49.9, 0]),
                                  [0, 1, 1, 2, 2, 3, 4, 4])


def test_prio_breaks_sin_ceros_ni_repetidos():
    assert engine.prio_breaks([0, 0, 0]).tolist() == [0.0]
    cuts = engine.prio_breaks([0, 10, 10, 10, 10, 10, 20, 30, 40, 50])
    assert list(cuts) == sorted(set(cuts))
    assert len(cuts) <= engine.N_PRIO_CLASSES - 1


# ----------------------------------------------------------------------
# compute() en modo distance
# ----------------------------------------------------------------------

@pytest.fixture
def toy():
    # 3 áreas de 100×100 m en fila; centroides en x = 50, 1050, 3050
    areas = gpd.GeoDataFrame(
        {"area_id": ["a", "b", "c"], "cusec": ["4600000001", "4600000002", "4600000003"],
         "poblacion": [100.0, 300.0, 600.0]},
        geometry=[box(0, 0, 100, 100), box(1000, 0, 1100, 100), box(3000, 0, 3100, 100)], crs=CRS)
    farm = gpd.GeoDataFrame({"name": ["F1", None]}, geometry=[Point(50, 50), Point(1450, 50)], crs=CRS)
    park = gpd.GeoDataFrame({"name": ["Parque"]}, geometry=[Point(1050, 1250)], crs=CRS)
    return Bundle(areas=areas, services={
        "farmacias": Service("farmacias", "Farmacias", farm, "💊"),
        "zonas_verdes": Service("zonas_verdes", "Zonas verdes", park, "🌳"),
    })


def test_compute_distance(toy):
    p = Params(mode="distance", view="score", distance_m=800, decay=2.0,
               services=[ServiceParams("farmacias"), ServiceParams("zonas_verdes", weight=3)])
    t = engine.compute(toy, p).table
    assert t["dist_farmacias_m"].tolist() == [0, 400, 1600]
    assert t["nombre_farmacias"].tolist() == ["F1", "—", "—"]       # sin nombre → "—"
    assert t["farmacias_ok"].tolist() == [True, True, False]
    assert t["dist_zonas_verdes_m"].tolist() == [round(np.hypot(1000, 1200)), 1200, round(np.hypot(2000, 1200))]
    # crédito zonas verdes: área b a 1200 m → 0,5 ; área a a 1562 m → (1600 − 1562)/800 = 0,0475
    assert t["cred_zonas_verdes"].tolist() == [0.048, 0.5, 0.0]
    # puntuación = 100·(1·cred_farm + 3·cred_zv)/4
    expected = np.round(100 * (np.array([1, 1, 0]) + 3 * np.array([1 - 762 / 800, 0.5, 0])) / 4, 1)
    np.testing.assert_allclose(t["puntuacion"], expected, atol=0.051)
    assert t["score"].tolist() == [1, 1, 0]
    assert t["prioridad"].tolist() == list(np.round(t["poblacion"] * (100 - t["puntuacion"]) / 100).astype(int))


def test_compute_vista_count_es_binaria(toy):
    t = engine.compute(toy, Params(mode="distance", view="count", decay=2.0)).table
    assert set(np.unique(t[["cred_farmacias", "cred_zonas_verdes"]].to_numpy())) <= {0.0, 1.0}


def test_servicio_sin_datos_cuenta_con_credito_cero(toy):
    p = Params(mode="distance", view="score",
               services=[ServiceParams("farmacias"), ServiceParams("inexistente")])
    res = engine.compute(toy, p)
    assert res.table["cred_inexistente"].tolist() == [0, 0, 0]
    assert res.table["puntuacion"].tolist() == [50.0, 50.0, 0.0]
    assert res.stats["coverage"]["per_service"]["inexistente"] == 0


def test_stats(toy):
    res = engine.compute(toy, Params(mode="distance", view="score",
                                     services=[ServiceParams("farmacias")]))
    st = res.stats
    assert st["population"]["total"] == 1000
    assert st["coverage"]["all"] == pytest.approx(40.0)
    assert st["coverage"]["none"] == pytest.approx(60.0)
    assert sum(b["pct"] for b in st["bands"]) == pytest.approx(100)
    assert sum(c["areas"] for c in st["priority"]["classes"]) == 3


def test_modo_sin_datos_da_error(toy):
    with pytest.raises(ValueError, match="red peatonal"):
        engine.compute(toy, Params(mode="network"))
    with pytest.raises(ValueError, match="GTFS"):
        engine.compute(toy, Params(mode="transit"))


def test_sin_poblacion(toy):
    toy.areas = toy.areas.drop(columns="poblacion")
    res = engine.compute(toy, Params(mode="distance"))
    assert "prioridad" not in res.table.columns
    assert res.stats["population"] is None
    assert isinstance(res.table, pd.DataFrame)
