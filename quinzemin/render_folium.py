"""
Mapa HTML estático con folium a partir de un Result del motor (para la memoria del TFM).
Mismo aspecto que el docs/index.html que generaba quinzemin/src/main.py.
"""
from __future__ import annotations

import folium
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
from branca.element import Element, MacroElement
from jinja2 import Template

from quinzemin import population
from quinzemin.engine import SCORE_BANDS, Bundle, Result

BAND_POS = [0.95, 0.75, 0.55, 0.30, 0.08]     # posición de cada tramo en RdYlGn

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
_H = '<b style="font-size:11px;color:#666;text-transform:uppercase;letter-spacing:.5px">{}</b>'


def _hex(cmap: str, x: float) -> str:
    return mcolors.to_hex(plt.get_cmap(cmap)(x))


def band_color(i: int) -> str:
    return _hex("RdYlGn", BAND_POS[i])


def count_color(score: int, n: int) -> str:
    return "#808080" if n == 0 else _hex("RdYlGn", score / n)


def prio_color(i: int, n: int) -> str:
    return _hex("YlOrRd", 0.12 + 0.83 * i / max(n - 1, 1))


def _add_script(mapa, js: str) -> None:
    """JS que se ejecuta después de crear el mapa y sus capas (un Element iría antes y fallaría)."""
    el = MacroElement()
    el._template = Template("{% macro script(this, kwargs) %}{% raw %}" + js + "{% endraw %}{% endmacro %}")
    mapa.add_child(el)


def _legend_html(title, subtitle, scales, services, cov, pop_note) -> str:
    keys = list(scales)
    toggle = ""
    if len(keys) > 1:
        labels = {"score": "Puntuación", "prio": "Prioridad"}
        toggle = '<div style="display:flex;gap:10px;margin:6px 0 2px">' + "".join(
            f'<label style="cursor:pointer"><input type="radio" name="qm-view" value="{k}"'
            f'{" checked" if i == 0 else ""} style="vertical-align:-2px"> {labels.get(k, k)}</label>'
            for i, k in enumerate(keys)) + "</div>"
    scale_html = "".join(
        f'<div class="qm-scale" data-view="{k}" style="{"" if i == 0 else "display:none"}">'
        f'{_H.format(head)}<div style="margin-top:4px">'
        + "".join(_SWATCH.format(color=c, label=lb, right=r) for c, lb, r in rows) + "</div></div>"
        for i, (k, (head, rows)) in enumerate(scales.items()))
    if cov:
        summary = (_ROW.format(emoji="✅", label="Todos los servicios", right=population.fmt_pct(cov["all"]))
                   + _ROW.format(emoji="⛔", label="Ningún servicio", right=population.fmt_pct(cov["none"])))
        if "mean_score" in cov:
            summary += _ROW.format(emoji="⌀", label="Puntuación media", right=f'{cov["mean_score"]:.0f} / 100')
        svc_rows = "".join(_ROW.format(emoji=s.emoji or "•", label=s.label,
                                       right=population.fmt_pct(cov["per_service"].get(s.id, 0)))
                           for s in services)
        bottom = (f'{_HR}{_H.format("Población con acceso")}'
                  f'<div style="font-size:11px;color:#666">{pop_note}</div>'
                  f'<div style="margin-top:4px">{summary}</div>{_HR}<div>{svc_rows}</div>')
    else:
        svc_rows = "".join(_ROW.format(emoji=s.emoji, label=s.label, right="") for s in services if s.emoji)
        bottom = f'{_HR}{_H.format("Servicios")}<div style="margin-top:4px">{svc_rows}</div>' if svc_rows else ""
    return ('<div id="qm-legend" style="position:fixed;bottom:30px;right:10px;z-index:1000;background:white;'
            'padding:10px 14px;border-radius:6px;border:1px solid #aaa;font-size:13px;'
            'font-family:sans-serif;box-shadow:2px 2px 6px rgba(0,0,0,.3);line-height:1.4;'
            'max-height:calc(100vh - 90px);overflow-y:auto;min-width:230px">'
            f'<b>{title}</b><div style="font-size:11px;color:#666">{subtitle}</div>'
            f'{toggle}<div style="margin-top:6px">{scale_html}</div>{bottom}</div>')


def render(bundle: Bundle, result: Result, attribution: str = "", emoji_min_zoom: int = 14) -> folium.Map:
    p, st, t = result.params, result.stats, result.table
    svc_ids = st["services"]
    services = [bundle.services[s] for s in svc_ids if s in bundle.services]
    n_svc = len(svc_ids)
    transit, network = p.mode == "transit", p.mode == "network"
    score_view = p.view == "score"
    cov = st.get("coverage")
    has_pop = cov is not None

    areas = bundle.areas[["geometry"]].join(t)
    areas = areas.drop(columns=[c for c in ("area_id",) if c in areas.columns and c in bundle.areas.columns])

    scales = {}
    if score_view:
        lows = [lo for lo, _ in SCORE_BANDS]
        band = np.array([next(i for i, lo in enumerate(lows) if s >= lo) for s in t["puntuacion"]])
        areas["_fill"] = [band_color(i) for i in band]
        rows = [(band_color(i), b["label"].replace("<", "&lt;"),
                 population.fmt_pct(b["pct"]) if has_pop else "")
                for i, b in enumerate(st.get("bands") or [{"label": lb, "pct": 0} for _, lb in SCORE_BANDS])]
        scales["score"] = ("Puntuación" + (" · % población" if has_pop else ""), rows)
        if has_pop:
            pr = st["priority"]
            cuts = np.array(pr["breaks"])
            cls = np.searchsorted(cuts, t["prioridad"].to_numpy(), side="right")
            n_cls = len(pr["classes"])
            areas["_fill_prio"] = [prio_color(c, n_cls) for c in cls]
            prio_rows = []
            for c in reversed(range(n_cls)):
                k = pr["classes"][c]
                label = (f"≥ {population.fmt_int(k['min'])}" if k["max"] is None
                         else f"{population.fmt_int(k['min'])} – {population.fmt_int(k['max'])}")
                prio_rows.append((prio_color(c, n_cls), label, f"{k['areas']} áreas"))
            scales["prio"] = ("Hab. × déficit de servicios", prio_rows)
    else:
        areas["_fill"] = [count_color(min(s, n_svc), n_svc) for s in t["score"]]
        by = {c["score"]: c for c in st.get("counts", [])}
        rows = []
        for s in reversed(range(n_svc + 1)):
            label = "0 — ninguno" if s == 0 else f"{s} — todos" if s == n_svc else str(s)
            rows.append((count_color(s, n_svc), label,
                         population.fmt_pct(by[s]["pct"]) if has_pop and s in by else ""))
        scales["count"] = ("Nº de servicios" + (" · % población" if has_pop else ""), rows)

    # Tooltip
    fields, aliases = ["cusec" if "cusec" in areas.columns else "area_id"], ["Área:"]
    if score_view:
        fields, aliases = fields + ["puntuacion"], aliases + ["Puntuación (0–100):"]
    if has_pop:
        fields, aliases = fields + ["poblacion"], aliases + ["Habitantes:"]
        if score_view:
            fields, aliases = fields + ["prioridad"], aliases + ["Prioridad (hab. × déficit):"]
    fields, aliases = fields + ["score"], aliases + [f"Servicios accesibles (0–{n_svc}):"]
    for s in services:
        thr = p.distance_m
        fields += [f"{s.id}_ok", f"nombre_{s.id}", f"dist_{s.id}_m"]
        aliases += ([f"{s.label} <{p.time_minutes:g}min:", "Más cercano:", "Tiempo (min):"] if transit else
                    [f"{s.label} <{thr:g}m{' (red)' if network else ''}:", "Más cercano:", "Distancia (m):"])

    center = bundle.meta.get("center") or [areas.to_crs(4326).geometry.union_all().centroid.y,
                                           areas.to_crs(4326).geometry.union_all().centroid.x]
    mapa = folium.Map(location=center, zoom_start=bundle.meta.get("zoom") or 12, attr=attribution or None)
    wgs = areas.to_crs(epsg=4326)
    choropleth = folium.GeoJson(
        wgs,
        style_function=lambda f: {"fillColor": f["properties"]["_fill"], "color": "#555",
                                  "weight": 0.5, "fillOpacity": 0.65},
        tooltip=folium.GeoJsonTooltip(fields=fields, aliases=aliases, localize=True),
    ).add_to(mapa)

    # Emojis de servicios que faltan, visibles desde emoji_min_zoom
    cents = bundle.areas.geometry.centroid.to_crs(epsg=4326)
    fg = folium.FeatureGroup(name="Servicios faltantes", show=True)
    for idx in t.index:
        missing = [s.emoji for s in services if s.emoji and not bool(t.at[idx, f"{s.id}_ok"])]
        if missing:
            n = len(missing)
            folium.Marker(location=[cents[idx].y, cents[idx].x], icon=folium.DivIcon(
                html=('<div style="font-size:18px;line-height:1;white-space:nowrap;'
                      f'filter:drop-shadow(0 0 3px rgba(255,255,255,0.9))">{"".join(missing)}</div>'),
                icon_size=(n * 22, 22), icon_anchor=(n * 11, 11), class_name="")).add_to(fg)
    fg.add_to(mapa)
    _add_script(mapa, (
        "(function(){"
        f"var _m={mapa.get_name()},_fg={fg.get_name()},_mz={emoji_min_zoom};"
        "function _u(){if(_m.getZoom()>=_mz){if(!_m.hasLayer(_fg))_m.addLayer(_fg);}"
        "else if(_m.hasLayer(_fg))_m.removeLayer(_fg);}"
        "_m.on('zoomend',_u);_u();})();"))
    if len(scales) > 1:
        _add_script(mapa, (
            "(function(){"
            f"var _gj={choropleth.get_name()},_key={{score:'_fill',prio:'_fill_prio'}};"
            "document.querySelectorAll('input[name=qm-view]').forEach(function(r){"
            "r.addEventListener('change',function(){var k=_key[r.value];"
            "_gj.setStyle(function(f){return {fillColor:f.properties[k]};});"
            "document.querySelectorAll('.qm-scale').forEach(function(s){"
            "s.style.display=s.dataset.view===r.value?'':'none';});});});})();"))

    # Leyenda
    thr_label = (f"en &lt;{p.time_minutes:g} min · transporte público" if transit else
                 f"a &lt;{p.distance_m:g} m por la red peatonal" if network else f"a &lt;{p.distance_m:g} m")
    decay = p.effective_decay
    if score_view:
        title = "Puntuación de accesibilidad"
        subtitle = f"100 = todos los servicios {thr_label}"
        if decay > 1:
            far = f"{p.time_minutes * decay:g} min" if transit else f"{p.distance_m * decay:g} m"
            subtitle += f"<br/>crédito parcial hasta {far}"
    else:
        title, subtitle = f"Servicios {thr_label}", ""
    year = (st.get("population") or {}).get("year")
    pop_note = (f"{population.fmt_int(cov['total'])} hab." + (f" · INE {year}" if year else "")) if cov else ""
    mapa.get_root().html.add_child(Element(_legend_html(title, subtitle, scales, services, cov, pop_note)))
    return mapa
