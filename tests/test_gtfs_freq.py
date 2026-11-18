"""
Modelo de transporte por frecuencias de línea, con un GTFS sintético cuyos tiempos se calculan a mano.

  L1: A → B → C          cada 10 min (espera 5 min), 2 min entre paradas
  L2: C → E              cada 30 min (espera 15 min), 5 min; además un servicio solo de domingo
                         cada 2 min que NO debe contar (día representativo = laborable)
  L3: D → F              cada 20 min (espera 10 min), 4 min; D está a 100 m de C (trasbordo a pie)
Todas las paradas en línea sobre un meridiano; velocidad a pie 80 m/min.
"""
import zipfile

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

from quinzemin import engine, gtfs
from quinzemin.engine import Bundle, Params, Service, ServiceParams

LAT0, LON0 = 39.47, -0.376
M_PER_DEG = 111_000
WALK = 80 / 60
CRS = "EPSG:32630"
# norte en metros desde A
STOPS = {"A": 0, "B": 1000, "C": 2000, "D": 2100, "E": 5000, "F": 7000}


def _lat(m):
    return LAT0 + m / M_PER_DEG


def _hms(s):
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _feed(path):
    stops = "stop_id,stop_name,stop_lat,stop_lon\n" + "".join(
        f"{k},Parada {k},{_lat(m):.7f},{LON0}\n" for k, m in STOPS.items())
    trips, times = ["route_id,service_id,trip_id,direction_id"], ["trip_id,arrival_time,departure_time,stop_id,stop_sequence"]

    def line(route, svc, seq, every, hop, start=6 * 3600, end=22 * 3600):
        for i, t0 in enumerate(range(start, end, every * 60)):
            tid = f"{route}_{svc}_{i}"
            trips.append(f"{route},{svc},{tid},0")
            for k, s in enumerate(seq):
                t = _hms(t0 + k * hop)
                times.append(f"{tid},{t},{t},{s},{k + 1}")

    line("L1", "lab", ["A", "B", "C"], 10, 120)
    line("L2", "lab", ["C", "E"], 30, 300)
    line("L2", "dom", ["C", "E"], 2, 300)          # solo domingos: no debe acortar la espera
    line("L3", "lab", ["D", "F"], 20, 240)
    cal = ("service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
           "lab,1,1,1,1,1,0,0,20260105,20261231\n"
           "dom,0,0,0,0,0,0,1,20260105,20261231\n")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("stops.txt", stops)
        z.writestr("trips.txt", "\n".join(trips) + "\n")
        z.writestr("stop_times.txt", "\n".join(times) + "\n")
        z.writestr("calendar.txt", cal)
    return path


@pytest.fixture(scope="module")
def tg(tmp_path_factory):
    p = _feed(tmp_path_factory.mktemp("gtfs") / "feed.zip")
    return gtfs.TransitGraph.load([{"id": "x", "label": "x", "path": str(p)}], CRS)


def _origin_at(tg, stop):
    i = list(tg.stop_ids).index(f"x_{stop}")
    return np.array([Point(tg.stop_xy[i])]), i


def _idx(tg, stop):
    return list(tg.stop_ids).index(f"x_{stop}")


def test_dia_representativo_es_laborable(tg):
    day = tg.freq["info"]["days"]["x"]
    assert pd.Timestamp(day).dayofweek in (1, 2, 3)          # martes, miércoles o jueves


def test_esperas_por_linea(tg):
    f = tg.freq
    # esperas distintas de infinito: L1 en A y B (5 min), L2 en C (15 min, sin el servicio de domingo),
    # L3 en D (10 min)
    waits = sorted(set(np.round(f["rs_wait"][np.isfinite(f["rs_wait"])] / 60, 2)))
    assert waits == [5.0, 10.0, 15.0]


def test_tiempos_con_esperas_y_trasbordos(tg):
    o, _ = _origin_at(tg, "A")
    r = tg.reachable_matrix_freq(o, 4 * 3600, WALK, 500)[0]
    assert np.isinf(r[_idx(tg, "A")])                         # sin viajar no se "alcanza" la parada
    assert r[_idx(tg, "B")] == pytest.approx(300 + 120, abs=1)
    assert r[_idx(tg, "C")] == pytest.approx(300 + 240, abs=1)
    # trasbordo en la misma parada: espera de L2 (15 min) + viaje
    assert r[_idx(tg, "E")] == pytest.approx(540 + 900 + 300, abs=1)
    # trasbordo a pie C → D (100 m)
    assert r[_idx(tg, "D")] == pytest.approx(540 + 100 / WALK, abs=3)
    assert r[_idx(tg, "F")] == pytest.approx(540 + 100 / WALK + 600 + 240, abs=3)
    # penalización por trasbordo (2 min): solo en las subidas tras la primera
    rp = tg.reachable_matrix_freq(o, 4 * 3600, WALK, 500, transfer_penalty_s=120)[0]
    assert rp[_idx(tg, "C")] == pytest.approx(r[_idx(tg, "C")], abs=1e-6)
    assert rp[_idx(tg, "E")] == pytest.approx(r[_idx(tg, "E")] + 120, abs=1e-6)


def test_el_simplificado_no_cuenta_la_espera_del_trasbordo(tg):
    o, _ = _origin_at(tg, "A")
    simple = tg.reachable_matrix(o, 4 * 3600, WALK, 500)[0]
    freq = tg.reachable_matrix_freq(o, 4 * 3600, WALK, 500)[0]
    assert simple[_idx(tg, "E")] < freq[_idx(tg, "E")] - 800   # le falta la espera de L2 en C


def test_presupuesto(tg):
    o, _ = _origin_at(tg, "A")
    r = tg.reachable_matrix_freq(o, 600, WALK, 500)[0]
    assert np.isfinite(r[_idx(tg, "C")]) and np.isinf(r[_idx(tg, "E")])


def test_engine_minimo_entre_andar_y_transporte(tg):
    a = tg.stop_xy[_idx(tg, "A")]
    c = tg.stop_xy[_idx(tg, "C")]
    areas = gpd.GeoDataFrame({"area_id": ["s1"], "cusec": ["0000000001"], "poblacion": [100.0]},
                             geometry=[Point(a).buffer(10)], crs=CRS)
    near = gpd.GeoDataFrame({"name": ["Al lado"]}, geometry=[Point(a[0] + 50, a[1])], crs=CRS)
    far = gpd.GeoDataFrame({"name": ["En C"]}, geometry=[Point(c)], crs=CRS)
    b = Bundle(areas=areas, transit=tg, services={
        "cerca": Service("cerca", "Cerca", near), "lejos": Service("lejos", "Lejos", far)})
    p = Params(mode="transit", view="count", time_minutes=15,
               services=[ServiceParams("cerca"), ServiceParams("lejos")])
    res = engine.compute(b, p)
    t = res.table.iloc[0]
    assert t["dist_cerca_m"] == 1                               # 50 m andando ≈ 0,6 min
    assert t["dist_lejos_m"] == 9                               # 5 min espera + 4 min viaje
    assert res.stats["diagnostics"]["transit_model"] == "frequencies"
    assert res.stats["diagnostics"]["walk_faster"] == {"cerca": 1, "lejos": 0}
    # El simplificado no cuenta ir andando: el servicio de al lado necesita subir a L1 en A
    simple = engine.compute(b, Params(**{**p.__dict__, "transit_model": "simple"})).table.iloc[0]
    assert simple["dist_cerca_m"] > t["dist_cerca_m"]


def test_bundle_antiguo_usa_el_simplificado(tg):
    old = gtfs.TransitGraph(tg.stop_ids, tg.stop_names, tg.stop_xy, tg.wait_s, tg.edge_a, tg.edge_b,
                            tg.edge_t, tg.utm_crs)              # sin freq, como un transit.pkl anterior
    areas = gpd.GeoDataFrame({"area_id": ["s1"], "cusec": ["0000000001"]},
                             geometry=[Point(tg.stop_xy[0]).buffer(10)], crs=CRS)
    svc = gpd.GeoDataFrame({"name": ["x"]}, geometry=[Point(tg.stop_xy[2])], crs=CRS)
    res = engine.compute(Bundle(areas=areas, transit=old, services={"s": Service("s", "S", svc)}),
                         Params(mode="transit", services=[ServiceParams("s")]))
    assert res.stats["diagnostics"]["transit_model"] == "simple"
    assert res.stats["diagnostics"]["transit_model_fallback"] is True
