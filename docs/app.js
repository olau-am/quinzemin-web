/* quinzemin · mapa web (Leaflet). Datos: data/cities/{ine}/areas.geojson + result.json */
(function () {
  "use strict";

  // Colores de main.py original: RdYlGn en 0,95/0,75/0,55/0,30/0,08 (tramos de puntuación)
  var BAND_COLORS = ["#0c7f43", "#84ca66", "#ecf7a6", "#fdad60", "#cc2627"];
  // YlOrRd en 0,12 + 0,83·i/(n−1), según el nº de clases de prioridad
  var PRIO_COLORS = {
    1: ["#ffeea3"],
    2: ["#ffeea3", "#970026"],
    3: ["#ffeea3", "#fd7c37", "#970026"],
    4: ["#ffeea3", "#feac49", "#f23924", "#970026"],
    5: ["#ffeea3", "#fec15d", "#fd7c37", "#e41c1d", "#970026"]
  };
  var MODE_TEXT = {
    distance: function (p) { return "a &lt;" + p.distance_m + " m en línea recta"; },
    network: function (p) { return "a &lt;" + p.distance_m + " m por la red peatonal"; },
    transit: function (p) { return "en &lt;" + p.time_minutes + " min en transporte público"; }
  };

  var nf0 = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0, useGrouping: "always" });
  var nf1 = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  function fmtInt(n) { return nf0.format(n); }
  function fmtPct(x) { return nf1.format(x) + " %"; }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  var state = { view: "score", data: null, layer: null, emojis: null, prioClass: null };
  var map = L.map("map", { zoomControl: true, preferCanvas: false });

  // Teselas estándar de OSM (la atribución de OSM va en la de la ciudad)
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 }).addTo(map);

  // ------------------------------------------------------------------
  // Carga
  // ------------------------------------------------------------------
  function status(msg, isError) {
    var el = document.getElementById("qm-status");
    el.hidden = !msg;
    el.className = "qm-card" + (isError ? " error" : "");
    el.textContent = msg || "";
  }

  function load(ine) {
    status("Cargando datos…");
    var base = "data/cities/" + ine + "/";
    Promise.all([
      fetch(base + "areas.geojson").then(check),
      fetch(base + "result.json").then(check)
    ]).then(function (r) {
      render(r[0], r[1]);
      status(null);
    }).catch(function (e) {
      console.error(e);
      status("No se han podido cargar los datos (" + e.message + ").", true);
    });
  }

  function check(resp) {
    if (!resp.ok) throw new Error(resp.status + " " + resp.url);
    return resp.json();
  }

  // ------------------------------------------------------------------
  // Clases y colores
  // ------------------------------------------------------------------
  function bandIndex(score, bands) {
    for (var i = 0; i < bands.length; i++) if (score >= bands[i].min) return i;
    return bands.length - 1;
  }

  function prioClassOf(value, breaks) {
    // np.searchsorted(cuts, value, side="right")
    var c = 0;
    while (c < breaks.length && breaks[c] <= value) c++;
    return c;
  }

  function fillFor(area) {
    var st = state.data.stats;
    if (state.view === "prio" && st.priority) {
      var n = st.priority.classes.length;
      return PRIO_COLORS[n][prioClassOf(area.prioridad, st.priority.breaks)];
    }
    return BAND_COLORS[bandIndex(area.puntuacion, st.bands)];
  }

  // ------------------------------------------------------------------
  // Mapa
  // ------------------------------------------------------------------
  function render(geo, data) {
    state.data = data;
    var city = data.city;
    map.attributionControl.addAttribution(city.attribution);
    document.getElementById("qm-subtitle").innerHTML =
      esc(city.name) + " · servicios " + MODE_TEXT[data.params.mode](data.params);

    state.layer = L.geoJSON(geo, {
      style: function (f) { return styleFor(f); },
      onEachFeature: function (f, lyr) {
        lyr.bindTooltip(function () { return tooltipHtml(f.properties.area_id); },
          { sticky: true, className: "qm-tip", direction: "auto" });
        lyr.on("mouseover", function () { lyr.setStyle({ weight: 2, color: "#222" }); lyr.bringToFront(); });
        lyr.on("mouseout", function () { lyr.setStyle({ weight: 0.5, color: "#555" }); });
      }
    }).addTo(map);

    map.fitBounds(state.layer.getBounds(), { padding: [10, 10] });
    buildEmojis(data, city.emoji_min_zoom || 14);
    renderLegend();
  }

  function styleFor(f) {
    var a = state.data.areas[f.properties.area_id];
    return {
      fillColor: a ? fillFor(a) : "#808080",
      color: "#555", weight: 0.5, fillOpacity: 0.65
    };
  }

  function recolor() {
    state.layer.setStyle(styleFor);
  }

  function buildEmojis(data, minZoom) {
    var group = L.layerGroup();
    Object.keys(data.areas).forEach(function (id) {
      var a = data.areas[id];
      var missing = data.services.filter(function (s) { return s.emoji && !a.svc[s.id][0]; })
        .map(function (s) { return s.emoji; });
      if (!missing.length) return;
      var n = missing.length;
      L.marker(a.c, {
        interactive: false, keyboard: false,
        icon: L.divIcon({
          html: '<div class="qm-emoji">' + missing.join("") + "</div>",
          className: "", iconSize: [n * 22, 22], iconAnchor: [n * 11, 11]
        })
      }).addTo(group);
    });
    state.emojis = group;
    function update() {
      if (map.getZoom() >= minZoom) { if (!map.hasLayer(group)) map.addLayer(group); }
      else if (map.hasLayer(group)) map.removeLayer(group);
    }
    map.on("zoomend", update);
    update();
  }

  // ------------------------------------------------------------------
  // Tooltip
  // ------------------------------------------------------------------
  function tooltipHtml(id) {
    var d = state.data, a = d.areas[id], p = d.params;
    if (!a) return "Sin datos";
    var transit = p.mode === "transit";
    var unit = transit ? " min" : " m";
    var html = "<b>Sección " + esc(a.cusec || id) + "</b><br>" +
      '<span class="kv">Puntuación:</span> ' + nf1.format(a.puntuacion) + " / 100";
    if (a.poblacion != null) {
      html += ' · <span class="kv">Habitantes:</span> ' + fmtInt(a.poblacion) +
        '<br><span class="kv">Prioridad (hab. × déficit):</span> ' + fmtInt(a.prioridad);
    }
    html += '<br><span class="kv">Servicios accesibles:</span> ' + a.score + " de " + d.services.length;
    html += "<table>";
    d.services.forEach(function (s) {
      var v = a.svc[s.id], ok = v[0], dist = v[1], name = v[2];
      html += "<tr><td>" + (s.emoji || "•") + "</td><td>" + esc(s.label) + "</td>" +
        '<td class="' + (ok ? "ok" : "ko") + '">' + (ok ? "✓" : "✗") + "</td>" +
        "<td>" + esc(name) + "</td>" +
        '<td class="n">' + (dist == null || dist >= 9999 ? "—" : fmtInt(dist) + unit) + "</td></tr>";
    });
    return html + "</table>";
  }

  // ------------------------------------------------------------------
  // Leyenda
  // ------------------------------------------------------------------
  function row(left, label, right) {
    return '<div class="qm-row">' + left + '<span class="lbl">' + label + '</span><span class="val">' +
      (right || "") + "</span></div>";
  }
  function swatch(color) { return '<span class="qm-sw" style="background:' + color + '"></span>'; }
  function emo(e) { return '<span class="qm-emo">' + e + "</span>"; }

  function renderLegend() {
    var d = state.data, st = d.stats, p = d.params, cov = st.coverage;
    var decay = p.decay > 1;
    var far = p.mode === "transit" ? (p.time_minutes * p.decay) + " min" : (p.distance_m * p.decay) + " m";
    var html = '<div class="qm-title">Puntuación de accesibilidad</div>' +
      '<div class="qm-sub">100 = todos los servicios ' + MODE_TEXT[p.mode](p) +
      (decay ? "<br>crédito parcial hasta " + far : "") + "</div>";

    var body = "";
    if (st.priority) {
      body += '<div class="qm-toggle" role="radiogroup">' +
        ["score", "prio"].map(function (k) {
          return '<label><input type="radio" name="qm-view" value="' + k + '"' +
            (state.view === k ? " checked" : "") + "> " + (k === "score" ? "Puntuación" : "Prioridad") + "</label>";
        }).join("") + "</div>";
    }

    if (state.view === "prio" && st.priority) {
      var cls = st.priority.classes, n = cls.length;
      body += '<div class="qm-h">Hab. × déficit de servicios</div>';
      for (var c = n - 1; c >= 0; c--) {
        var k = cls[c];
        var label = k.max == null ? "≥ " + fmtInt(k.min) : fmtInt(k.min) + " – " + fmtInt(k.max);
        body += row(swatch(PRIO_COLORS[n][c]), label, k.areas + " áreas");
      }
    } else {
      body += '<div class="qm-h">Puntuación' + (cov ? " · % población" : "") + "</div>";
      st.bands.forEach(function (b, i) {
        body += row(swatch(BAND_COLORS[i]), esc(b.label), cov ? fmtPct(b.pct) : "");
      });
    }

    if (cov) {
      var year = st.population.year ? " · INE " + st.population.year : "";
      body += '<hr class="qm-hr"><div class="qm-h">Población con acceso</div>' +
        '<div class="qm-sub">' + fmtInt(st.population.total) + " hab." + year + "</div>" +
        row(emo("✅"), "Todos los servicios", fmtPct(cov.all)) +
        row(emo("⛔"), "Ningún servicio", fmtPct(cov.none));
      if (cov.mean_score != null) body += row(emo("⌀"), "Puntuación media", nf0.format(cov.mean_score) + " / 100");
      body += '<hr class="qm-hr">';
      d.services.forEach(function (s) {
        body += row(emo(s.emoji || "•"), esc(s.label), fmtPct(cov.per_service[s.id] || 0));
      });
    }

    document.getElementById("qm-legend-body").innerHTML =
      html + '<div class="qm-collapsible" style="margin-top:6px">' + body + "</div>";
    document.querySelectorAll("input[name=qm-view]").forEach(function (r) {
      r.addEventListener("change", function () { state.view = r.value; recolor(); renderLegend(); });
    });
  }

  document.getElementById("qm-legend-toggle").addEventListener("click", function () {
    var lg = document.getElementById("qm-legend");
    var collapsed = lg.classList.toggle("collapsed");
    this.textContent = collapsed ? "▸" : "▾";
    this.setAttribute("aria-expanded", String(!collapsed));
  });

  // Plegar la leyenda de entrada en pantallas pequeñas
  if (window.matchMedia("(max-width: 600px)").matches) {
    document.getElementById("qm-legend-toggle").click();
  }

  load("46250");
})();
