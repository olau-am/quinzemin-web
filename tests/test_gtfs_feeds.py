import zipfile

import numpy as np
import pytest
from shapely.geometry import Point, box

from quinzemin import gtfs_feeds, storage
from quinzemin.gtfs import TransitGraph

# ----------------------------------------------------------------------
# URLs de usuario (SSRF)
# ----------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://example.com/gtfs.zip",            # sin https
    "ftp://example.com/gtfs.zip",
    "https://127.0.0.1/gtfs.zip",             # local
    "https://10.0.0.5/gtfs.zip",              # red privada
    "https://192.168.1.10/gtfs.zip",
    "https://169.254.169.254/latest/meta-data",   # metadatos de la nube
    "https://[::1]/gtfs.zip",
    "https://localhost/gtfs.zip",
])
def test_check_url_rechaza(url):
    with pytest.raises(gtfs_feeds.FeedError):
        gtfs_feeds.check_url(url)


# ----------------------------------------------------------------------
# Catálogo
# ----------------------------------------------------------------------

def _catalog(tmp_path):
    st = storage.LocalStorage(tmp_path)
    st.write_json(gtfs_feeds.CATALOG_KEY, [
        {"id": "mdb-1", "provider": "Urbano", "status": "active", "bbox": [-0.45, 39.40, -0.30, 39.55],
         "url": "https://x/1.zip", "license_url": None},
        {"id": "mdb-2", "provider": "Nacional", "status": "active", "bbox": [-9.0, 36.0, 3.3, 43.8],
         "url": "https://x/2.zip", "license_url": gtfs_feeds.NAP_LICENSE},
        {"id": "mdb-3", "provider": "Lejos", "status": "active", "bbox": [2.0, 41.3, 2.2, 41.5],
         "url": "https://x/3.zip", "license_url": None},
        {"id": "mdb-4", "provider": "Inactivo", "status": "inactive", "bbox": [-0.45, 39.40, -0.30, 39.55],
         "url": "https://x/4.zip", "license_url": None},
    ])
    return st


def test_feeds_for(tmp_path):
    st = _catalog(tmp_path)
    burjassot = [-0.43, 39.50, -0.40, 39.52]
    assert [f["id"] for f in gtfs_feeds.feeds_for(st, burjassot)] == ["mdb-1", "mdb-2"]
    assert [f["id"] for f in gtfs_feeds.feeds_for(st, burjassot, max_km2=gtfs_feeds.AUTO_MAX_KM2)] == ["mdb-1"]
    assert "mdb-4" in [f["id"] for f in gtfs_feeds.feeds_for(st, burjassot, include_inactive=True)]
    scopes = {f["id"]: f["scope"] for f in gtfs_feeds.feeds_for(st, burjassot)}
    assert scopes == {"mdb-1": "local", "mdb-2": "nacional"}


def test_resolve_e_is_nap(tmp_path):
    st = _catalog(tmp_path)
    assert gtfs_feeds.resolve(st, "mdb-2")["provider"] == "Nacional"
    assert gtfs_feeds.is_nap(gtfs_feeds.resolve(st, "mdb-2"))
    with pytest.raises(gtfs_feeds.FeedError):
        gtfs_feeds.resolve(st, "mdb-999")


# ----------------------------------------------------------------------
# GTFS sintético: validación, cabeceras sucias y recorte
# ----------------------------------------------------------------------

def _gtfs(path, padded=False):
    """3 paradas en línea (A dentro, B fuera, C dentro) y un viaje A→B→C."""
    sp = "   " if padded else ""
    stops = (f"stop_id,stop_name,stop_lat,stop_lon{sp}\n"
             "A,Parada A,39.500,-0.400\nB,Parada B,39.600,-0.400\nC,Parada C,39.510,-0.400\n")
    times = (f"trip_id,arrival_time,departure_time,stop_id,stop_sequence{sp}\n"
             "t1,08:00:00,08:00:00,A,1\nt1,08:10:00,08:10:00,B,2\nt1,08:25:00,08:25:00,C,3\n")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("stops.txt", stops)
        z.writestr("stop_times.txt", times)
        z.writestr("feed_info.txt", "feed_publisher_name,feed_start_date,feed_end_date\nX, 20260101,20261231\n")
    return path


def test_validate(tmp_path):
    p = _gtfs(tmp_path / "ok.zip")
    assert gtfs_feeds.validate(p) == {"start": "20260101", "end": "20261231", "version": None}
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("stops.txt", "stop_id\n")
    with pytest.raises(gtfs_feeds.FeedError, match="stop_times"):
        gtfs_feeds.validate(bad)
    (tmp_path / "nozip.zip").write_bytes(b"<html>")
    with pytest.raises(gtfs_feeds.FeedError, match="zip"):
        gtfs_feeds.validate(tmp_path / "nozip.zip")


@pytest.mark.parametrize("padded", [False, True])
def test_recorte_une_paradas_interiores(tmp_path, padded):
    p = _gtfs(tmp_path / "g.zip", padded=padded)
    src = [{"id": "x", "label": "x", "path": str(p)}]
    full = TransitGraph.load(src, "EPSG:25830")
    assert full.n_stops == 3 and len(full.edge_a) == 2
    clip = box(-0.45, 39.45, -0.35, 39.55)          # deja fuera B
    tg = TransitGraph.load(src, "EPSG:25830", clip=clip)
    assert sorted(tg.stop_ids) == ["x_A", "x_C"]
    # A → C directo con el tiempo real del viaje (25 min), pasando por B fuera de la zona
    assert len(tg.edge_a) == 1 and tg.edge_t[0] == 25 * 60
    # Con 1 viaje al día el modelo estima 8 h de espera (16 h / 2): presupuesto amplio
    reach = tg.reachable_matrix(np.array([Point(tg.stop_xy[0])]), 12 * 3600, 80 / 60, 500)
    assert np.isfinite(reach[0]).all()
    assert reach[0][1] - reach[0][0] == pytest.approx(25 * 60)
