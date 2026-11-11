import os
import webbrowser

import numpy as np
import pandas as pd

import folium
from branca.element import Element, MacroElement
from jinja2 import Template
import geopandas as gpd
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt

import config
import population

# Tramos de la vista "score": (límite inferior, etiqueta, posición en RdYlGn), de mejor a peor.
# No son equidistantes: en una ciudad compacta casi todas las áreas superan 80, y lo que
# interesa distinguir es quién llega a todo (100) y cuánto le falta al resto.
SCORE_BANDS = [
    (100, "100 · todos",  0.95),
    (90,  "90 – 100",     0.75),
    (75,  "75 – 90",      0.55),
    (50,  "50 – 75",      0.30),
    (0,   "&lt; 50",      0.08),
]
N_PRIO_CLASSES = 5


# ----------------------------------------------------------------------
# Colores
# ----------------------------------------------------------------------

def _score_color(score: int, max_score: int) -> str:
    """Mapea un score 0..max_score a un color hex del gradiente RdYlGn."""
    if max_score == 0:
        return "#808080"
    cmap = plt.get_cmap("RdYlGn")
    return mcolors.to_hex(cmap(score / max_score))


def _band_index(score: float) -> int:
    """Puntuación 0–100 → índice en SCORE_BANDS (0 = mejor tramo)."""
    return next(i for i, (lo, _, _) in enumerate(SCORE_BANDS) if score >= lo)


def _band_color(i: int) -> str:
    return mcolors.to_hex(plt.get_cmap("RdYlGn")(SCORE_BANDS[i][2]))


def _prio_breaks(values: pd.Series) -> np.ndarray:
    """Cortes por cuantiles (N_PRIO_CLASSES clases) de la prioridad, sin repetir valores."""
    v = values[values > 0]
    if v.empty:
        return np.array([0.0])
    qs = np.quantile(v, np.linspace(0, 1, N_PRIO_CLASSES + 1)[1:-1])
    return np.unique(np.round(qs))


def _prio_color(i: int, n: int) -> str:
    return mcolors.to_hex(plt.get_cmap("YlOrRd")(0.12 + 0.83 * i / max(n - 1, 1)))


# ----------------------------------------------------------------------
# Leyenda
# ----------------------------------------------------------------------

_SWATCH = ('<div style="display:flex;align-items:center;margin:3px 0">'
           '<div style="width:18px;height:18px;background:{color};border:1px solid #666;'
           'margin-right:7px;flex-shrink:0;border-radius:2px"></div>'
           '<span style="flex:1">{label}</span>'
           '<span style="color:#555;margin-left:10px;font-variant-numeric:tabular-nums">{right}</span>'
           '</div>')
_ROW = ('<div style="display:flex;align-items:center;margin:3px 0">'
        '<span style="font-size:15px;width:22px;text-align:center;margin-right:5px">{emoji}</span>'
        '<span style="flex:1">{label}</span>'
        '<span style="color:#555;margin-left:10px;font-variant-numeric:tabular-nums">{right}</span>'
        '</div>')
_HR = '<hr style="border:none;border-top:1px solid #ddd;margin:8px 0"/>'
_H = ('<b style="font-size:11px;color:#666;text-transform:uppercase;letter-spacing:.5px">'
      '{}</b>')


def _swatches(rows) -> str:
    return "".join(_SWATCH.format(color=c, label=l, right=r) for c, l, r in rows)


def _legend_html(title: str, subtitle: str, scales: dict, services: list,
                 cov: dict | None, pop_note: str) -> str:
    """
    scales  → {"clave": (encabezado, [(color, etiqueta, texto_derecha), ...])}.
              Con más de una escala se muestra un selector que cambia el coloreado del mapa.
    cov     → resultado de population.coverage() o None si no hay población.
    """
    keys = list(scales)
    toggle = ""
    if len(keys) > 1:
        labels = {"score": "Puntuación", "prio": "Prioridad"}
        toggle = '<div style="display:flex;gap:10px;margin:6px 0 2px">' + "".join(
            f'<label style="cursor:pointer"><input type="radio" name="qm-view" value="{k}"'
            f'{" checked" if i == 0 else ""} style="vertical-align:-2px"> {labels.get(k, k)}</label>'
            for i, k in enumerate(keys)
        ) + "</div>"

    scale_html = "".join(
        f'<div class="qm-scale" data-view="{k}" style="{"" if i == 0 else "display:none"}">'
        f'{_H.format(head)}<div style="margin-top:4px">{_swatches(rows)}</div></div>'
        for i, (k, (head, rows)) in enumerate(scales.items())
    )

    if cov:
        summary = (
            _ROW.format(emoji="✅", label="Todos los servicios", right=population.fmt_pct(cov["all"]))
            + _ROW.format(emoji="⛔", label="Ningún servicio", right=population.fmt_pct(cov["none"]))
        )
        if "mean_score" in cov:
            summary += _ROW.format(emoji="⌀", label="Puntuación media",
                                   right=f'{cov["mean_score"]:.0f} / 100')
        svc_rows = "".join(
            _ROW.format(emoji=s.get("emoji", "•"), label=s["label"],
                        right=population.fmt_pct(cov["per_service"].get(s["id"], 0)))
            for s in services
        )
        bottom = (
            f'{_HR}{_H.format("Población con acceso")}'
            f'<div style="font-size:11px;color:#666">{pop_note}</div>'
            f'<div style="margin-top:4px">{summary}</div>{_HR}'
            f'<div>{svc_rows}</div>'
        )
    else:
        svc_rows = "".join(_ROW.format(emoji=s["emoji"], label=s["label"], right="")
                           for s in services if s.get("emoji"))
        bottom = f'{_HR}{_H.format("Servicios")}<div style="margin-top:4px">{svc_rows}</div>' \
            if svc_rows else ""

    return (
        '<div id="qm-legend" style="position:fixed;bottom:30px;right:10px;z-index:1000;background:white;'
        'padding:10px 14px;border-radius:6px;border:1px solid #aaa;font-size:13px;'
        'font-family:sans-serif;box-shadow:2px 2px 6px rgba(0,0,0,.3);line-height:1.4;'
        'max-height:calc(100vh - 90px);overflow-y:auto;min-width:230px">'
        f'<b>{title}</b><div style="font-size:11px;color:#666">{subtitle}</div>'
        f'{toggle}<div style="margin-top:6px">{scale_html}</div>'
        f'{bottom}'
        '</div>'
    )


def _add_script(mapa, js: str) -> None:
    """
    Añade JS que se ejecuta después de crear el mapa y sus capas. Un Element en
    get_root().html o .script se emite antes del código de Leaflet y falla
    (las variables del mapa aún no existen).
    """
    el = MacroElement()
    el._template = Template("{% macro script(this, kwargs) %}{% raw %}" + js
                            + "{% endraw %}{% endmacro %}")
    mapa.add_child(el)


# ----------------------------------------------------------------------
# Cálculo
# ----------------------------------------------------------------------

def _nearest(centroid, gdf, name_col: str) -> tuple[int, str]:
    """Devuelve (distancia_m, nombre) del elemento más cercano al centroide."""
    distances = gdf.geometry.distance(centroid)
    idx = distances.idxmin()
    dist = round(float(distances[idx]))
    name = gdf.at[idx, name_col] if name_col in gdf.columns else ""
    return dist, str(name) if name else "—"


def _credit(raw, threshold: float, decay: float) -> np.ndarray:
    """
    Crédito 0–1 de un servicio para cada área: 1 hasta el umbral y caída lineal
    hasta 0 en umbral × decay (decay ≤ 1 → criterio binario dentro/fuera).
    `raw` y `threshold` en las mismas unidades (metros o segundos).
    """
    raw = np.asarray(raw, dtype=float)
    if decay <= 1:
        return (raw <= threshold).astype(float)
    return np.clip((decay * threshold - raw) / ((decay - 1) * threshold), 0.0, 1.0)


def main() -> None:
    cfg      = config.load()
    city     = cfg["city"]
    services = cfg["services"]
    analysis   = cfg.get("analysis", {})
    dist_m     = analysis.get("distance_m", 1000)
    mode       = analysis.get("mode", "distance")
    transit    = mode == "transit"
    network    = mode == "network"
    net_cfg    = analysis.get("network", {})
    time_min   = analysis.get("time_minutes", 15)
    budget_s   = time_min * 60
    walk_mps   = analysis.get("walking_speed_mpm", 80) / 60
    max_walk_m = analysis.get("max_walk_to_stop_m", 500)

    map_cfg    = cfg.get("map", {})
    view       = map_cfg.get("view", "count")
    score_view = view == "score"
    decay      = float(analysis.get("scoring", {}).get("decay", 2.0)) if score_view else 1.0

    lat, lon = city["center"]
    mapa = folium.Map(location=[lat, lon], zoom_start=city["zoom"], attr=config.build_attr(cfg))

    # --- Cargar áreas de análisis ---
    areas_path = "data/areas.geojson"
    if not os.path.exists(areas_path):
        print(f"Archivo no encontrado: {areas_path}. Ejecuta download_data.py primero.")
        return

    areas = gpd.read_file(areas_path)
    # CRS proyectado óptimo para la ciudad (metros reales, sin hardcodear EPSG)
    utm = areas.estimate_utm_crs()
    areas = areas.to_crs(utm)
    centroids = areas.geometry.centroid

    # --- Población por área (opcional; necesaria para los % de población) ---
    pop = population.assign(cfg, areas)
    has_pop = pop is not None and pop.sum() > 0
    if has_pop:
        areas["poblacion"] = pop.round().astype(int)
        print(f"Población asignada: {population.fmt_int(pop.sum())} hab. en {len(areas)} áreas")

    areas["score"] = 0

    # --- Cargar grafo GTFS si mode=transit ---
    graph     = None
    reachable = None
    # En la vista score con decaimiento, se explora hasta budget × decay para dar crédito parcial
    reach_s   = budget_s * max(decay, 1.0)
    if transit:
        from gtfs import TransitGraph
        sources = [
            {"id": s["id"], "label": s.get("label", s["id"]),
             "path": f"data/gtfs_{s['id']}.zip"}
            for s in analysis.get("gtfs_sources", [])
            if os.path.exists(f"data/gtfs_{s['id']}.zip")
        ]
        if sources:
            graph = TransitGraph.load(sources, utm)
            print(f"Calculando alcance por transporte público ({len(areas)} centroides)...")
            reachable = {idx: graph.reachable_from(c, reach_s, walk_mps, max_walk_m)
                         for idx, c in centroids.items()}
            print("  Listo.")
        else:
            print("⚠ No hay datos GTFS disponibles — usando modo distancia")
            transit = False

    # --- Cargar red peatonal si mode=network ---
    net = None
    snap_rows = []
    if network:
        graph_path = net_cfg.get("graph_path", "data/walk_graph.graphml")
        if os.path.exists(graph_path):
            from network import WalkNetwork
            net = WalkNetwork.load(graph_path, utm)
            orig_node, orig_snap = net.snap(centroids)
            areas["snap_m"] = orig_snap.round(1)
            snap_rows.append(("origen (centroides)", orig_snap))
        else:
            print(f"⚠ No existe {graph_path} — ejecuta download_data.py. Usando modo distancia")
            network = False

    id_field        = city["areas"]["id_field"]
    n_services      = len(services)
    tooltip_fields  = [id_field]
    tooltip_aliases = ["Área:"]
    if score_view:
        tooltip_fields  += ["puntuacion"]
        tooltip_aliases += ["Puntuación (0–100):"]
    if has_pop:
        tooltip_fields  += ["poblacion"]
        tooltip_aliases += ["Habitantes:"]
        if score_view:
            tooltip_fields  += ["prioridad"]
            tooltip_aliases += ["Prioridad (hab. × déficit):"]
    tooltip_fields  += ["score"]
    tooltip_aliases += [f"Servicios accesibles (0–{n_services}):"]

    weighted = np.zeros(len(areas))
    weights  = 0.0

    # --- Procesar cada servicio ---
    for svc in services:
        svc_id     = svc["id"]
        label      = svc["label"]
        name_col   = svc.get("name_field", "name")
        threshold  = svc.get("distance_m", dist_m)   # distancia global o por servicio
        svc_filter = svc.get("filter")
        weight     = float(svc.get("weight", 1))

        dist_col   = f"dist_{svc_id}_m"
        nombre_col = f"nombre_{svc_id}"
        ok_col     = f"{svc_id}_ok"
        path       = f"data/{svc_id}.geojson"

        # raw/thr: medida del servicio más cercano y umbral, en las mismas unidades
        # (metros en distance/network, segundos en transit) → base del crédito de puntuación
        raw, thr = None, None

        if os.path.exists(path):
            gdf = gpd.read_file(path).to_crs(utm)

            if svc_filter:
                mask = gdf[svc_filter["field"]].str.contains(
                    svc_filter["contains"], case=False, na=False
                )
                gdf = gdf[mask].reset_index(drop=True)

            if network:
                feat, dnode, doff = net.destinations(
                    gdf, polygon_access=svc.get("access") == "polygon",
                    access_m=net_cfg.get("park_access_m", 20),
                    max_snap_m=net_cfg.get("max_snap_m", 500),
                )
                snap_rows.append((svc_id, doff))
                node_dist, node_feat = net.nearest_service(feat, dnode, doff)
                d     = node_dist[orig_node] + orig_snap
                fidx  = node_feat[orig_node]
                names = gdf[name_col].astype(str).to_numpy() if name_col in gdf.columns                         else np.full(len(gdf), "—")
                areas[dist_col]   = np.where(np.isfinite(d), np.round(d), 99999).astype(int)
                areas[nombre_col] = [names[i] if i >= 0 else "—" for i in fidx]
                raw, thr = d, threshold
                areas[ok_col]     = areas[dist_col] <= threshold
                # Índice de rodeo: distancia por la red / distancia en línea recta al mismo servicio
                euclid = np.array([
                    gdf.geometry.iloc[i].distance(c) if i >= 0 else np.nan
                    for i, c in zip(fidx, centroids)
                ])
                with np.errstate(divide="ignore", invalid="ignore"):
                    areas[f"rodeo_{svc_id}"] = np.round(np.where(euclid > 1, d / euclid, np.nan), 2)
                n = int(areas[ok_col].sum())
                print(f"{label}: {n}/{len(areas)} áreas con acceso <{threshold}m por la red "
                      f"({len(gdf) - net.last_dropped} elementos en la red, {net.last_dropped} descartados "
                      f"por estar a >{net_cfg.get('max_snap_m', 500)} m)")
            elif transit:
                secs, names = [], []
                for idx in areas.index:
                    t, name = graph.service_min_time(
                        reachable[idx], gdf, name_col, walk_mps, reach_s, max_walk_m
                    )
                    secs.append(t if t is not None else np.inf)
                    names.append(name)
                secs = np.array(secs, dtype=float)
                areas[dist_col]  = np.where(np.isfinite(secs), np.round(secs / 60), 9999).astype(int)
                areas[nombre_col]= names
                raw, thr = secs, budget_s
                areas[ok_col]    = secs <= budget_s
                n = int(areas[ok_col].sum())
                print(f"{label}: {n}/{len(areas)} áreas con acceso <{time_min}min")
            else:
                results          = centroids.apply(lambda c, g=gdf, nc=name_col: _nearest(c, g, nc))
                areas[dist_col]  = results.apply(lambda r: r[0])
                areas[nombre_col]= results.apply(lambda r: r[1])
                raw, thr = areas[dist_col].to_numpy(dtype=float), threshold
                areas[ok_col]    = areas[dist_col] <= threshold
                n = int(areas[ok_col].sum())
                print(f"{label}: {n}/{len(areas)} áreas con acceso <{threshold}m ({len(gdf)} elementos)")
            areas["score"] += areas[ok_col].astype(int)
        else:
            areas[dist_col]  = None
            areas[nombre_col]= "—"
            areas[ok_col]    = False
            print(f"Archivo no encontrado: {path}")

        # Puntuación: el servicio cuenta en el denominador aunque falten sus datos (crédito 0)
        credit = _credit(raw, thr, decay) if raw is not None else np.zeros(len(areas))
        areas[f"cred_{svc_id}"] = np.round(credit, 3)
        weighted += weight * credit
        weights  += weight

        tooltip_fields += [ok_col, nombre_col, dist_col]
        if transit:
            tooltip_aliases += [
                f"{label} <{time_min}min:",
                "Más cercano:",
                "Tiempo (min):",
            ]
        else:
            tooltip_aliases += [
                f"{label} <{threshold}m{' (red)' if network else ''}:",
                "Más cercano:",
                "Distancia (m):",
            ]

    # --- Puntuación y prioridad ---
    areas["puntuacion"] = np.round(100 * weighted / weights, 1) if weights else 0.0
    if has_pop:
        areas["prioridad"] = np.round(pop * (100 - areas["puntuacion"]) / 100).astype(int)

    # --- Diagnóstico de snapping (modo network) ---
    if network and snap_rows:
        warn_m = net_cfg.get("snap_warn_m", 150)
        print("\nDistancias de enganche a la red (m):")
        print(f"  {'conjunto':<22}{'n':>6}{'p50':>8}{'p90':>8}{'p99':>8}{'max':>8}{'>'+str(warn_m):>7}")
        report = []
        for name, arr in snap_rows:
            arr = np.asarray(arr, dtype=float)
            if not len(arr):
                continue
            p50, p90, p99 = np.percentile(arr, [50, 90, 99])
            n_warn = int((arr > warn_m).sum())
            print(f"  {name:<22}{len(arr):>6}{p50:>8.1f}{p90:>8.1f}{p99:>8.1f}{arr.max():>8.1f}{n_warn:>7}")
            report.append({"conjunto": name, "n": len(arr), "p50": p50, "p90": p90,
                           "p99": p99, "max": arr.max(), f"n_mayor_{warn_m}m": n_warn})
        os.makedirs("data", exist_ok=True)
        pd.DataFrame(report).round(1).to_csv("data/snap_report.csv", index=False)
        print("  → data/snap_report.csv")

    # --- Cobertura de población ---
    cov = None
    if has_pop:
        ok_by_svc = {s["id"]: areas[f"{s['id']}_ok"] for s in services}
        cov = population.coverage(pop, ok_by_svc, areas["puntuacion"] if score_view else None)
        print(f"\nPoblación con acceso ({population.fmt_int(cov['total'])} hab.):")
        for s in services:
            print(f"  {s['label']:<24}{population.fmt_pct(cov['per_service'][s['id']]):>9}")
        print(f"  {'Todos los servicios':<24}{population.fmt_pct(cov['all']):>9}")
        print(f"  {'Ningún servicio':<24}{population.fmt_pct(cov['none']):>9}")
        if "mean_score" in cov:
            print(f"  {'Puntuación media':<24}{cov['mean_score']:>7.1f}/100".replace(".", ","))
        rows = [{"servicio": s["id"], "etiqueta": s["label"],
                 "pct_poblacion": round(cov["per_service"][s["id"]], 2)} for s in services]
        rows += [{"servicio": "_todos", "etiqueta": "Todos los servicios", "pct_poblacion": round(cov["all"], 2)},
                 {"servicio": "_ninguno", "etiqueta": "Ningún servicio", "pct_poblacion": round(cov["none"], 2)}]
        os.makedirs("data", exist_ok=True)
        pd.DataFrame(rows).to_csv("data/cobertura_poblacion.csv", index=False)
        print("  → data/cobertura_poblacion.csv")

    # --- Colores por área y escalas de la leyenda ---
    pop_share = (pop / pop.sum() * 100) if has_pop else None

    def share(mask) -> str:
        return population.fmt_pct(float(pop_share[mask].sum())) if has_pop else ""

    scales = {}
    if score_view:
        band = areas["puntuacion"].apply(_band_index)
        areas["_fill"] = band.map(_band_color)
        rows = [(_band_color(i), label, share(band == i))
                for i, (_, label, _) in enumerate(SCORE_BANDS)]
        scales["score"] = ("Puntuación" + (" · % población" if has_pop else ""), rows)
        if has_pop:
            cuts = _prio_breaks(areas["prioridad"])
            cls  = np.searchsorted(cuts, areas["prioridad"].to_numpy(), side="right")
            n_cls = len(cuts) + 1
            areas["_fill_prio"] = [_prio_color(c, n_cls) for c in cls]
            edges = [0, *cuts.tolist()]
            prio_rows = []
            for c in reversed(range(n_cls)):
                lo = edges[c]
                label = (f"≥ {population.fmt_int(lo)}" if c == n_cls - 1
                         else f"{population.fmt_int(lo)} – {population.fmt_int(edges[c + 1])}")
                n_areas = int((cls == c).sum())
                prio_rows.append((_prio_color(c, n_cls), label, f"{n_areas} áreas"))
            scales["prio"] = ("Hab. × déficit de servicios", prio_rows)
    else:
        color_cache = {s: _score_color(s, n_services) for s in range(n_services + 1)}
        areas["_fill"] = areas["score"].clip(0, n_services).map(color_cache)
        rows = []
        for s in reversed(range(n_services + 1)):
            label = "0 — ninguno" if s == 0 else f"{s} — todos" if s == n_services else str(s)
            rows.append((color_cache[s], label, share(areas["score"] == s)))
        scales["count"] = ("Nº de servicios" + (" · % población" if has_pop else ""), rows)

    # --- Renderizar mapa ---
    areas_wgs84       = areas.to_crs(epsg=4326)
    centroids_wgs84   = centroids.to_crs(epsg=4326)

    choropleth = folium.GeoJson(
        areas_wgs84,
        style_function=lambda f: {
            "fillColor": f["properties"]["_fill"],
            "color": "#555",
            "weight": 0.5,
            "fillOpacity": 0.65,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=tooltip_fields,
            aliases=tooltip_aliases,
            localize=True,
        ),
    ).add_to(mapa)

    map_var = mapa.get_name()

    # --- Emojis de servicios faltantes (visibles desde emoji_min_zoom) ---
    emoji_min_zoom = map_cfg.get("emoji_min_zoom", 14)
    svcs_with_emoji = [s for s in services if s.get("emoji")]

    if svcs_with_emoji:
        # show=True: la capa se añade al mapa; el JS la oculta si zoom < min_zoom
        emoji_fg = folium.FeatureGroup(name="Servicios faltantes", show=True)
        ok_cols  = {s["id"]: f"{s['id']}_ok" for s in svcs_with_emoji}
        n_emoji  = 0

        for idx, row in areas_wgs84.iterrows():
            missing = []
            for s in svcs_with_emoji:
                col = ok_cols[s["id"]]
                if col in areas_wgs84.columns and not bool(row[col]):
                    missing.append(s["emoji"])
            if not missing:
                continue

            c    = centroids_wgs84[idx]
            n    = len(missing)
            text = "".join(missing)
            folium.Marker(
                location=[c.y, c.x],
                icon=folium.DivIcon(
                    html=(
                        f'<div style="font-size:18px;line-height:1;white-space:nowrap;'
                        f'filter:drop-shadow(0 0 3px rgba(255,255,255,0.9))">{text}</div>'
                    ),
                    icon_size=(n * 22, 22),
                    icon_anchor=(n * 11, 11),
                    class_name="",   # elimina el wrapper extra de Folium
                ),
            ).add_to(emoji_fg)
            n_emoji += 1

        emoji_fg.add_to(mapa)
        print(f"Emoji markers: {n_emoji} áreas con servicios faltantes")

        fg_var  = emoji_fg.get_name()
        _add_script(mapa, (
            "(function(){"
            f"var _m={map_var},_fg={fg_var},_mz={emoji_min_zoom};"
            "function _upd(){"
            "if(_m.getZoom()>=_mz){if(!_m.hasLayer(_fg))_m.addLayer(_fg);}"
            "else{if(_m.hasLayer(_fg))_m.removeLayer(_fg);}"
            "}"
            "_m.on('zoomend',_upd);"
            "_upd();"
            "})();"
        ))

    # Selector Puntuación / Prioridad: recolorea la capa y cambia la escala de la leyenda
    if len(scales) > 1:
        gj_var = choropleth.get_name()
        _add_script(mapa, (
            "(function(){"
            f"var _gj={gj_var},_key={{score:'_fill',prio:'_fill_prio'}};"
            "document.querySelectorAll('input[name=qm-view]').forEach(function(r){"
            "r.addEventListener('change',function(){"
            "var k=_key[r.value];"
            "_gj.setStyle(function(f){return {fillColor:f.properties[k]};});"
            "document.querySelectorAll('.qm-scale').forEach(function(s){"
            "s.style.display=s.dataset.view===r.value?'':'none';});"
            "});});"
            "})();"
        ))

    output_dir  = "docs"
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, "index.html")
    threshold_label = (
        f"en &lt;{time_min} min · transporte público"
        if transit else
        f"a &lt;{dist_m} m por la red peatonal"
        if network else
        f"a &lt;{dist_m} m"
    )
    if score_view:
        title = "Puntuación de accesibilidad"
        if decay > 1:
            far = f"{time_min * decay:g} min" if transit else f"{dist_m * decay:g} m"
            subtitle = f"100 = todos los servicios {threshold_label}<br/>crédito parcial hasta {far}"
        else:
            subtitle = f"100 = todos los servicios {threshold_label}"
    else:
        title, subtitle = f"Servicios {threshold_label}", ""
    year = pop.attrs.get("year") if has_pop else None
    pop_note = (f"{population.fmt_int(cov['total'])} hab." + (f" · INE {year}" if year else "")
                if cov else "")
    mapa.get_root().html.add_child(Element(
        _legend_html(title, subtitle, scales, services, cov, pop_note)
    ))
    mapa.get_root().html.add_child(Element(
        '<a href="configurar.html" style="position:fixed;top:12px;right:12px;z-index:1000;'
        'background:white;border:1px solid #aaa;border-radius:6px;padding:7px 12px;'
        'font-size:12px;font-family:sans-serif;text-decoration:none;color:#1e40af;'
        'box-shadow:2px 2px 6px rgba(0,0,0,.2);font-weight:600">'
        '⚙ Generador de configuración</a>'
    ))

    mapa.save(output_file)
    print(f"\nMapa guardado en {output_file}")
    if not os.environ.get("QUINZEMIN_NO_BROWSER"):
        webbrowser.open(output_file)


if __name__ == "__main__":
    main()
