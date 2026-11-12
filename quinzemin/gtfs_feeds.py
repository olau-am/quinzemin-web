"""
Feeds GTFS para municipios sin configuración propia.

Catálogo: Mobility Database (MobilityData), CSV público feeds_v2.csv. Sus metadatos son CC0; cada
feed tiene la licencia de su proveedor (urls.license). La mayoría de los feeds españoles vienen del
Punto de Acceso Nacional de Transporte (NAP, MITRAMS), que exige registro para descargar; Mobility
Database guarda una copia de la última versión (urls.latest) que se descarga sin autenticación.

  refresh_catalog(st)          → spain/gtfs_feeds.json (feeds GTFS de España no obsoletos)
  feeds_for(st, bbox)          → feeds cuya zona de cobertura corta la del municipio
  resolve(st, ref)             → feed del catálogo ("mdb-795") o URL del usuario (https)
  download(url, dest)          → descarga validada (https, sin IPs privadas, ≤ 200 MB, zip GTFS)
"""
from __future__ import annotations

import io
import ipaddress
import math
import socket
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests

from quinzemin import storage as qs

CATALOG_URL = "https://files.mobilitydatabase.org/feeds_v2.csv"
CATALOG_KEY = "spain/gtfs_feeds.json"
MAX_BYTES = 200 * 1024 * 1024
HEADERS = {"User-Agent": "quinzemin/1.0 (ciudad-15-minutos)"}
NAP_LICENSE = "https://nap.transportes.gob.es/licencia-datos"
REQUIRED_FILES = ("stops.txt", "stop_times.txt")


class FeedError(ValueError):
    pass


# ----------------------------------------------------------------------
# Catálogo
# ----------------------------------------------------------------------

def refresh_catalog(st: qs.Storage) -> list[dict]:
    r = requests.get(CATALOG_URL, headers=HEADERS, timeout=120)
    r.raise_for_status()
    d = pd.read_csv(io.BytesIO(r.content), dtype=str)
    es = d[(d["location.country_code"] == "ES") & (d["data_type"] == "gtfs") & (d["status"] != "deprecated")]
    feeds = []
    for _, f in es.iterrows():
        auth = (f.get("urls.authentication_type") or "0") != "0"
        url = f.get("urls.latest") if isinstance(f.get("urls.latest"), str) else None
        if url is None and not auth:
            url = f.get("urls.direct_download")
        if not isinstance(url, str):
            continue                                 # sin copia descargable sin registro
        bbox = [f.get(f"location.bounding_box.{k}") for k in
                ("minimum_longitude", "minimum_latitude", "maximum_longitude", "maximum_latitude")]
        feeds.append({
            "id": f["id"],
            "provider": f.get("provider") if isinstance(f.get("provider"), str) else f["id"],
            "name": f.get("name") if isinstance(f.get("name"), str) else "",
            "status": f.get("status"),
            "official": f.get("is_official") == "True",
            "region": f.get("location.subdivision_name") if isinstance(f.get("location.subdivision_name"), str) else "",
            "bbox": [float(x) for x in bbox] if all(isinstance(x, str) for x in bbox) else None,
            "url": url,
            "license_url": f.get("urls.license") if isinstance(f.get("urls.license"), str) else None,
        })
    st.write_json(CATALOG_KEY, feeds, indent=1)
    print(f"✓ Feeds GTFS de España: {len(feeds)} → {CATALOG_KEY}")
    return feeds


def catalog(st: qs.Storage) -> list[dict]:
    return st.read_json(CATALOG_KEY) if st.exists(CATALOG_KEY) else refresh_catalog(st)


AUTO_MAX_KM2 = 100_000      # modo automático: solo feeds urbanos o regionales


def bbox_km2(b: list[float]) -> float:
    w = (b[2] - b[0]) * 111.32 * math.cos(math.radians((b[1] + b[3]) / 2))
    return abs(w * (b[3] - b[1]) * 110.57)


def scope(f: dict) -> str:
    """local (< 5.000 km²), regional (< 100.000 km²) o nacional: el rectángulo de un feed nacional
    (ALSA, Renfe, ferris…) corta casi cualquier municipio aunque no tenga paradas en él."""
    a = bbox_km2(f["bbox"]) if f.get("bbox") else float("inf")
    return "local" if a < 5_000 else "regional" if a < AUTO_MAX_KM2 else "nacional"


def feeds_for(st: qs.Storage, bbox: list[float], include_inactive: bool = False,
              max_km2: float | None = None) -> list[dict]:
    """
    Feeds cuyo rectángulo de cobertura corta el del municipio (lon_min, lat_min, lon_max, lat_max),
    de menor a mayor cobertura. max_km2 descarta los de cobertura mayor (modo automático).
    """
    x0, y0, x1, y1 = bbox
    out = []
    for f in catalog(st):
        b = f.get("bbox")
        if not b or (f["status"] != "active" and not include_inactive):
            continue
        if max_km2 is not None and bbox_km2(b) > max_km2:
            continue
        if b[0] <= x1 and b[2] >= x0 and b[1] <= y1 and b[3] >= y0:
            out.append({**f, "scope": scope(f)})
    return sorted(out, key=lambda f: bbox_km2(f["bbox"]))


def resolve(st: qs.Storage, ref: str | dict) -> dict:
    """'mdb-795' → feed del catálogo; 'https://…' → feed del usuario; dict → tal cual (YAML de la ciudad)."""
    if isinstance(ref, dict):
        return ref
    if ref.startswith("mdb-"):
        for f in catalog(st):
            if f["id"] == ref:
                return f
        raise FeedError(f"No existe el feed {ref} en el catálogo de España")
    check_url(ref)
    host = urlparse(ref).hostname
    return {"id": "url-" + host.replace(".", "-"), "provider": host, "name": "", "url": ref,
            "license_url": None, "user": True}


def is_nap(feed: dict) -> bool:
    return (feed.get("license_url") or "").rstrip("/") == NAP_LICENSE


# ----------------------------------------------------------------------
# Descarga segura (URLs de usuarios)
# ----------------------------------------------------------------------

def check_url(url: str) -> None:
    """Solo https y nunca hacia direcciones privadas, locales o reservadas (evita SSRF)."""
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname:
        raise FeedError("La URL del GTFS debe empezar por https://")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise FeedError(f"No se puede resolver {u.hostname}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise FeedError(f"{u.hostname} apunta a una dirección no pública ({ip})")


def download(url: str, dest: str | Path, max_bytes: int = MAX_BYTES, verify: bool = True) -> Path:
    """Descarga un GTFS comprobando la URL (también tras cada redirección), el tamaño y el contenido."""
    dest = Path(dest)
    # Las redirecciones se siguen a mano para validar cada destino
    for _ in range(6):
        check_url(url)
        r = requests.get(url, headers=HEADERS, stream=True, timeout=(15, 120), verify=verify,
                         allow_redirects=False)
        if not r.is_redirect:
            break
        url = requests.compat.urljoin(r.url, r.headers["Location"])
        r.close()
    else:
        raise FeedError("Demasiadas redirecciones")
    with r:
        r.raise_for_status()
        if int(r.headers.get("Content-Length") or 0) > max_bytes:
            raise FeedError(f"El GTFS supera {max_bytes // 2**20} MB")
        size = 0
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                size += len(chunk)
                if size > max_bytes:
                    raise FeedError(f"El GTFS supera {max_bytes // 2**20} MB")
                fh.write(chunk)
    validate(dest)
    return dest


def validate(path: str | Path) -> dict:
    """Comprueba que es un zip GTFS con paradas y horarios; devuelve sus fechas de vigencia si las trae."""
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise FeedError("El fichero descargado no es un zip") from e
    names = {Path(n).name for n in z.namelist()}
    missing = [f for f in REQUIRED_FILES if f not in names]
    if missing:
        raise FeedError(f"El zip no es un GTFS válido: faltan {', '.join(missing)}")
    return feed_dates(z)


def feed_dates(z: zipfile.ZipFile) -> dict:
    """Vigencia del feed (feed_info.txt o, si no, calendar.txt): la fecha de actualización a conservar."""
    def _read(name):
        n = next((x for x in z.namelist() if Path(x).name == name), None)
        return pd.read_csv(z.open(n), dtype=str, encoding="utf-8-sig").rename(columns=str.strip)             .apply(lambda c: c.str.strip()) if n else None
    info = _read("feed_info.txt")
    if info is not None and {"feed_start_date", "feed_end_date"} <= set(info.columns) and len(info):
        return {"start": info["feed_start_date"].min(), "end": info["feed_end_date"].max(),
                "version": info["feed_version"].iloc[0] if "feed_version" in info else None}
    cal = _read("calendar.txt")
    if cal is not None and {"start_date", "end_date"} <= set(cal.columns) and len(cal):
        return {"start": cal["start_date"].min(), "end": cal["end_date"].max(), "version": None}
    return {}
