"""
Preparación de un municipio: genera su bundle en cities/{ine}/ del almacenamiento.

  meta.json               municipio, servicios, fuentes, fechas, informe de la preparación
  areas.parquet           secciones censales del INE en UTM + cusec + población (para el cálculo)
  areas.geojson           las mismas en WGS84, simplificadas (para el navegador)
  services/{id}.parquet   elementos de cada servicio (UTM, columnas name + geometry)
  walk_graph.npz          red peatonal OSM (sin osmnx en runtime)
  transit.pkl             grafo GTFS (opcional)

Servicios: config/cities/{ine}.yaml si existe (fuentes municipales, p. ej. València); si no, la
plantilla OSM (config/osm_services.yaml), con primaria/secundaria separadas solo si los datos lo
permiten (ver osm.py).

  PYTHONIOENCODING=utf-8 .venv/Scripts/python -m quinzemin.prepare 46078
  PYTHONIOENCODING=utf-8 .venv/Scripts/python -m quinzemin.prepare 46250 --gtfs

Requiere el catálogo nacional (python -m quinzemin.catalog).
"""
from __future__ import annotations

import argparse
import io
import json
import pickle
import re
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import geopandas as gpd
import pandas as pd

from quinzemin import __version__, config, osm, population
from quinzemin import storage as qs

BUFFER_M = 1000          # margen alrededor del término (servicios y red justo fuera cuentan)
SIMPLIFY_M = 2.0         # simplificación de la geometría para el navegador
MAX_POPULATION = 1_500_000
MAX_KM2 = 400
TRANSIT_CLIP_M = 1500   # paradas que se conservan alrededor del término (feeds regionales)
OSM_ATTRIBUTION = {"label": "© OpenStreetMap contributors (ODbL)", "url": "https://www.openstreetmap.org/copyright"}
INE_ATTRIBUTION = {"label": "Fuente: INE, elaboración propia (CC BY 4.0)", "url": "https://www.ine.es"}
NAP_ATTRIBUTION = {"label": "Fuente: MITRAMS, Punto de Acceso Nacional de Transporte (datos explotados)",
                   "url": "https://nap.transportes.gob.es/licencia-datos"}
ATTRIBUTION_BY_SOURCE = {
    "Ajuntament de València": {"label": "Fuente de los datos: Ajuntament de València – Dades Obertes (CC BY 4.0)",
                               "url": "https://www.valencia.es/val/ajuntament/govern-obert"},
    "ICV/GVA": {"label": "© Institut Cartogràfic Valencià, Generalitat", "url": "https://icv.gva.es"},
}


class PrepareError(RuntimeError):
    pass


_T0 = [time.monotonic()]


def _print_progress(stage: str, pct: int, msg: str = "") -> None:
    """Progreso por consola con el tiempo transcurrido (la API lo guardará en jobs/{id}.json)."""
    el = time.monotonic() - _T0[0]
    print(f"[{el:6.0f} s · {pct:3d} %] {stage}{': ' + msg if msg else ''}", flush=True)


def municipality(st: qs.Storage, ine: str) -> dict:
    for m in st.read_json("spain/municipios.json"):
        if m["ine"] == ine:
            return m
    raise PrepareError(f"No existe el municipio {ine} en el catálogo")


def check_limits(mun: dict) -> None:
    pop, km2 = mun.get("poblacion") or 0, mun.get("km2") or 0
    if pop > MAX_POPULATION or km2 > MAX_KM2:
        raise PrepareError(f"{mun['nombre']} supera los límites ({pop} hab., {km2} km²; "
                           f"máx. {MAX_POPULATION} hab. y {MAX_KM2} km²)")


# ----------------------------------------------------------------------
# Áreas y población
# ----------------------------------------------------------------------

def load_areas(st: qs.Storage, mun: dict) -> tuple[gpd.GeoDataFrame, dict]:
    ine, cpro = mun["ine"], mun["cpro"]
    sec = gpd.read_parquet(st.local_path(f"spain/secciones/{cpro}.parquet"))
    sec = sec[sec["CUMUN"] == ine].reset_index(drop=True)
    utm = sec.estimate_utm_crs()
    areas = sec.to_crs(utm)[["CUSEC", "geometry"]].rename(columns={"CUSEC": "cusec"})
    # Una sección por fila; si viniera partida en varios polígonos, se unen
    areas = areas.dissolve(by="cusec", as_index=False)
    areas["area_id"] = areas["cusec"]

    pop = pd.read_csv(io.BytesIO(st.read_bytes(f"spain/poblacion/{cpro}.csv")), dtype={"codigo": str})
    pop = pop[pop["codigo"].str.startswith(ine)]
    by_code = pop.set_index("codigo")["poblacion"]
    areas["poblacion"] = areas["cusec"].map(by_code).fillna(0).astype(float)
    orphan = pop[~pop["codigo"].isin(set(areas["cusec"]))]
    report = {
        "secciones": len(areas),
        "anyo": int(pop["anyo"].max()) if len(pop) else None,
        "poblacion_asignada": int(areas["poblacion"].sum()),
        "secciones_sin_poblacion": sorted(set(areas["cusec"]) - set(pop["codigo"])),
        "secciones_ine_sin_poligono": orphan["codigo"].tolist(),
        "poblacion_sin_poligono": int(orphan["poblacion"].sum()),
    }
    return areas[["area_id", "cusec", "poblacion", "geometry"]], report


# ----------------------------------------------------------------------
# Servicios
# ----------------------------------------------------------------------

def _named(gdf: gpd.GeoDataFrame, name_col: str) -> gpd.GeoDataFrame:
    names = gdf[name_col] if name_col in gdf.columns else None
    return gpd.GeoDataFrame({"name": names}, geometry=gdf.geometry.values, crs=gdf.crs)


def services_from_osm(poly_wgs84, utm, progress) -> tuple[list[dict], dict]:
    out, edu_report = [], None
    template = osm.load_template()
    progress("servicios", 20, f"consulta única a OpenStreetMap ({len(template)} tipos)")
    fetched = osm.fetch_template(poly_wgs84, template)
    for s in template:
        raw = fetched[s["id"]]
        gdf = osm.to_service_points(raw, s.get("access") == "polygon") if len(raw) else raw
        if s.get("education"):
            parts, edu_report = osm.split_education(gdf) if len(gdf) else ({s["id"]: gdf}, None)
            for sid, g in parts.items():
                meta = s["education"].get(sid, {"label": s["label"], "emoji": s["emoji"]})
                out.append({"id": sid, "label": meta["label"], "emoji": meta["emoji"],
                            "polygon_access": False, "source": "OpenStreetMap",
                            "features": _named(g, "name").to_crs(utm)})
            continue
        out.append({"id": s["id"], "label": s["label"], "emoji": s["emoji"],
                    "polygon_access": s.get("access") == "polygon", "source": "OpenStreetMap",
                    "features": _named(gdf, "name").to_crs(utm) if len(gdf) else
                    gpd.GeoDataFrame({"name": []}, geometry=[], crs=utm)})
    return out, {"educacion": edu_report}


def _fetch_config_source(src: dict, bbox: str) -> gpd.GeoDataFrame:
    from quinzemin import download

    if src.get("type", "url") == "overpass":
        feats = download._osm_to_geojson(osm.overpass_json(src["overpass_query"].replace("{bbox}", bbox)))["features"]
        gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326") if feats else \
            gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    else:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            p = Path(td) / "f.geojson"
            download._download_url(src["url"], str(p), verify=src.get("verify_ssl", True))
            gdf = gpd.read_file(p)
    return gdf


_KNOWN_HOSTS = {           # dominio de la URL → organismo que se cita
    "valencia.es": "Ajuntament de València",
    "gva.es": "ICV/GVA",
}


def source_name(src: dict) -> str:
    """Organismo de una fuente del YAML: 'attribution' explícito, OSM si es Overpass o por dominio."""
    if src.get("attribution"):
        return src["attribution"]
    if src.get("type") == "overpass":
        return "OpenStreetMap"
    host = urlparse(src.get("url", "")).hostname or ""
    for dom, name in _KNOWN_HOSTS.items():
        if host == dom or host.endswith("." + dom):
            return name
    return host or "?"


def services_from_config(cfg: dict, poly_wgs84, utm, progress) -> tuple[list[dict], dict]:
    minx, miny, maxx, maxy = poly_wgs84.bounds
    bbox = f"{miny},{minx},{maxy},{maxx}"
    out, cache = [], {}
    for i, s in enumerate(cfg["services"]):
        progress("servicios", 20 + 30 * i // len(cfg["services"]), s["label"])
        name_col = s.get("name_field", "name")
        parts = []
        for src in s.get("sources") or [s]:
            key = json.dumps({k: src.get(k) for k in ("type", "url", "overpass_query")}, sort_keys=True)
            if key not in cache:
                cache[key] = _fetch_config_source(src, bbox)
            g = cache[key].copy()
            if src is not s and src.get("name_field") and src["name_field"] in g.columns:
                g[name_col] = g[name_col].fillna(g[src["name_field"]]) if name_col in g.columns \
                    else g[src["name_field"]]
            parts.append(g.to_crs(utm))
        gdf = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=utm)
        if s.get("filter"):
            f = s["filter"]
            gdf = gdf[gdf[f["field"]].str.contains(f["contains"], case=False, na=False)]
        out.append({"id": s["id"], "label": s["label"], "emoji": s.get("emoji", ""),
                    "polygon_access": s.get("access") == "polygon",
                    "source": ", ".join(dict.fromkeys(source_name(src) for src in s.get("sources") or [s])),
                    "features": _named(gdf.reset_index(drop=True), name_col)})
    return out, {}


# ----------------------------------------------------------------------
# Red peatonal y GTFS
# ----------------------------------------------------------------------

def build_network(poly_wgs84, utm):
    import osmnx as ox

    from quinzemin.network import WalkNetwork

    # La red es la consulta más pesada: más margen por petición
    G = osm.with_overpass(ox.graph_from_polygon, poly_wgs84, network_type="walk", simplify=True,
                          retain_all=False, timeout_s=240)
    return WalkNetwork.from_graph(G, utm)


def build_transit(feeds: list[dict], utm, workdir: Path, clip_wgs84=None):
    """
    feeds: fuentes del YAML de la ciudad (de confianza) o del catálogo / URL de un usuario
    (se descargan con validación: https, sin IPs privadas, ≤ 200 MB, zip GTFS).
    Devuelve (TransitGraph, metadatos de cada feed).
    """
    from quinzemin import download, gtfs_feeds
    from quinzemin.gtfs import TransitGraph

    local, info = [], []
    now = datetime.now(UTC).date().isoformat()
    for f in feeds:
        fid = re.sub(r"[^A-Za-z0-9]", "", f["id"])
        path = workdir / f"gtfs_{fid}.zip"
        if f.get("city"):
            download._download_url(f["url"], str(path), verify=f.get("verify_ssl", True))
        else:
            gtfs_feeds.download(f["url"], path)
        dates = gtfs_feeds.validate(path)
        label = f.get("label") or f.get("provider") or f["id"]
        local.append({"id": fid, "label": label, "path": str(path)})
        info.append({"id": f["id"], "label": label,
                     "source": "city" if f.get("city") else ("user" if f.get("user") else "mobilitydatabase"),
                     "url": f["url"], "license_url": f.get("license_url"),
                     "nap": gtfs_feeds.is_nap(f), "downloaded": now, "validity": dates})
    tg = TransitGraph.load(local, utm, clip=clip_wgs84)
    if tg.n_stops == 0:
        raise PrepareError("Ninguno de los feeds GTFS tiene paradas en el municipio")
    # Los feeds sin paradas en la zona no aportan nada: fuera de los metadatos y de las atribuciones
    prefixes = pd.Series(tg.stop_ids).str.split("_", n=1).str[0].value_counts()
    for loc, f in zip(local, info):
        f["stops"] = int(prefixes.get(loc["id"], 0))
    return tg, [f for f in info if f["stops"] > 0]


def gtfs_feed_list(st: qs.Storage, cfg: dict | None, city_gtfs: bool, refs: list[str] | None,
                   auto: bool, bbox: list[float]) -> list[dict]:
    from quinzemin import gtfs_feeds

    feeds = []
    if city_gtfs and cfg:
        for g in cfg.get("analysis", {}).get("gtfs_sources", []):
            feeds.append({**g, "city": True})
    for ref in refs or []:
        feeds.append(gtfs_feeds.resolve(st, ref))
    if auto:
        known = {f["id"] for f in feeds}
        feeds += [f for f in gtfs_feeds.feeds_for(st, bbox, max_km2=gtfs_feeds.AUTO_MAX_KM2)
                  if f["id"] not in known]
    return feeds


def _attribution_key(label: str) -> str:
    """Misma clave para las distintas formas de citar a un organismo (la primera, más completa, gana)."""
    low = label.lower()
    if "openstreetmap" in low:
        return "osm"
    if low == "ine" or low.startswith("fuente: ine"):
        return "ine"
    if "ajuntament de val" in low:
        return "ajuntament"
    if "icv" in low or "institut cartogr" in low:
        return "icv"
    return low


def build_attributions(cfg: dict | None, services: list[dict], gtfs_info: list[dict], date: str) -> list[dict]:
    """
    Atribuciones que exige cada licencia (se muestran en el mapa):
      OSM (ODbL) "© OpenStreetMap contributors" · INE (CC BY 4.0) citando elaboración propia ·
      Ajuntament de València (CC BY 4.0) · ICV/GVA · cada feed GTFS con su licencia ·
      MITRAMS/NAP como fuente de los feeds del Punto de Acceso Nacional (datos explotados).
    `date`: fecha de obtención de los datos (la piden el INE, el Ajuntament y el NAP).
    """
    out = [OSM_ATTRIBUTION, INE_ATTRIBUTION]
    for s in services:
        for name in s["source"].split(", "):
            if name in ATTRIBUTION_BY_SOURCE:
                out.append(ATTRIBUTION_BY_SOURCE[name])
    for a in (cfg or {}).get("city", {}).get("attributions") or []:
        out.append({"label": a["label"], "url": a.get("url")})
    for g in gtfs_info:
        if g["source"] != "city":        # los de la ciudad ya los cita su YAML (city.attributions)
            out.append({"label": g["label"], "url": g.get("license_url"), "kind": "gtfs"})
        if g.get("nap"):
            out.append(NAP_ATTRIBUTION)
    if any(g["source"] == "mobilitydatabase" for g in gtfs_info):
        out.append({"label": "Catálogo GTFS: Mobility Database", "url": "https://mobilitydatabase.org"})
    seen, uniq = set(), []
    for a in out:
        key = _attribution_key(a["label"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append({**a, "date": date})
    return uniq


def update_transit(ine: str, st: qs.Storage | None = None, refs: list[str] | None = None, auto: bool = False,
                   city_gtfs: bool = False, progress=_print_progress) -> dict:
    """Añade o sustituye el transporte público de un municipio ya preparado (sin repetir OSM)."""
    st = st or qs.from_env()
    base = f"cities/{ine}"
    meta = st.read_json(f"{base}/meta.json")
    areas = gpd.read_parquet(st.local_path(f"{base}/areas.parquet"))
    cfg_path = config.city_config_path(ine)
    cfg = config.load(cfg_path) if cfg_path.exists() else None
    feeds = gtfs_feed_list(st, cfg, city_gtfs, refs, auto, meta["bbox"])
    if not feeds:
        raise PrepareError("No hay feeds GTFS que añadir")
    progress("transporte público", 10, ", ".join(f.get("label") or f.get("provider") or f["id"] for f in feeds))
    clip = None if all(f.get("city") for f in feeds) else \
        gpd.GeoSeries([areas.union_all().buffer(TRANSIT_CLIP_M)], crs=areas.crs).to_crs(4326).iloc[0]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        transit, info = build_transit(feeds, areas.crs, Path(td), clip)
    st.write_bytes(f"{base}/transit.pkl", pickle.dumps(transit, protocol=pickle.HIGHEST_PROTOCOL))
    meta["has_transit"], meta["gtfs_sources"] = True, info
    if cfg:   # recalcular también el organismo de cada servicio (bundles anteriores a source_name)
        by_id = {s["id"]: s for s in cfg["services"]}
        for s in meta["services"]:
            if s["id"] in by_id:
                c = by_id[s["id"]]
                s["source"] = ", ".join(dict.fromkeys(source_name(x) for x in c.get("sources") or [c]))
    meta["attributions"] = build_attributions(cfg, meta["services"], info, meta["prepared_at"][:10])
    meta.setdefault("report", {})["transit"] = {"stops": transit.n_stops, "edges": len(transit.edge_a)}
    st.write_json(f"{base}/meta.json", meta, indent=1)
    progress("listo", 100, f"{transit.n_stops} paradas")
    return meta


# ----------------------------------------------------------------------
# Orquestación
# ----------------------------------------------------------------------

def prepare(ine: str, st: qs.Storage | None = None, progress=_print_progress, network: bool = True,
            gtfs: bool = False, gtfs_feeds: list[str] | None = None, gtfs_auto: bool = False,
            force: bool = False) -> dict:
    """
    gtfs: incluir las fuentes GTFS del YAML de la ciudad; gtfs_feeds: ids del catálogo
    ("mdb-795") o URLs https; gtfs_auto: todos los feeds activos del catálogo que cubren el municipio.
    """
    st = st or qs.from_env()
    t0 = time.perf_counter()
    _T0[0] = time.monotonic()
    mun = municipality(st, ine)
    if not force:
        check_limits(mun)
    progress("áreas", 5, f"{mun['nombre']} ({mun['provincia']})")
    areas, area_report = load_areas(st, mun)
    utm = areas.crs
    poly = areas.union_all().buffer(BUFFER_M)
    poly_wgs84 = gpd.GeoSeries([poly], crs=utm).to_crs(epsg=4326).iloc[0]

    cfg_path = config.city_config_path(ine)
    cfg = config.load(cfg_path) if cfg_path.exists() else None
    if cfg:
        services, svc_report = services_from_config(cfg, poly_wgs84, utm, progress)
    else:
        services, svc_report = services_from_osm(poly_wgs84, utm, progress)

    net = None
    if network:
        progress("red peatonal", 55, "descargando de OSM")
        net = build_network(poly_wgs84, utm)
        progress("red peatonal", 80, f"{net.node_count} nodos, {len(net._u)} aristas")

    transit, gtfs_info = None, []
    bbox = [round(v, 5) for v in gpd.GeoSeries([areas.union_all()], crs=utm).to_crs(4326).total_bounds]
    feeds = gtfs_feed_list(st, cfg, gtfs, gtfs_feeds, gtfs_auto, bbox)
    if feeds:
        progress("transporte público", 80, ", ".join(f.get("label") or f.get("provider") or f["id"] for f in feeds))
        # Los feeds de la ciudad se cargan enteros (como en el proyecto original); los demás, recortados
        clip = None if all(f.get("city") for f in feeds) else \
            gpd.GeoSeries([areas.union_all().buffer(TRANSIT_CLIP_M)], crs=utm).to_crs(4326).iloc[0]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            transit, gtfs_info = build_transit(feeds, utm, Path(td), clip)

    progress("guardando", 90)
    base = f"cities/{ine}"
    buf = io.BytesIO()
    areas.to_parquet(buf)
    st.write_bytes(f"{base}/areas.parquet", buf.getvalue())
    web = areas[["area_id", "cusec", "geometry"]].copy()
    web["geometry"] = web.geometry.simplify(SIMPLIFY_M, preserve_topology=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        p = Path(td) / "areas.geojson"
        web.to_crs(epsg=4326).to_file(p, driver="GeoJSON", COORDINATE_PRECISION=6)
        st.upload_file(f"{base}/areas.geojson", p)
        if net is not None:
            net.to_npz(Path(td) / "walk_graph.npz")
            st.upload_file(f"{base}/walk_graph.npz", Path(td) / "walk_graph.npz")
    for s in services:
        buf = io.BytesIO()
        s["features"].to_parquet(buf)
        st.write_bytes(f"{base}/services/{s['id']}.parquet", buf.getvalue())
    if transit is not None:
        st.write_bytes(f"{base}/transit.pkl", pickle.dumps(transit, protocol=pickle.HIGHEST_PROTOCOL))

    center = gpd.GeoSeries([areas.union_all().centroid], crs=utm).to_crs(epsg=4326).iloc[0]
    prepared_at = datetime.now(UTC).isoformat(timespec="seconds")
    attributions = build_attributions(cfg, services, gtfs_info, prepared_at[:10])
    meta = {
        "ine": ine, "name": mun["nombre"], "province": mun["provincia"], "cpro": mun["cpro"],
        "center": [round(center.y, 5), round(center.x, 5)],
        "bbox": bbox,
        "utm_epsg": utm.to_epsg(), "population_year": area_report["anyo"],
        "population": area_report["poblacion_asignada"], "km2": mun["km2"],
        "services": [{"id": s["id"], "label": s["label"], "emoji": s["emoji"], "source": s["source"],
                      "polygon_access": s["polygon_access"], "features": len(s["features"])} for s in services],
        "has_network": net is not None, "has_transit": transit is not None,
        "gtfs_sources": gtfs_info,
        "attributions": attributions, "config": "city" if cfg else "osm",
        "report": {"areas": area_report, **svc_report,
                   "network": {"nodes": net.node_count, "edges": len(net._u)} if net is not None else None,
                   "transit": {"stops": transit.n_stops, "edges": len(transit.edge_a)} if transit else None},
        "prepared_at": prepared_at, "version": __version__,
        "seconds": round(time.perf_counter() - t0, 1),
    }
    st.write_json(f"{base}/meta.json", meta, indent=1)
    progress("listo", 100, f"{meta['seconds']} s")
    return meta


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Prepara un municipio (bundle en cities/{ine}/)")
    ap.add_argument("ine", help="código INE del municipio (5 dígitos)")
    ap.add_argument("--no-network", action="store_true", help="sin red peatonal")
    ap.add_argument("--gtfs", action="store_true", help="incluir las fuentes GTFS de config/cities/{ine}.yaml")
    ap.add_argument("--gtfs-feed", action="append", metavar="REF",
                    help="feed GTFS del catálogo (mdb-795) o URL https; se puede repetir")
    ap.add_argument("--gtfs-auto", action="store_true",
                    help="los feeds activos urbanos o regionales que cubren el municipio")
    ap.add_argument("--gtfs-list", action="store_true", help="solo listar los feeds disponibles y salir")
    ap.add_argument("--only-transit", action="store_true",
                    help="añadir/sustituir solo el transporte público de un municipio ya preparado")
    ap.add_argument("--force", action="store_true", help="ignorar los límites de tamaño")
    a = ap.parse_args(argv)
    st = qs.from_env()
    if a.gtfs_list:
        from quinzemin import gtfs_feeds
        mun = municipality(st, a.ine)
        areas, _ = load_areas(st, mun)
        bbox = gpd.GeoSeries([areas.union_all()], crs=areas.crs).to_crs(4326).total_bounds.tolist()
        for f in gtfs_feeds.feeds_for(st, bbox, include_inactive=True):
            lic = "NAP/MITRAMS" if gtfs_feeds.is_nap(f) else (f["license_url"] or "sin licencia indicada")
            print(f"  {f['id']:<10} {f['status']:<9} {f['scope']:<9} {f['provider'][:50]:<51} {lic}")
        return
    if a.only_transit:
        meta = update_transit(a.ine, st, refs=a.gtfs_feed, auto=a.gtfs_auto, city_gtfs=a.gtfs)
        for g in meta["gtfs_sources"]:
            print(f"  {g['id']:<10} {g['label'][:50]:<51} vigencia {g['validity'].get('start')}–{g['validity'].get('end')}")
        return
    meta = prepare(a.ine, st, network=not a.no_network, gtfs=a.gtfs, gtfs_feeds=a.gtfs_feed,
                   gtfs_auto=a.gtfs_auto, force=a.force)
    r = meta["report"]
    print(f"\n{meta['name']}: {r['areas']['secciones']} secciones, "
          f"{population.fmt_int(meta['population'])} hab. ({meta['population_year']})")
    for s in meta["services"]:
        print(f"  {s['emoji']} {s['label']:<22}{s['features']:>6} elementos")
    if r.get("educacion"):
        e = r["educacion"]
        print(f"  Educación: {e['proporcion_clasificada']:.0%} de los centros clasificados → "
              f"{'primaria y secundaria separadas' if e['separado'] else 'servicio único'}")


if __name__ == "__main__":
    main()
