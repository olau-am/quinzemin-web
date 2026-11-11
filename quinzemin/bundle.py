"""
Construcción de un Bundle (datos de una ciudad listos para engine.compute).

De momento solo desde el formato del proyecto original: un YAML como config/cities/46250.yaml
y una carpeta con lo que deja download_data.py (areas.geojson, {servicio}.geojson,
population.csv, gtfs_{id}.zip, walk_graph.graphml). El formato de bundle de la web
(areas.parquet, services/*.parquet, walk_graph.npz, transit.pkl) llega con prepare.py (fase 2).
"""
from __future__ import annotations

import os
from pathlib import Path

import geopandas as gpd

from quinzemin import population
from quinzemin.engine import Bundle, Params, Service, ServiceParams


def load_legacy(cfg: dict, data_dir: str | Path, network: bool = True, transit: bool = True,
                npz_cache: bool = True) -> Bundle:
    """
    network / transit: cargar la red peatonal y el grafo GTFS si existen los ficheros.
    npz_cache: guardar la red como walk_graph.npz junto al GraphML y reutilizarla (sin osmnx).
    """
    data_dir = Path(data_dir)
    city = cfg["city"]

    areas = gpd.read_file(data_dir / "areas.geojson")
    utm = areas.estimate_utm_crs()
    areas = areas.to_crs(utm)

    pop_cfg = city.get("population") or {}
    pop = population.assign(cfg, areas, csv_path=str(data_dir / "population.csv"))
    meta = {"name": city.get("name"), "center": city.get("center"), "zoom": city.get("zoom"),
            "population_year": pop.attrs.get("year") if pop is not None else None}
    if pop is not None:
        areas["poblacion"] = pop
    if pop_cfg.get("area_code"):
        areas["cusec"] = population._area_codes(areas, pop_cfg["area_code"])
    areas["area_id"] = areas.index.astype(str)

    services = {}
    for s in cfg["services"]:
        path = data_dir / f"{s['id']}.geojson"
        if not path.exists():
            continue
        gdf = gpd.read_file(path).to_crs(utm)
        if s.get("filter"):
            f = s["filter"]
            gdf = gdf[gdf[f["field"]].str.contains(f["contains"], case=False, na=False)]
        gdf = gdf.reset_index(drop=True)
        name_col = s.get("name_field", "name")
        gdf = gdf.assign(name=gdf[name_col] if name_col in gdf.columns else None)[["name", "geometry"]]
        services[s["id"]] = Service(id=s["id"], label=s.get("label", s["id"]), features=gdf,
                                    emoji=s.get("emoji", ""), polygon_access=s.get("access") == "polygon")

    net = None
    graph = data_dir / "walk_graph.graphml"
    npz = data_dir / "walk_graph.npz"
    if network and (graph.exists() or npz.exists()):
        from quinzemin.network import WalkNetwork
        if npz_cache and npz.exists() and (not graph.exists() or npz.stat().st_mtime >= graph.stat().st_mtime):
            net = WalkNetwork.from_npz(npz)
        else:
            net = WalkNetwork.load(str(graph), utm)
            if npz_cache:
                net.to_npz(npz)

    tg = None
    a = cfg.get("analysis", {})
    sources = [{"id": g["id"], "label": g.get("label", g["id"]), "path": str(data_dir / f"gtfs_{g['id']}.zip")}
               for g in a.get("gtfs_sources", []) if os.path.exists(data_dir / f"gtfs_{g['id']}.zip")]
    if transit and sources:
        from quinzemin.gtfs import TransitGraph
        tg = TransitGraph.load(sources, utm)

    return Bundle(areas=areas, services=services, network=net, transit=tg, meta=meta)


def params_from_config(cfg: dict) -> Params:
    """Params equivalentes a un config.yaml del proyecto original."""
    a = cfg.get("analysis", {})
    net = a.get("network", {})
    return Params(
        mode=a.get("mode", "distance"),
        view=cfg.get("map", {}).get("view", "count"),
        distance_m=a.get("distance_m", 1000),
        time_minutes=a.get("time_minutes", 15),
        decay=float(a.get("scoring", {}).get("decay", 2.0)),
        walking_speed_mpm=a.get("walking_speed_mpm", 80),
        max_walk_to_stop_m=a.get("max_walk_to_stop_m", 500),
        max_snap_m=net.get("max_snap_m", 500),
        park_access_m=net.get("park_access_m", 20),
        services=[ServiceParams(s["id"], float(s.get("weight", 1)), s.get("distance_m"))
                  for s in cfg["services"]],
    )
