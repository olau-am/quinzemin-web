"""API con una ciudad sintética en un almacenamiento temporal (no necesita datos reales ni red)."""
import io
import json

import geopandas as gpd
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Point, box

import api.app as appmod
from quinzemin import jobs, storage

INE = "99001"
CRS = "EPSG:25830"


def _toy_bundle(st: storage.LocalStorage) -> None:
    areas = gpd.GeoDataFrame(
        {"area_id": ["9900101001", "9900101002", "9900101003"], "cusec": ["9900101001", "9900101002", "9900101003"],
         "poblacion": [100.0, 300.0, 600.0]},
        geometry=[box(700000, 4370000, 700100, 4370100), box(701000, 4370000, 701100, 4370100),
                  box(703000, 4370000, 703100, 4370100)], crs=CRS)
    buf = io.BytesIO()
    areas.to_parquet(buf)
    st.write_bytes(f"cities/{INE}/areas.parquet", buf.getvalue())
    st.write_bytes(f"cities/{INE}/areas.geojson", areas.to_crs(4326).to_json().encode())
    for sid, pts in {"farmacias": [(700050, 4370050)], "zonas_verdes": [(701050, 4371250)]}.items():
        g = gpd.GeoDataFrame({"name": [f"{sid} 1"]}, geometry=[Point(*p) for p in pts], crs=CRS)
        buf = io.BytesIO()
        g.to_parquet(buf)
        st.write_bytes(f"cities/{INE}/services/{sid}.parquet", buf.getvalue())
    st.write_json(f"cities/{INE}/meta.json", {
        "ine": INE, "name": "Villa Prueba", "province": "Prueba", "center": [39.4, -0.4], "bbox": [-0.5, 39.3, -0.3, 39.5],
        "population": 1000, "population_year": 2024, "km2": 1.0,
        "services": [{"id": "farmacias", "label": "Farmacias", "emoji": "💊", "source": "OpenStreetMap",
                      "polygon_access": False, "features": 1},
                     {"id": "zonas_verdes", "label": "Zonas Verdes", "emoji": "🌳", "source": "OpenStreetMap",
                      "polygon_access": False, "features": 1}],
        "has_network": False, "has_transit": False, "gtfs_sources": [],
        "attributions": [{"label": "© OpenStreetMap contributors (ODbL)", "url": "https://www.openstreetmap.org/copyright"}],
        "prepared_at": "2026-11-12T10:00:00+00:00", "config": "osm",
    })
    st.write_json("spain/municipios.json", [
        {"ine": INE, "nombre": "Villa Prueba", "cpro": "99", "provincia": "Prueba", "secciones": 3,
         "km2": 1.0, "poblacion": 1000, "anyo": 2024},
        {"ine": "99002", "nombre": "Ávila de Prueba", "cpro": "99", "provincia": "Prueba", "secciones": 2,
         "km2": 2.0, "poblacion": 500, "anyo": 2024},
        {"ine": "99003", "nombre": "Megalópolis", "cpro": "99", "provincia": "Prueba", "secciones": 9,
         "km2": 900.0, "poblacion": 3_000_000, "anyo": 2024},
    ])


@pytest.fixture
def client(tmp_path, monkeypatch):
    st = storage.LocalStorage(tmp_path)
    _toy_bundle(st)
    monkeypatch.setattr(appmod, "_store", st)
    monkeypatch.setattr(appmod, "_municipalities", None)
    monkeypatch.setattr(appmod, "_runs_limiter", appmod._RateLimiter(3, 3600))
    appmod._bundles.clear()
    launched = []
    monkeypatch.setattr(jobs, "launch", lambda job_id, st=None: launched.append(job_id))
    c = TestClient(appmod.app)
    c.launched = launched
    c.st = st
    return c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_municipios_y_ciudades(client):
    r = client.get("/api/municipalities", params={"q": "avila"})        # sin tilde
    assert [m["ine"] for m in r.json()] == ["99002"]
    assert client.get("/api/municipalities", params={"q": "990"}).json()[0]["prepared"] is True
    assert [c["ine"] for c in client.get("/api/cities").json()] == [INE]
    city = client.get(f"/api/cities/{INE}").json()
    assert city["prepared"] and city["name"] == "Villa Prueba" and "OpenStreetMap" in city["attribution_html"]
    assert client.get("/api/cities/99002").json()["prepared"] is False
    assert client.get("/api/cities/12345").status_code == 404
    assert client.get("/api/cities/abc").status_code == 422
    r = client.get(f"/api/cities/{INE}/areas.geojson")
    assert r.status_code == 200 and r.json()["type"] == "FeatureCollection"


def test_run_calcula_y_cachea(client):
    body = {"ine": INE, "mode": "distance", "view": "score", "distance_m": 800}
    r1 = client.post("/api/runs", json=body)
    assert r1.status_code == 200 and r1.headers["X-Cache"] == "miss"
    p = r1.json()
    assert set(p) >= {"city", "params", "services", "stats", "areas", "run"}
    assert p["areas"]["9900101001"]["svc"]["farmacias"][0] is True
    assert p["stats"]["coverage"]["total"] == 1000
    r2 = client.post("/api/runs", json=body)
    assert r2.headers["X-Cache"] == "hit" and r2.json()["run"]["hash"] == p["run"]["hash"]
    # en la vista count el decay no cuenta: misma clave de caché
    c1 = client.post("/api/runs", json={**body, "view": "count", "decay": 1.5}).json()
    c2 = client.post("/api/runs", json={**body, "view": "count", "decay": 2.5})
    assert c2.headers["X-Cache"] == "hit" and c2.json()["run"]["hash"] == c1["run"]["hash"]
    # URL compartible y YAML reproducible
    h = p["run"]["hash"]
    assert client.get(f"/api/runs/{INE}/{h}").json()["run"]["hash"] == h
    y = client.get(f"/api/runs/{INE}/{h}/config.yaml")
    assert y.status_code == 200 and "mode: distance" in y.text and "quinzemin.cli --ine 99001" in y.text


@pytest.mark.parametrize("body,status", [
    ({"ine": INE, "distance_m": 50}, 422),                     # fuera de rango
    ({"ine": INE, "decay": 5}, 422),
    ({"ine": INE, "mode": "metro"}, 422),
    ({"ine": INE, "mode": "network"}, 422),                    # sin red peatonal
    ({"ine": INE, "mode": "transit"}, 422),                    # sin GTFS
    ({"ine": INE, "mode": "distance", "services": [{"id": "bares"}]}, 422),
    ({"ine": INE, "mode": "distance", "services": [{"id": "farmacias", "weight": 0}]}, 422),
    ({"ine": INE, "mode": "distance", "services": [{"id": "farmacias", "weight": 9}]}, 422),
    ({"ine": "99002", "mode": "distance"}, 404),               # no preparado
])
def test_run_validacion(client, body, status):
    assert client.post("/api/runs", json=body).status_code == status


def test_run_limite_por_ip(client):
    for d in (400, 500, 600):
        assert client.post("/api/runs", json={"ine": INE, "mode": "distance", "distance_m": d}).status_code == 200
    r = client.post("/api/runs", json={"ine": INE, "mode": "distance", "distance_m": 700})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    # lo que ya está en caché no cuenta
    assert client.post("/api/runs", json={"ine": INE, "mode": "distance", "distance_m": 400}).status_code == 200


def test_preparar(client):
    assert client.post(f"/api/cities/{INE}/prepare").status_code == 409            # ya preparada
    assert client.post("/api/cities/99003/prepare").status_code == 422              # demasiado grande
    r = client.post("/api/cities/99002/prepare", json={"gtfs_feeds": ["ftp://x"]})
    assert r.status_code == 422
    r = client.post("/api/cities/99002/prepare")
    assert r.status_code == 202
    job = r.json()
    assert job["status"] == "queued" and "client" not in job and client.launched == [job["id"]]
    assert client.get(f"/api/jobs/{job['id']}").json()["ine"] == "99002"
    assert client.get("/api/cities/99002").json()["job"] == job["id"]
    # una preparación a la vez
    r = client.post("/api/cities/99002/prepare")
    assert r.status_code == 429


def test_lock_y_limite_diario(client):
    st = client.st
    j = jobs.create(st, "99002", client="x")
    with pytest.raises(jobs.Busy):
        jobs.create(st, "99002", client="x")
    jobs.release(st, "99002", j["id"])
    jobs.create(st, "99002", client="x")            # liberado: se puede volver a crear
    jobs.release(st, "99002", json.loads((st.root / "locks" / "prepare-99002.json").read_text())["job"])
    # dos preparaciones al día por cliente (TestClient → mismo hash de IP)
    cid = appmod.client_id(type("R", (), {"headers": {}, "client": type("C", (), {"host": "testclient"})()})())
    # un intento que no llegó a lanzarse no cuenta
    jj = jobs.create(st, "99002", client=cid)
    jobs.release(st, "99002", jj["id"])
    for _ in range(2):
        jj = jobs.create(st, "99002", client=cid)
        jobs.update(st, jj, launched=True)
        jobs.release(st, "99002", jj["id"])
    r = client.post("/api/cities/99002/prepare")
    assert r.status_code == 429 and "al día" in r.json()["detail"]


def test_fallo_al_lanzar_no_gasta_cupo(client, monkeypatch):
    def boom(job_id, st=None):
        raise PermissionError("403 run.jobs.runWithOverrides")
    monkeypatch.setattr(jobs, "launch", boom)
    for _ in range(3):
        r = client.post("/api/cities/99002/prepare")
        assert r.status_code == 503 and "403" in r.json()["detail"]


def test_cors(client):
    r = client.options("/api/runs", headers={"Origin": "https://olau-am.github.io",
                                              "Access-Control-Request-Method": "POST",
                                              "Access-Control-Request-Headers": "content-type"})
    assert r.headers.get("access-control-allow-origin") == "https://olau-am.github.io"
    r = client.options("/api/runs", headers={"Origin": "https://malicioso.example",
                                              "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers
