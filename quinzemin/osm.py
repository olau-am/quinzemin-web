"""
Servicios desde OpenStreetMap para municipios sin fuentes propias, y clasificación de centros
educativos en primaria / secundaria.

En OSM `amenity=school` no distingue la etapa. Solo se separan primaria y secundaria cuando los
datos lo permiten con seguridad (decisión del usuario): cada centro se clasifica con señales
fiables y, si en el municipio quedan clasificados al menos EDU_MIN_CLASSIFIED de los centros, se
separan; si no, se usa un único servicio "Educación".

Señales (por orden):
  1. isced:level  → 1 = primaria; 2, 3 = secundaria (puede traer varias: "1;2", "1-3")
  2. school=*     → primary / secondary (y variantes como "primary;secondary")
  3. nombre       → prefijos oficiales: CEIP, CP, CRA, Escola… = primaria;
                    IES, IESO, Institut, Instituto, SES = secundaria;
                    CEO, CPI, CEIPSO, Centro Integrado = ambas
Los "Colegio …" sin más datos (concertados, que suelen tener las dos etapas) quedan sin clasificar.
Las escuelas solo infantiles (EEI, Escola Bressol…) se descartan.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import yaml

TEMPLATE = Path(__file__).resolve().parent.parent / "config" / "osm_services.yaml"
EDU_MIN_CLASSIFIED = 0.85

_P, _S = "primaria", "secundaria"
_OTHER = "otro"      # centro educativo que no es de primaria ni secundaria → se descarta
_NAME_RULES = [      # se aplica la primera que coincide
    # ambas etapas
    (re.compile(r"^\s*(C\.?\s?E\.?\s?O\.?|C\.?\s?P\.?\s?I\.?|CEIPSO)\b|centro integrado|"
                r"educaci[oó]n obligatoria|primaria y secundaria|prim[àa]ria i secund[àa]ria", re.I), {_P, _S}),
    # otras enseñanzas: FP, arte, idiomas, adultos, educación especial, música…
    (re.compile(r"formaci[oó]n? profesional|\bF\.?P\.?\b|escuela de arte|escola d'art|\bidiomas\b|\bE\.?O\.?I\.?\b|"
                r"adultos|adultes|\bE\.?P\.?A\.?\b|\bE\.?P\.?L\.?A\.?\b|\bF\.?P\.?A\.?\b|educaci[oó]n? especial|"
                r"conservatori|m[uú]sica|danza|dansa|autoescuela|academia|universi", re.I), {_OTHER}),
    # solo infantil (específico)
    (re.compile(r"^\s*(E\.?\s?E\.?\s?I\.?|C\.?\s?A\.?\s?I\.?)\b|escuela infantil|escola infantil|escola bressol|"
                r"escoleta|haurreskola|guarder[ií]a|bressol|nursery|kindergarten", re.I), set()),
    # secundaria
    (re.compile(r"^\s*(I\.?\s?E\.?\s?S\.?\s?O?\.?|S\.?\s?E\.?\s?S\.?|INS)\b|\binstitut(o)?\b|"
                r"secundaria|secund[àa]ria|bachillerato|batxillerat|\bliceo\b|lyc[ée]e", re.I), {_S}),
    # primaria
    (re.compile(r"^\s*(C\.?\s?E\.?\s?I\.?\s?P\.?|C\.?\s?P\.?|C\.?\s?R\.?\s?A\.?|C\.?\s?R\.?\s?I\.?\s?E\.?)\b|"
                r"primaria|prim[àa]ria|colegio p[uú]blico|col[·.]?legi p[uú]blic|\bescola\b|\beskola\b|"
                r"rural agrupado", re.I), {_P}),
    # solo infantil (genérico: "Centro de Educación Infantil" sin primaria)
    (re.compile(r"educaci[oó]n? infantil|\bC\.?\s?E\.?\s?I\.?\b", re.I), set()),
]


def classify_school(tags: dict) -> set[str] | None:
    """{primaria}, {secundaria}, ambas, set() (solo infantil: descartar) o None (no se sabe)."""
    isced = str(tags.get("isced:level") or "").strip()
    if isced and isced.lower() != "nan":
        levels = set()
        for part in re.split(r"[;,]", isced):
            part = part.strip()
            m = re.fullmatch(r"(\d)\s*-\s*(\d)", part)
            nums = range(int(m.group(1)), int(m.group(2)) + 1) if m else ([int(part)] if part.isdigit() else [])
            for n in nums:
                if n == 1:
                    levels.add(_P)
                elif n in (2, 3):
                    levels.add(_S)
        if levels:
            return levels
        if all(p.strip() == "0" for p in re.split(r"[;,]", isced)):
            return set()
    school = str(tags.get("school") or "").lower()
    if school and school != "nan":
        levels = set()
        if "primary" in school:
            levels.add(_P)
        if "secondary" in school:
            levels.add(_S)
        if levels:
            return levels
        if school in ("kindergarten", "nursery", "preschool"):
            return set()
    name = str(tags.get("name") or "")
    for rx, levels in _NAME_RULES:
        if rx.search(name):
            return set(levels)
    return None


def split_education(gdf: gpd.GeoDataFrame, min_classified: float = EDU_MIN_CLASSIFIED):
    """
    Devuelve (servicios, informe). servicios = {"primaria": gdf, "secundaria": gdf} si se separan,
    o {"educacion": gdf} si no. Los centros sin clasificar, cuando se separa, cuentan en las dos
    etapas (son pocos y suelen ser concertados con ambas).
    """
    cls = [classify_school(r) for r in gdf.drop(columns="geometry").to_dict("records")]
    infant = np.array([c == set() for c in cls])
    other = np.array([c is not None and _OTHER in c for c in cls])
    unknown = np.array([c is None for c in cls])
    keep = ~infant & ~other
    known = keep & ~unknown
    n_schools = int(keep.sum())
    share = float(known.sum() / n_schools) if n_schools else 0.0
    report = {"centros": n_schools, "clasificados": int(known.sum()), "sin_clasificar": int(unknown.sum()),
              "descartados_infantil": int(infant.sum()), "descartados_otras_ensenanzas": int(other.sum()),
              "proporcion_clasificada": round(share, 3), "umbral": min_classified}
    if n_schools and share >= min_classified:
        prim = np.array([c is None or (c and _P in c) for c in cls]) & keep
        sec = np.array([c is None or (c and _S in c) for c in cls]) & keep
        report["separado"] = True
        report["primaria"], report["secundaria"] = int(prim.sum()), int(sec.sum())
        return {_P: gdf[prim].reset_index(drop=True), _S: gdf[sec].reset_index(drop=True)}, report
    report["separado"] = False
    return {"educacion": gdf[keep].reset_index(drop=True)}, report


def load_template(path: str | Path = TEMPLATE) -> list[dict]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))["services"]


OVERPASS_MIRRORS = [
    "https://overpass-api.de/api",
    "https://maps.mail.ru/osm/tools/overpass/api",   # el que usaba el proyecto original
    "https://overpass.kumi.systems/api",
    "https://overpass.private.coffee/api",
]
_last_ok = [0]       # índice del último mirror que respondió: se prueba primero en la siguiente consulta


def _base_mirrors() -> list[str]:
    import os

    return [u.strip() for u in os.environ.get("QM_OVERPASS_URLS", "").split(",") if u.strip()] or OVERPASS_MIRRORS


def _mirrors() -> list[str]:
    """Mirrors empezando por el último que funcionó (QM_OVERPASS_URLS los sustituye, separados por comas)."""
    urls = _base_mirrors()
    k = _last_ok[0] % len(urls)
    return urls[k:] + urls[:k]
OVERPASS_TIMEOUT_S = 120      # por petición (también el [timeout:] del servidor Overpass)
OVERPASS_DEADLINE_S = 900     # tope total de reintentos para una consulta


def with_overpass(fn, *args, timeout_s: int = OVERPASS_TIMEOUT_S, deadline_s: int = OVERPASS_DEADLINE_S,
                  wait_s: float = 10, **kwargs):
    """
    Ejecuta una llamada de osmnx que consulta Overpass. El servidor público a menudo responde
    429/502/504 o no contesta cuando está saturado: se prueba cada mirror con un tiempo de espera
    acotado y, si fallan todos, se repite la ronda con espera exponencial hasta deadline_s.
    """
    import os

    import osmnx as ox

    ox.settings.requests_timeout = timeout_s
    ox.settings.http_user_agent = "quinzemin/1.0 (ciudad-15-minutos)"
    # Sin esperas de cuota de osmnx (pueden ser largas y no acotadas): se reintenta en otro mirror
    ox.settings.overpass_rate_limit = False
    ox.settings.use_cache = os.environ.get("QM_OSM_CACHE", "1") != "0"
    t0, attempt, last = time.monotonic(), 0, None
    while True:
        urls = _mirrors()
        for url in urls:
            ox.settings.overpass_url = url
            t = time.monotonic()
            try:
                result = fn(*args, **kwargs)
                _last_ok[0] = _base_mirrors().index(url)
                print(f"  Overpass {url.split('/')[2]}: OK en {time.monotonic() - t:.0f} s", flush=True)
                return result
            except ox._errors.InsufficientResponseError:
                raise
            except Exception as e:          # noqa: BLE001 — cualquier fallo de red → siguiente mirror
                last = e
                print(f"  Overpass {url.split('/')[2]}: {type(e).__name__} tras {time.monotonic() - t:.0f} s",
                      flush=True)
            if time.monotonic() - t0 > deadline_s:
                raise RuntimeError(f"Overpass no disponible tras {deadline_s} s: {last}")
        pause = min(wait_s * 2 ** attempt, 120)
        print(f"  Todos los mirrors han fallado; nuevo intento en {pause:.0f} s", flush=True)
        time.sleep(pause)
        attempt += 1


def overpass_json(query: str, timeout_s: int = OVERPASS_TIMEOUT_S, deadline_s: int = OVERPASS_DEADLINE_S) -> dict:
    """Consulta Overpass QL en bruto (fuentes 'overpass' de config/cities/*.yaml), con los mismos mirrors."""
    import osmnx as ox
    import requests

    def _call():
        # with_overpass pone en ox.settings.overpass_url el mirror que toca probar
        r = requests.post(f"{ox.settings.overpass_url}/interpreter",
                          data={"data": f"[out:json][timeout:{timeout_s}];{query}"},
                          headers={"User-Agent": "quinzemin/1.0 (ciudad-15-minutos)"},
                          timeout=(15, timeout_s + 30))
        r.raise_for_status()
        return r.json()

    return with_overpass(_call, timeout_s=timeout_s, deadline_s=deadline_s)


def _empty() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"name": pd.Series(dtype=str)}, geometry=[], crs="EPSG:4326")


def fetch_features(polygon_wgs84, tags: dict) -> gpd.GeoDataFrame:
    """Elementos OSM (nodos, vías y relaciones) dentro del polígono, en EPSG:4326."""
    import osmnx as ox

    try:
        gdf = with_overpass(ox.features_from_polygon, polygon_wgs84, tags)
    except ox._errors.InsufficientResponseError:
        return _empty()
    return gdf.reset_index()


def merge_tags(specs: list[dict]) -> dict:
    """Une varios filtros de etiquetas (clave → True | valor | [valores]) en uno solo (OR)."""
    out: dict = {}
    for spec in specs:
        for k, v in spec.items():
            if out.get(k) is True or v is True:
                out[k] = True
                continue
            vals = set(out.get(k, [])) | set([v] if isinstance(v, str) else v)
            out[k] = sorted(vals)
    return out


def match_tags(gdf: gpd.GeoDataFrame, tags: dict) -> np.ndarray:
    """Máscara de los elementos que cumplen alguna de las etiquetas (mismo formato que osmnx)."""
    mask = np.zeros(len(gdf), dtype=bool)
    for k, v in tags.items():
        if k not in gdf.columns:
            continue
        col = gdf[k]
        if v is True:
            mask |= col.notna().to_numpy()
        else:
            vals = {v} if isinstance(v, str) else set(v)
            # OSM admite varios valores separados por ";" (p. ej. "clinic;doctors")
            mask |= col.fillna("").astype(str).map(lambda x, vals=vals: bool(vals & set(x.split(";")))).to_numpy()
    return mask


def fetch_template(polygon_wgs84, template: list[dict]) -> dict[str, gpd.GeoDataFrame]:
    """Todos los servicios de la plantilla con UNA sola consulta a Overpass, separados en local."""
    import osmnx as ox

    try:
        allf = with_overpass(ox.features_from_polygon, polygon_wgs84,
                             merge_tags([s["tags"] for s in template])).reset_index()
    except ox._errors.InsufficientResponseError:
        return {s["id"]: _empty() for s in template}
    return {s["id"]: allf[match_tags(allf, s["tags"])].reset_index(drop=True) for s in template}


def to_service_points(gdf: gpd.GeoDataFrame, polygon_access: bool) -> gpd.GeoDataFrame:
    """Polígonos → punto representativo, salvo los servicios con acceso por polígono (parques)."""
    geom = gdf.geometry
    if polygon_access:
        keep = geom.geom_type.isin(["Polygon", "MultiPolygon"]) | (geom.geom_type == "Point")
        return gdf[keep].reset_index(drop=True)
    pts = geom.where(geom.geom_type == "Point", geom.representative_point())
    return gdf.set_geometry(pts)
