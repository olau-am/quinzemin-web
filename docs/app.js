/* quinzemin · aplicación web (Leaflet, sin compilación).
 *
 * Arranque instantáneo con València precalculada (data/cities/46250/). El resto va contra la API
 * (Cloud Run): buscar municipios, recalcular, preparar municipios nuevos y añadir transporte público.
 * Estado compartible en la URL:  #ine=46250&mode=network&view=score&d=800&t=15&k=2&s=salud:1,…&run=<hash>
 * Para desarrollo local:  http://localhost:8000/?api=http://localhost:8080
 */
(function () {
  "use strict";

  var DEFAULT_API = "https://quinzemin-api-358341041742.europe-southwest1.run.app";
  var API = (new URLSearchParams(location.search).get("api") || DEFAULT_API).replace(/\/$/, "");
  var STATIC_INE = "46250";
  var STATIC_BASE = "data/cities/" + STATIC_INE + "/";
  var MAX_FEEDS = 5;

  // ------------------------------------------------------------------
  // Colores (los mismos que el mapa folium del proyecto original)
  // ------------------------------------------------------------------
  var BAND_COLORS = ["#0c7f43", "#84ca66", "#ecf7a6", "#fdad60", "#cc2627"];   // RdYlGn 0,95…0,08
  var PRIO_COLORS = {                                                          // YlOrRd 0,12 + 0,83·i/(n−1)
    1: ["#ffeea3"],
    2: ["#ffeea3", "#970026"],
    3: ["#ffeea3", "#fd7c37", "#970026"],
    4: ["#ffeea3", "#feac49", "#f23924", "#970026"],
    5: ["#ffeea3", "#fec15d", "#fd7c37", "#e41c1d", "#970026"]
  };
  var RDYLGN = ["#a50026", "#d73027", "#f46d43", "#fdae61", "#fee08b", "#ffffbf",
                "#d9ef8b", "#a6d96a", "#66bd63", "#1a9850", "#006837"];

  function rdylgn(x) {                       // interpolación lineal como matplotlib
    x = Math.max(0, Math.min(1, x)) * (RDYLGN.length - 1);
    var i = Math.min(Math.floor(x), RDYLGN.length - 2), f = x - i;
    var a = hex(RDYLGN[i]), b = hex(RDYLGN[i + 1]);
    return "#" + [0, 1, 2].map(function (k) {
      return ("0" + Math.round(a[k] + (b[k] - a[k]) * f).toString(16)).slice(-2);
    }).join("");
  }
  function hex(c) { return [1, 3, 5].map(function (i) { return parseInt(c.substr(i, 2), 16); }); }

  // ------------------------------------------------------------------
  // Utilidades
  // ------------------------------------------------------------------
  var nf0 = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0, useGrouping: "always" });
  var nf1 = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  var nf2 = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 2 });
  function fmtInt(n) { return nf0.format(n); }
  function fmtPct(x) { return nf1.format(x) + " %"; }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function $(id) { return document.getElementById(id); }
  function show(el, on) { (typeof el === "string" ? $(el) : el).hidden = !on; }

  function ApiError(status, detail, retryAfter) {
    this.status = status; this.detail = detail; this.retryAfter = retryAfter;
    this.message = detail;
  }

  function api(path, opts) {
    opts = opts || {};
    var init = { method: opts.method || "GET", headers: {} };
    if (opts.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    return fetch(API + path, init).catch(function () {
      throw new ApiError(0, "No se puede conectar con el servidor. Inténtalo de nuevo en un momento.");
    }).then(function (r) {
      return r.text().then(function (txt) {
        var data = null;
        try { data = txt ? JSON.parse(txt) : null; } catch (e) { data = null; }
        if (!r.ok) {
          var d = data && data.detail;
          if (Array.isArray(d)) d = d.map(function (x) { return x.msg; }).join("; ");
          var err = new ApiError(r.status, d || ("Error " + r.status), r.headers.get("Retry-After"));
          err.data = data;
          throw err;
        }
        return data;
      });
    });
  }

  function getJSON(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new ApiError(r.status, "No se ha podido cargar " + url);
      return r.json();
    });
  }

  function status(msg, kind) {
    var el = $("qm-status");
    el.hidden = !msg;
    el.className = kind || "";
    el.textContent = msg || "";
  }

  function errorText(e) {
    if (e && e.status === 429) {
      var s = parseInt(e.retryAfter, 10);
      var when = !s ? "" : s < 90 ? " Prueba de nuevo en " + s + " s." : " Prueba de nuevo en " + Math.ceil(s / 60) + " min.";
      return (e.detail || "Demasiadas peticiones.") + when;
    }
    return (e && e.message) || String(e);
  }

  // ------------------------------------------------------------------
  // Estado
  // ------------------------------------------------------------------
  var state = {
    city: null,          // metadatos de la ciudad (GET /api/cities/{ine})
    data: null,          // resultado del cálculo (payload)
    legendView: "score", // "score" | "prio" (selector de la leyenda)
    layer: null, emojis: null, attribution: null, minZoom: 14,
    runHash: null, pollTimer: null, feedsFor: null, busy: false
  };

  var map = L.map("map", { zoomControl: true }).setView([40.2, -3.7], 6);
  // Teselas estándar de OSM: su atribución ("© OpenStreetMap contributors") va en la de cada ciudad
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 }).addTo(map);
  setAttribution('<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap contributors</a>');
  map.on("zoomend", updateEmojis);

  // ------------------------------------------------------------------
  // URL compartible
  // ------------------------------------------------------------------
  var settingHash = false;

  function writeHash() {
    if (!state.city) return;
    var p = state.data ? state.data.params : collectParams();
    var parts = ["ine=" + state.city.ine, "mode=" + p.mode, "view=" + p.view,
                 "d=" + p.distance_m, "t=" + p.time_minutes, "k=" + p.decay,
                 "s=" + p.services.map(function (s) { return s.id + ":" + s.weight; }).join(",")];
    if (state.runHash) parts.push("run=" + state.runHash);
    settingHash = true;
    history.replaceState(null, "", "#" + parts.join("&"));
    setTimeout(function () { settingHash = false; }, 0);
  }

  function readHash() {
    var h = new URLSearchParams(location.hash.replace(/^#/, ""));
    if (!h.get("ine")) return null;
    var out = { ine: h.get("ine"), run: h.get("run") || null, params: null };
    if (h.get("mode")) {
      out.params = {
        mode: h.get("mode"), view: h.get("view") || "score",
        distance_m: +h.get("d") || 800, time_minutes: +h.get("t") || 15, decay: +h.get("k") || 2,
        services: (h.get("s") || "").split(",").filter(Boolean).map(function (x) {
          var kv = x.split(":"); return { id: kv[0], weight: kv[1] === undefined ? 1 : +kv[1] };
        })
      };
    }
    return out;
  }

  window.addEventListener("hashchange", function () {
    if (settingHash) return;
    var h = readHash();
    if (h) loadCity(h.ine, { params: h.params, run: h.run });
  });

  // ------------------------------------------------------------------
  // Mapa
  // ------------------------------------------------------------------
  function bandIndex(score, bands) {
    for (var i = 0; i < bands.length; i++) if (score >= bands[i].min) return i;
    return bands.length - 1;
  }
  function prioClassOf(value, breaks) {     // np.searchsorted(cuts, value, side="right")
    var c = 0;
    while (c < breaks.length && breaks[c] <= value) c++;
    return c;
  }

  function fillFor(a) {
    var d = state.data, st = d.stats, p = d.params;
    if (p.view === "count") return rdylgn(a.score / Math.max(d.services.length, 1));
    if (state.legendView === "prio" && st.priority) {
      var n = st.priority.classes.length;
      return PRIO_COLORS[n][prioClassOf(a.prioridad, st.priority.breaks)];
    }
    return BAND_COLORS[bandIndex(a.puntuacion, st.bands)];
  }

  function styleFor(f) {
    var a = state.data && state.data.areas[f.properties.area_id];
    return { fillColor: a ? fillFor(a) : "#9aa0a6", color: "#555", weight: 0.5, fillOpacity: a ? 0.65 : 0.25 };
  }

  function setGeometry(geo, fit) {
    if (state.layer) map.removeLayer(state.layer);
    state.layer = L.geoJSON(geo, {
      style: styleFor,
      onEachFeature: function (f, lyr) {
        lyr.bindTooltip(function () { return tooltipHtml(f.properties.area_id); },
          { sticky: true, className: "qm-tip", direction: "auto" });
        lyr.on("mouseover", function () { lyr.setStyle({ weight: 2, color: "#222" }); lyr.bringToFront(); });
        lyr.on("mouseout", function () { lyr.setStyle({ weight: 0.5, color: "#555" }); });
      }
    }).addTo(map);
    if (fit) map.fitBounds(state.layer.getBounds(), { padding: [10, 10] });
  }

  function recolor() { if (state.layer) state.layer.setStyle(styleFor); }

  function buildEmojis() {
    if (state.emojis) map.removeLayer(state.emojis);
    var d = state.data, group = L.layerGroup();
    Object.keys(d.areas).forEach(function (id) {
      var a = d.areas[id];
      var missing = d.services.filter(function (s) { return s.emoji && a.svc[s.id] && !a.svc[s.id][0]; })
        .map(function (s) { return s.emoji; });
      if (!missing.length || !a.c) return;
      var n = missing.length;
      L.marker(a.c, {
        interactive: false, keyboard: false,
        icon: L.divIcon({ html: '<div class="qm-emoji">' + missing.join("") + "</div>",
                          className: "", iconSize: [n * 22, 22], iconAnchor: [n * 11, 11] })
      }).addTo(group);
    });
    state.emojis = group;
    state.minZoom = (d.city && d.city.emoji_min_zoom) || 14;
    updateEmojis();
  }

  function updateEmojis() {
    var g = state.emojis;
    if (!g) return;
    if (map.getZoom() >= state.minZoom) { if (!map.hasLayer(g)) map.addLayer(g); }
    else if (map.hasLayer(g)) map.removeLayer(g);
  }

  function setAttribution(html) {
    if (state.attribution) map.attributionControl.removeAttribution(state.attribution);
    state.attribution = html || null;
    if (html) map.attributionControl.addAttribution(html);
  }

  var MODE_TEXT = {
    distance: function (p) { return "a &lt;" + fmtInt(p.distance_m) + " m en línea recta"; },
    network: function (p) { return "a &lt;" + fmtInt(p.distance_m) + " m por la red peatonal"; },
    transit: function (p) { return "en &lt;" + p.time_minutes + " min en transporte público"; }
  };

  function renderResult(payload) {
    state.data = payload;
    if (payload.params.view === "count") state.legendView = "score";
    $("qm-subtitle").innerHTML = esc(payload.city.name) + " · servicios " + MODE_TEXT[payload.params.mode](payload.params);
    setAttribution(payload.city.attribution);
    recolor();
    buildEmojis();
    renderLegend();
  }

  // ------------------------------------------------------------------
  // Tooltip
  // ------------------------------------------------------------------
  function tooltipHtml(id) {
    var d = state.data, a = d && d.areas[id];
    if (!a) return "Sin datos";
    var p = d.params, unit = p.mode === "transit" ? " min" : " m";
    var html = "<b>Sección " + esc(a.cusec || id) + "</b><br>" +
      '<span class="kv">Puntuación:</span> ' + nf1.format(a.puntuacion) + " / 100";
    if (a.poblacion != null) {
      html += ' · <span class="kv">Habitantes:</span> ' + fmtInt(a.poblacion);
      if (a.prioridad != null) html += '<br><span class="kv">Prioridad (hab. × déficit):</span> ' + fmtInt(a.prioridad);
    }
    html += '<br><span class="kv">Servicios accesibles:</span> ' + a.score + " de " + d.services.length + "<table>";
    d.services.forEach(function (s) {
      var v = a.svc[s.id] || [false, null, "—"], ok = v[0], dist = v[1];
      html += "<tr><td>" + (s.emoji || "•") + "</td><td>" + esc(s.label) + "</td>" +
        '<td class="' + (ok ? "ok" : "ko") + '">' + (ok ? "✓" : "✗") + "</td><td>" + esc(v[2]) + "</td>" +
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
  function swatch(c) { return '<span class="qm-sw" style="background:' + c + '"></span>'; }
  function emo(e) { return '<span class="qm-emo">' + e + "</span>"; }

  function renderLegend() {
    var d = state.data, st = d.stats, p = d.params, cov = st.coverage, html, body = "";
    if (p.view === "count") {
      html = '<div class="qm-title">Servicios ' + MODE_TEXT[p.mode](p) + "</div>";
      var n = d.services.length;
      body += '<div class="qm-h">Nº de servicios' + (cov ? " · % población" : "") + "</div>";
      (st.counts || []).forEach(function (c) {
        var label = c.score === 0 ? "0 — ninguno" : c.score === n ? n + " — todos" : String(c.score);
        body += row(swatch(rdylgn(c.score / Math.max(n, 1))), label, cov ? fmtPct(c.pct) : "");
      });
    } else {
      var far = p.mode === "transit" ? nf2.format(p.time_minutes * p.decay) + " min"
                                     : fmtInt(p.distance_m * p.decay) + " m";
      html = '<div class="qm-title">Puntuación de accesibilidad</div>' +
        '<div class="qm-sub">100 = todos los servicios ' + MODE_TEXT[p.mode](p) +
        (p.decay > 1 ? "<br>crédito parcial hasta " + far : "") + "</div>";
      if (st.priority) {
        body += '<div class="qm-toggle" role="radiogroup">' + ["score", "prio"].map(function (k) {
          return '<label><input type="radio" name="qm-lview" value="' + k + '"' +
            (state.legendView === k ? " checked" : "") + "> " + (k === "score" ? "Puntuación" : "Prioridad") + "</label>";
        }).join("") + "</div>";
      }
      if (state.legendView === "prio" && st.priority) {
        var cls = st.priority.classes, nc = cls.length;
        body += '<div class="qm-h">Hab. × déficit de servicios</div>';
        for (var c = nc - 1; c >= 0; c--) {
          var k = cls[c];
          var lab = k.max == null ? "≥ " + fmtInt(k.min) : fmtInt(k.min) + " – " + fmtInt(k.max);
          body += row(swatch(PRIO_COLORS[nc][c]), lab, k.areas + " áreas");
        }
      } else {
        body += '<div class="qm-h">Puntuación' + (cov ? " · % población" : "") + "</div>";
        st.bands.forEach(function (b, i) { body += row(swatch(BAND_COLORS[i]), esc(b.label), cov ? fmtPct(b.pct) : ""); });
      }
    }
    if (cov) {
      var year = st.population && st.population.year ? " · INE " + st.population.year : "";
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
    if (p.mode === "transit") {
      body += '<div class="qm-warn">Solo cuenta lo que se alcanza subiendo a un transporte público (con la espera ' +
        "media de cada parada), no los trayectos solo a pie. Donde hay poco servicio los resultados son pesimistas.</div>";
    }
    $("qm-legend-body").innerHTML = html + '<div class="qm-collapsible" style="margin-top:6px">' + body + "</div>";
    document.querySelectorAll("input[name=qm-lview]").forEach(function (r) {
      r.addEventListener("change", function () { state.legendView = r.value; recolor(); renderLegend(); });
    });
  }

  // ------------------------------------------------------------------
  // Formulario
  // ------------------------------------------------------------------
  var form = $("qm-form");

  function defaultParams(meta) {
    return {
      mode: meta.has_network ? "network" : "distance", view: "score", distance_m: 800, time_minutes: 15,
      decay: 2, services: meta.services.map(function (s) { return { id: s.id, weight: 1 }; })
    };
  }

  function available(meta, mode) {
    return mode === "distance" || (mode === "network" && meta.has_network) || (mode === "transit" && meta.has_transit);
  }

  function fillForm(meta, p) {
    if (!available(meta, p.mode)) p.mode = meta.has_network ? "network" : "distance";
    form.querySelectorAll("input[name=mode]").forEach(function (r) {
      var ok = available(meta, r.value);
      r.disabled = !ok;
      r.parentNode.classList.toggle("disabled", !ok);
      r.parentNode.title = ok ? "" : r.value === "transit" ? "Este municipio no tiene transporte público: añádelo abajo"
                                                           : "Este municipio no tiene red peatonal";
      r.checked = r.value === p.mode;
    });
    form.querySelectorAll("input[name=view]").forEach(function (r) { r.checked = r.value === p.view; });
    $("qm-distance").value = p.distance_m;
    $("qm-minutes").value = p.time_minutes;
    $("qm-decay").value = p.decay;
    var weights = {};
    p.services.forEach(function (s) { weights[s.id] = s.weight; });
    $("qm-services").innerHTML = meta.services.map(function (s) {
      var on = s.id in weights, w = on ? weights[s.id] : 1;
      return '<div class="qm-svc"><input type="checkbox" id="qm-s-' + s.id + '" data-svc="' + s.id + '"' + (on ? " checked" : "") +
        '><label class="lbl" for="qm-s-' + s.id + '">' + (s.emoji || "•") + " " + esc(s.label) + "</label>" +
        '<input type="number" min="0" max="5" step="0.5" value="' + w + '" data-weight="' + s.id +
        '" aria-label="Peso de ' + esc(s.label) + '" title="Peso en la puntuación (0–5)"></div>';
    }).join("");
    syncFields();
    show(form, true);
  }

  function collectParams() {
    var mode = (form.querySelector("input[name=mode]:checked") || {}).value || "distance";
    var view = (form.querySelector("input[name=view]:checked") || {}).value || "score";
    var services = [];
    form.querySelectorAll("input[data-svc]").forEach(function (c) {
      if (!c.checked) return;
      var w = parseFloat(form.querySelector('input[data-weight="' + c.dataset.svc + '"]').value);
      services.push({ id: c.dataset.svc, weight: isNaN(w) ? 1 : Math.max(0, Math.min(5, w)) });
    });
    return { mode: mode, view: view, distance_m: +$("qm-distance").value, time_minutes: +$("qm-minutes").value,
             decay: +$("qm-decay").value, services: services };
  }

  function syncFields() {
    var p = collectParams();
    show("qm-f-distance", p.mode !== "transit");
    show("qm-f-minutes", p.mode === "transit");
    show("qm-f-decay", p.view === "score");
    $("qm-distance-out").textContent = fmtInt(p.distance_m) + " m";
    $("qm-minutes-out").textContent = p.time_minutes + " min";
    var far = p.mode === "transit" ? nf2.format(p.time_minutes * p.decay) + " min" : fmtInt(p.distance_m * p.decay) + " m";
    $("qm-decay-out").textContent = p.decay <= 1 ? "sin crédito parcial" : "×" + nf2.format(p.decay) + " = " + far;
  }

  form.addEventListener("input", syncFields);
  form.addEventListener("change", syncFields);
  form.addEventListener("submit", function (ev) { ev.preventDefault(); runCalc(); });

  function setYaml() {
    var a = $("qm-yaml");
    if (state.runHash && state.city) {
      a.href = API + "/api/runs/" + state.city.ine + "/" + state.runHash + "/config.yaml";
      a.setAttribute("aria-disabled", "false");
      a.title = "Descarga la configuración para repetir el cálculo en local";
    } else {
      a.href = "#";
      a.setAttribute("aria-disabled", "true");
      a.title = "Recalcula para poder descargar la configuración";
    }
  }

  $("qm-share").addEventListener("click", function () {
    writeHash();
    var url = location.href;
    (navigator.clipboard ? navigator.clipboard.writeText(url) : Promise.reject()).then(function () {
      status("Enlace copiado al portapapeles.");
    }, function () { window.prompt("Copia este enlace:", url); });
  });

  // ------------------------------------------------------------------
  // Cálculo
  // ------------------------------------------------------------------
  function setBusy(on) {
    state.busy = on;
    $("qm-run").disabled = on;
  }

  function runCalc(params) {
    if (!state.city || state.busy) return Promise.resolve();
    var p = params || collectParams();
    if (!p.services.length) { status("Elige al menos un servicio.", "error"); return Promise.resolve(); }
    if (!p.services.some(function (s) { return s.weight > 0; })) {
      status("Algún servicio debe tener peso mayor que 0.", "error"); return Promise.resolve();
    }
    setBusy(true);
    status("Calculando…", "busy");
    var body = { ine: state.city.ine, mode: p.mode, view: p.view, distance_m: p.distance_m,
                 time_minutes: p.time_minutes, decay: p.decay, services: p.services };
    return api("/api/runs", { method: "POST", body: body }).then(function (payload) {
      state.runHash = payload.run && payload.run.hash;
      renderResult(payload);
      setYaml();
      writeHash();
      status(null);
    }).catch(function (e) { status(errorText(e), "error"); })
      .then(function () { setBusy(false); });
  }

  function loadRun(ine, hash) {
    status("Cargando resultado…", "busy");
    return api("/api/runs/" + ine + "/" + hash).then(function (payload) {
      state.runHash = hash;
      renderResult(payload);
      fillForm(state.city, payload.params);
      setYaml();
      status(null);
    });
  }

  // ------------------------------------------------------------------
  // Ciudades
  // ------------------------------------------------------------------
  function cityInfo(meta) {
    var bits = [esc(meta.province || ""), meta.population ? fmtInt(meta.population) + " hab." : ""];
    if (meta.prepared_at) {
      var d = meta.prepared_at.slice(0, 10).split("-");
      bits.push("datos del " + d[2] + "/" + d[1] + "/" + d[0]);
    }
    $("qm-city-info").innerHTML = bits.filter(Boolean).join(" · ");
  }

  function loadCity(ine, opts) {
    opts = opts || {};
    stopPolling();
    status("Cargando municipio…", "busy");
    return api("/api/cities/" + ine).then(function (meta) {
      if (!meta.prepared) {
        state.city = null;
        showUnprepared(meta);
        return;
      }
      state.city = meta;
      state.feedsFor = null;
      show("qm-prepare", false);
      show("qm-job", false);
      $("qm-search").value = meta.name;
      cityInfo(meta);
      renderTransitSection(meta);
      var geoUrl = ine === STATIC_INE ? STATIC_BASE + "areas.geojson" : API + "/api/cities/" + ine + "/areas.geojson";
      return getJSON(geoUrl).then(function (geo) {
        state.data = null;
        setGeometry(geo, true);
        var p = opts.params || defaultParams(meta);
        fillForm(meta, p);
        if (opts.run) return loadRun(ine, opts.run).catch(function () { return runCalc(collectParams()); });
        return runCalc(collectParams());
      });
    }).catch(function (e) { status(errorText(e), "error"); });
  }

  // Arranque sin hash: València precalculada al instante; los metadatos de la API llegan después
  function loadStatic() {
    return Promise.all([getJSON(STATIC_BASE + "areas.geojson"), getJSON(STATIC_BASE + "result.json")])
      .then(function (r) {
        var payload = r[1];
        setGeometry(r[0], true);
        renderResult(payload);
        // Formulario provisional con lo que trae el precálculo (por si la API tarda o no responde)
        state.city = { ine: STATIC_INE, name: payload.city.name, has_network: true, has_transit: false,
                       services: payload.services };
        fillForm(state.city, payload.params);
        $("qm-search").value = payload.city.name;
        setYaml();
        return api("/api/cities/" + STATIC_INE).then(function (meta) {
          if (!meta.prepared) return;
          state.city = meta;
          cityInfo(meta);
          renderTransitSection(meta);
          fillForm(meta, payload.params);
        }).catch(function () { /* sin API: el mapa precalculado sigue siendo útil */ });
      }).catch(function (e) { status(errorText(e), "error"); });
  }

  // ------------------------------------------------------------------
  // Preparar municipios
  // ------------------------------------------------------------------
  function clearMap(message) {
    if (state.layer) map.removeLayer(state.layer);
    if (state.emojis) map.removeLayer(state.emojis);
    state.layer = state.emojis = state.data = null;
    state.runHash = null;
    setYaml();
    setAttribution('<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap contributors</a>');
    $("qm-legend-body").innerHTML = '<div class="qm-title">Sin datos</div><div class="qm-sub">' + esc(message) + "</div>";
  }

  function showUnprepared(meta) {
    clearMap(meta.allowed ? "Prepara " + meta.name + " para ver su mapa de accesibilidad."
                          : meta.name + " es demasiado grande para prepararlo desde la web.");
    $("qm-subtitle").textContent = meta.name + " · sin preparar";
    if (meta.bbox) map.fitBounds([[meta.bbox[1], meta.bbox[0]], [meta.bbox[3], meta.bbox[2]]], { padding: [20, 20] });
    show(form, false);
    show("qm-transit", false);
    $("qm-search").value = meta.name;
    $("qm-city-info").textContent = "";
    status(null);
    show("qm-prepare", true);
    var btn = $("qm-prepare-btn");
    if (!meta.allowed) {
      $("qm-prepare-text").textContent = meta.name + " supera el tamaño máximo que se puede preparar desde la web " +
        "(1,5 millones de habitantes y 400 km²).";
      btn.disabled = true;
    } else {
      $("qm-prepare-text").textContent = meta.name + " todavía no está preparado.";
      btn.disabled = false;
    }
    btn.onclick = function () { startPrepare(meta.ine); };
    if (meta.job) watchJob(meta.job, meta.ine);
  }

  function startPrepare(ine) {
    $("qm-prepare-btn").disabled = true;
    status("Enviando la petición…", "busy");
    api("/api/cities/" + ine + "/prepare", { method: "POST", body: { gtfs_feeds: [], gtfs_auto: $("qm-prepare-gtfs").checked } })
      .then(function (job) { status(null); watchJob(job.id, ine); })
      .catch(function (e) {
        if (e.status === 409 && e.data && e.data.job) { status(null); return watchJob(e.data.job, ine); }
        $("qm-prepare-btn").disabled = false;
        status(errorText(e), "error");
      });
  }

  function stopPolling() {
    if (state.pollTimer) clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }

  function watchJob(id, ine) {
    stopPolling();
    show("qm-prepare", false);
    show("qm-job", true);
    $("qm-job-title").textContent = "Preparando " + ($("qm-search").value || ine) + "…";
    var tick = function () {
      api("/api/jobs/" + id).then(function (j) {
        $("qm-job-bar").style.width = Math.max(3, j.pct || 0) + "%";
        $("qm-job-msg").textContent = (j.status === "queued" ? "En cola" : j.stage) + (j.msg ? ": " + j.msg : "") +
          " (" + (j.pct || 0) + " %)";
        if (j.kind === "transit") $("qm-job-title").textContent = "Añadiendo transporte público…";
        if (j.status === "done") {
          show("qm-job", false);
          status("Listo.");
          loadCity(ine);
        } else if (j.status === "error") {
          show("qm-job", false);
          status("La preparación ha fallado: " + j.msg, "error");
          if (j.kind !== "transit") show("qm-prepare", true);
          $("qm-prepare-btn").disabled = false;
        } else {
          state.pollTimer = setTimeout(tick, 4000);
        }
      }).catch(function () { state.pollTimer = setTimeout(tick, 8000); });
    };
    tick();
  }

  // ------------------------------------------------------------------
  // Transporte público (GTFS)
  // ------------------------------------------------------------------
  function renderTransitSection(meta) {
    show("qm-transit", true);
    var cur = meta.gtfs_sources || [];
    $("qm-transit-current").innerHTML = cur.length
      ? "Ahora: " + cur.map(function (g) { return esc(g.label); }).join(", ")
      : "Este municipio todavía no tiene transporte público.";
    $("qm-feeds").innerHTML = "";
    $("qm-transit-box").open = false;
  }

  $("qm-transit-box").addEventListener("toggle", function () {
    if (!this.open || !state.city || state.feedsFor === state.city.ine) return;
    var ine = state.city.ine;
    $("qm-feeds").innerHTML = '<div class="qm-sub">Buscando feeds…</div>';
    api("/api/cities/" + ine + "/gtfs-feeds").then(function (feeds) {
      state.feedsFor = ine;
      var current = {};
      (state.city.gtfs_sources || []).forEach(function (g) { current[g.id] = true; });
      if (!feeds.length) {
        $("qm-feeds").innerHTML = '<div class="qm-sub">No hay feeds del catálogo para esta zona.</div>';
        return;
      }
      $("qm-feeds").innerHTML = feeds.map(function (f) {
        var checked = current[f.id] || (f.auto && !(state.city.gtfs_sources || []).length);
        var lic = f.nap ? '<a href="https://nap.transportes.gob.es/licencia-datos" target="_blank" rel="noopener">NAP (MITRAMS)</a>'
                : f.license_url ? '<a href="' + esc(f.license_url) + '" target="_blank" rel="noopener">licencia</a>' : "sin licencia indicada";
        return '<label class="qm-feed"><input type="checkbox" data-feed="' + esc(f.id) + '"' + (checked ? " checked" : "") +
          (f.status !== "active" ? " disabled" : "") + "><span>" + esc(f.provider) +
          '<br><span class="meta"><span class="qm-badge">' + esc(f.scope) + "</span> " +
          (f.status !== "active" ? "inactivo · " : "") + lic + "</span></span></label>";
      }).join("");
    }).catch(function (e) { $("qm-feeds").innerHTML = '<div class="qm-sub">' + esc(errorText(e)) + "</div>"; });
  });

  $("qm-transit-btn").addEventListener("click", function () {
    if (!state.city) return;
    var ids = [];
    document.querySelectorAll("input[data-feed]:checked").forEach(function (c) { ids.push(c.dataset.feed); });
    var url = $("qm-feed-url").value.trim();
    if (url) ids.push(url);
    if (!ids.length) { status("Elige algún feed o escribe una URL.", "error"); return; }
    if (ids.length > MAX_FEEDS) { status("Como máximo " + MAX_FEEDS + " feeds.", "error"); return; }
    var ine = state.city.ine;
    status("Enviando la petición…", "busy");
    api("/api/cities/" + ine + "/transit", { method: "POST", body: { gtfs_feeds: ids, gtfs_auto: false } })
      .then(function (job) { status(null); watchJob(job.id, ine); })
      .catch(function (e) {
        if (e.status === 409 && e.data && e.data.job) { status(null); return watchJob(e.data.job, ine); }
        status(errorText(e), "error");
      });
  });

  // ------------------------------------------------------------------
  // Buscador de municipios
  // ------------------------------------------------------------------
  var searchTimer = null, results = [], active = -1;
  var input = $("qm-search"), list = $("qm-results");

  function renderResults() {
    list.innerHTML = results.map(function (m, i) {
      var badge = m.prepared ? '<span class="qm-badge ok">preparado</span>'
                : m.allowed ? '<span class="qm-badge">sin preparar</span>' : '<span class="qm-badge ko">demasiado grande</span>';
      return '<li role="option" data-i="' + i + '" aria-selected="' + (i === active) + '"><span class="name">' +
        esc(m.nombre) + '</span><span class="meta">' + esc(m.provincia) + " · " + fmtInt(m.poblacion || 0) +
        " hab.</span>" + badge + "</li>";
    }).join("") || '<li class="qm-sub">Sin resultados</li>';
    list.hidden = false;
  }

  function pick(m) {
    list.hidden = true;
    input.value = m.nombre;
    state.runHash = null;
    loadCity(m.ine).then(writeHash);
  }

  input.addEventListener("input", function () {
    clearTimeout(searchTimer);
    var q = input.value.trim();
    if (q.length < 2) { list.hidden = true; return; }
    searchTimer = setTimeout(function () {
      api("/api/municipalities?q=" + encodeURIComponent(q) + "&limit=12").then(function (r) {
        results = r; active = -1; renderResults();
      }).catch(function (e) { results = []; list.innerHTML = '<li class="qm-sub">' + esc(errorText(e)) + "</li>"; list.hidden = false; });
    }, 250);
  });
  input.addEventListener("keydown", function (ev) {
    if (list.hidden || !results.length) return;
    if (ev.key === "ArrowDown") { active = Math.min(active + 1, results.length - 1); renderResults(); ev.preventDefault(); }
    else if (ev.key === "ArrowUp") { active = Math.max(active - 1, 0); renderResults(); ev.preventDefault(); }
    else if (ev.key === "Enter") { pick(results[Math.max(active, 0)]); ev.preventDefault(); }
    else if (ev.key === "Escape") { list.hidden = true; }
  });
  list.addEventListener("mousedown", function (ev) {
    var li = ev.target.closest("li[data-i]");
    if (li) { ev.preventDefault(); pick(results[+li.dataset.i]); }
  });
  input.addEventListener("blur", function () { setTimeout(function () { list.hidden = true; }, 150); });

  // ------------------------------------------------------------------
  // Paneles plegables
  // ------------------------------------------------------------------
  function toggler(btnId, boxId, openTxt, closedTxt) {
    var btn = $(btnId);
    btn.addEventListener("click", function () {
      var collapsed = $(boxId).classList.toggle("collapsed");
      btn.textContent = collapsed ? closedTxt : openTxt;
      btn.setAttribute("aria-expanded", String(!collapsed));
    });
    return btn;
  }
  var legendBtn = toggler("qm-legend-toggle", "qm-legend", "▾", "▸");
  var panelBtn = toggler("qm-panel-toggle", "qm-panel", "☰", "☰");
  if (window.matchMedia("(max-width: 700px)").matches) { legendBtn.click(); panelBtn.click(); }

  // ------------------------------------------------------------------
  // Arranque
  // ------------------------------------------------------------------
  var start = readHash();
  if (start && (start.ine !== STATIC_INE || start.params || start.run)) loadCity(start.ine, { params: start.params, run: start.run });
  else loadStatic();
})();
