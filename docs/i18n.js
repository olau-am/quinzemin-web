/* quinzemin · textos de la interfaz en castellano, catalán e inglés.
 * Uso: t("clave", {var: valor}) en app.js; los elementos estáticos de index.html llevan
 * data-i18n (texto), data-i18n-html, data-i18n-placeholder, data-i18n-title o data-i18n-aria.
 * Las banderas son SVG en línea (Windows no dibuja los emojis de banderas y la senyera no tiene emoji).
 */
(function () {
  "use strict";

  var LANGS = [
    { code: "es", name: "Castellano", locale: "es-ES" },
    { code: "ca", name: "Català", locale: "ca-ES" },
    { code: "en", name: "English", locale: "en-GB" }
  ];

  var _uid = 0;
  function flag(code) {
    var id = "qmf" + (++_uid);
    if (code === "es") {
      return '<svg class="qm-flag" viewBox="0 0 750 500" aria-hidden="true"><rect width="750" height="500" fill="#c60b1e"/>' +
        '<rect y="125" width="750" height="250" fill="#ffc400"/></svg>';
    }
    if (code === "ca") {   // senyera: 9 franjas, 4 rojas sobre fondo amarillo
      return '<svg class="qm-flag" viewBox="0 0 810 540" aria-hidden="true"><rect width="810" height="540" fill="#fcdd09"/>' +
        [60, 180, 300, 420].map(function (y) { return '<rect y="' + y + '" width="810" height="60" fill="#da121a"/>'; }).join("") +
        "</svg>";
    }
    return '<svg class="qm-flag" viewBox="0 0 60 30" aria-hidden="true"><clipPath id="' + id + 's"><path d="M0,0v30h60v-30z"/></clipPath>' +
      '<clipPath id="' + id + 't"><path d="M30,15h30v15zv15h-30zh-30v-15zv-15h30z"/></clipPath><g clip-path="url(#' + id + 's)">' +
      '<path d="M0,0v30h60v-30z" fill="#012169"/><path d="M0,0L60,30M60,0L0,30" stroke="#fff" stroke-width="6"/>' +
      '<path d="M0,0L60,30M60,0L0,30" clip-path="url(#' + id + 't)" stroke="#C8102E" stroke-width="4"/>' +
      '<path d="M30,0v30M0,15h60" stroke="#fff" stroke-width="10"/><path d="M30,0v30M0,15h60" stroke="#C8102E" stroke-width="6"/></g></svg>';
  }

  // [castellano, catalán, inglés]
  var T = {
    // --- Página y panel ---
    "doc.title": ["quinzemin · ciudad de los 15 minutos", "quinzemin · ciutat dels 15 minuts", "quinzemin · 15-minute city"],
    "doc.description": ["Acceso a servicios básicos por sección censal en cualquier municipio de España: puntuación de accesibilidad y prioridad.",
      "Accés a serveis bàsics per secció censal a qualsevol municipi d'Espanya: puntuació d'accessibilitat i prioritat.",
      "Access to basic services by census section in any Spanish municipality: accessibility score and priority."],
    "loading": ["Cargando…", "Carregant…", "Loading…"],
    "panel.aria": ["Configuración", "Configuració", "Settings"],
    "panel.toggle": ["Mostrar u ocultar el panel", "Mostra o amaga el tauler", "Show or hide the panel"],
    "legend.toggle": ["Mostrar u ocultar la leyenda", "Mostra o amaga la llegenda", "Show or hide the legend"],
    "map.aria": ["Mapa de accesibilidad", "Mapa d'accessibilitat", "Accessibility map"],
    "lang.label": ["Idioma", "Idioma", "Language"],
    "sec.municipality": ["Municipio", "Municipi", "Municipality"],
    "search.placeholder": ["Buscar municipio o código INE…", "Cerca un municipi o un codi INE…", "Search municipality or INE code…"],
    "sec.calc": ["Cálculo", "Càlcul", "Calculation"],
    "mode.aria": ["Modo de cálculo", "Mode de càlcul", "Calculation mode"],
    "mode.distance": ["Línea recta", "Línia recta", "Straight line"],
    "mode.network": ["Red peatonal", "Xarxa de vianants", "Walking network"],
    "mode.transit": ["Transporte", "Transport", "Transit"],
    "f.distance": ["Distancia máxima", "Distància màxima", "Maximum distance"],
    "f.minutes": ["Tiempo máximo", "Temps màxim", "Maximum time"],
    "f.tmodel": ["Modelo de transporte", "Model de transport", "Transit model"],
    "tmodel.frequencies": ["Con esperas y trasbordos", "Amb esperes i transbords", "With waits and transfers"],
    "tmodel.simple": ["Simplificado (proyecto original)", "Simplificat (projecte original)", "Simplified (original project)"],
    "f.penalty": ["Penalización por trasbordo", "Penalització per transbord", "Transfer penalty"],
    "view.aria": ["Vista del mapa", "Vista del mapa", "Map view"],
    "view.score": ["Puntuación 0–100", "Puntuació 0–100", "Score 0–100"],
    "view.count": ["Nº de servicios", "Nre. de serveis", "No. of services"],
    "f.decay": ["Crédito parcial hasta", "Crèdit parcial fins a", "Partial credit up to"],
    "services.box": ["Servicios y pesos", "Serveis i pesos", "Services and weights"],
    "btn.run": ["Recalcular", "Recalcula", "Recalculate"],
    "btn.share": ["Compartir", "Comparteix", "Share"],
    "share.title": ["Copiar el enlace a este resultado", "Copia l'enllaç a aquest resultat", "Copy the link to this result"],
    "btn.capture": ["📷 Captura del mapa (PNG)", "📷 Captura del mapa (PNG)", "📷 Map screenshot (PNG)"],
    "capture.title": ["Imagen PNG del municipio entero con la leyenda (para informes)",
      "Imatge PNG del municipi sencer amb la llegenda (per a informes)",
      "PNG image of the whole municipality with the legend (for reports)"],
    "yaml.title": ["Descarga la configuración para repetir el cálculo en local",
      "Baixa la configuració per repetir el càlcul en local", "Download the settings to repeat the calculation locally"],
    "yaml.disabled": ["Recalcula para poder descargar la configuración", "Recalcula per poder baixar la configuració",
      "Recalculate to download the settings"],

    // --- Preparar municipios ---
    "prepare.gtfs": ["Incluir transporte público (feeds GTFS urbanos y regionales)",
      "Inclou el transport públic (feeds GTFS urbans i regionals)", "Include public transport (urban and regional GTFS feeds)"],
    "prepare.btn": ["Preparar municipio", "Prepara el municipi", "Prepare municipality"],
    "prepare.note": ["Se descargan sus secciones censales y población (INE), los servicios y la red peatonal (OpenStreetMap). Suele tardar entre 5 y 30 minutos; puedes dejar la página abierta o volver luego.",
      "S'hi baixen les seccions censals i la població (INE), els serveis i la xarxa de vianants (OpenStreetMap). Sol trigar entre 5 i 30 minuts; pots deixar la pàgina oberta o tornar-hi més tard.",
      "Its census sections and population (INE), services and walking network (OpenStreetMap) are downloaded. It usually takes 5 to 30 minutes; you can leave the page open or come back later."],
    "prepare.not_ready": ["{name} todavía no está preparado.", "{name} encara no està preparat.", "{name} has not been prepared yet."],
    "prepare.too_big": ["{name} supera el tamaño máximo que se puede preparar desde la web (1,5 millones de habitantes y 400 km²).",
      "{name} supera la mida màxima que es pot preparar des del web (1,5 milions d'habitants i 400 km²).",
      "{name} exceeds the maximum size that can be prepared from the website (1.5 million inhabitants and 400 km²)."],
    "unprep.legend": ["Prepara {name} para ver su mapa de accesibilidad.", "Prepara {name} per veure'n el mapa d'accessibilitat.",
      "Prepare {name} to see its accessibility map."],
    "unprep.legend_big": ["{name} es demasiado grande para prepararlo desde la web.", "{name} és massa gran per preparar-lo des del web.",
      "{name} is too large to prepare from the website."],
    "unprep.subtitle": ["{name} · sin preparar", "{name} · sense preparar", "{name} · not prepared"],
    "job.preparing": ["Preparando {name}…", "Preparant {name}…", "Preparing {name}…"],
    "job.transit": ["Añadiendo transporte público…", "Afegint el transport públic…", "Adding public transport…"],
    "job.done": ["Listo.", "Fet.", "Done."],
    "job.failed": ["La preparación ha fallado: {msg}", "La preparació ha fallat: {msg}", "Preparation failed: {msg}"],
    "stage.queued": ["En cola", "En cua", "Queued"],
    "stage.empezando": ["Empezando", "Començant", "Starting"],
    "stage.áreas": ["Secciones censales", "Seccions censals", "Census sections"],
    "stage.servicios": ["Servicios", "Serveis", "Services"],
    "stage.red peatonal": ["Red peatonal", "Xarxa de vianants", "Walking network"],
    "stage.transporte público": ["Transporte público", "Transport públic", "Public transport"],
    "stage.guardando": ["Guardando", "Desant", "Saving"],
    "stage.listo": ["Listo", "Fet", "Done"],
    "stage.error": ["Error", "Error", "Error"],
    "sending": ["Enviando la petición…", "Enviant la petició…", "Sending request…"],

    // --- Transporte (GTFS) ---
    "transit.box": ["Transporte público (GTFS)", "Transport públic (GTFS)", "Public transport (GTFS)"],
    "transit.url": ["Otro feed (URL https de un zip GTFS):", "Un altre feed (URL https d'un zip GTFS):", "Another feed (https URL of a GTFS zip):"],
    "transit.btn": ["Añadir / sustituir transporte", "Afegeix / substitueix el transport", "Add / replace transit"],
    "transit.note": ["Catálogo: Mobility Database. Cada feed se usa con la licencia de su proveedor.",
      "Catàleg: Mobility Database. Cada feed s'usa amb la llicència del seu proveïdor.",
      "Catalogue: Mobility Database. Each feed is used under its provider's licence."],
    "transit.now": ["Ahora: {list}", "Ara: {list}", "Current: {list}"],
    "transit.none": ["Este municipio todavía no tiene transporte público.", "Aquest municipi encara no té transport públic.",
      "This municipality has no public transport yet."],
    "transit.refresh": ["Actualizar al modelo con esperas y trasbordos", "Actualitza al model amb esperes i transbords",
      "Update to the model with waits and transfers"],
    "transit.old_note": ["Los datos de transporte de este municipio son anteriores al modelo con esperas: actualízalos en «Transporte público».",
      "Les dades de transport d'aquest municipi són anteriors al model amb esperes: actualitza-les a «Transport públic».",
      "This municipality's transit data predate the model with waits: update them under “Public transport”."],
    "feeds.searching": ["Buscando feeds…", "Cercant feeds…", "Looking for feeds…"],
    "feeds.none": ["No hay feeds del catálogo para esta zona.", "No hi ha feeds del catàleg per a aquesta zona.", "No catalogue feeds for this area."],
    "feeds.licence": ["licencia", "llicència", "licence"],
    "feeds.no_licence": ["sin licencia indicada", "sense llicència indicada", "no licence stated"],
    "feeds.inactive": ["inactivo", "inactiu", "inactive"],
    "scope.local": ["local", "local", "local"],
    "scope.regional": ["regional", "regional", "regional"],
    "scope.nacional": ["nacional", "estatal", "national"],
    "feeds.choose": ["Elige algún feed o escribe una URL.", "Tria algun feed o escriu una URL.", "Choose a feed or enter a URL."],
    "feeds.max": ["Como máximo {n} feeds.", "Com a màxim {n} feeds.", "At most {n} feeds."],
    "mode.no_transit": ["Este municipio no tiene transporte público: añádelo abajo", "Aquest municipi no té transport públic: afegeix-lo a sota",
      "This municipality has no public transport: add it below"],
    "mode.no_network": ["Este municipio no tiene red peatonal", "Aquest municipi no té xarxa de vianants", "This municipality has no walking network"],

    // --- Buscador ---
    "badge.prepared": ["preparado", "preparat", "prepared"],
    "badge.unprepared": ["sin preparar", "sense preparar", "not prepared"],
    "badge.too_big": ["demasiado grande", "massa gran", "too large"],
    "search.none": ["Sin resultados", "Sense resultats", "No results"],

    // --- Estados y errores ---
    "calculating": ["Calculando…", "Calculant…", "Calculating…"],
    "loading.result": ["Cargando resultado…", "Carregant el resultat…", "Loading result…"],
    "loading.city": ["Cargando municipio…", "Carregant el municipi…", "Loading municipality…"],
    "need.service": ["Elige al menos un servicio.", "Tria almenys un servei.", "Choose at least one service."],
    "need.weight": ["Algún servicio debe tener peso mayor que 0.", "Algun servei ha de tenir un pes més gran que 0.",
      "At least one service must have a weight above 0."],
    "err.connect": ["No se puede conectar con el servidor. Inténtalo de nuevo en un momento.",
      "No es pot connectar amb el servidor. Torna-ho a provar d'aquí a una estona.",
      "Cannot reach the server. Please try again in a moment."],
    "err.load": ["No se ha podido cargar {url}", "No s'ha pogut carregar {url}", "Could not load {url}"],
    "err.too_many": ["Demasiadas peticiones.", "Massa peticions.", "Too many requests."],
    "err.retry_s": [" Prueba de nuevo en {n} s.", " Torna-ho a provar d'aquí a {n} s.", " Try again in {n} s."],
    "err.retry_min": [" Prueba de nuevo en {n} min.", " Torna-ho a provar d'aquí a {n} min.", " Try again in {n} min."],
    "share.copied": ["Enlace copiado al portapapeles.", "Enllaç copiat al porta-retalls.", "Link copied to clipboard."],
    "share.prompt": ["Copia este enlace:", "Copia aquest enllaç:", "Copy this link:"],
    "city.data_from": ["datos del {d}", "dades del {d}", "data from {d}"],
    "inhab": ["{n} hab.", "{n} hab.", "{n} inhab."],

    // --- Textos de modo ---
    "modetxt.distance": ["a &lt;{d} m en línea recta", "a &lt;{d} m en línia recta", "within {d} m in a straight line"],
    "modetxt.network": ["a &lt;{d} m por la red peatonal", "a &lt;{d} m per la xarxa de vianants", "within {d} m on foot"],
    "modetxt.transit": ["en &lt;{t} min en transporte público", "en &lt;{t} min en transport públic", "within {t} min by public transport"],
    "subtitle": ["{city} · servicios {mode}", "{city} · serveis {mode}", "{city} · services {mode}"],

    // --- Tooltip ---
    "tip.nodata": ["Sin datos", "Sense dades", "No data"],
    "tip.section": ["Sección {id}", "Secció {id}", "Section {id}"],
    "tip.score": ["Puntuación:", "Puntuació:", "Score:"],
    "tip.pop": ["Habitantes:", "Habitants:", "Inhabitants:"],
    "tip.prio": ["Prioridad (hab. × déficit):", "Prioritat (hab. × dèficit):", "Priority (inhab. × deficit):"],
    "tip.access": ["Servicios accesibles:", "Serveis accessibles:", "Accessible services:"],
    "tip.of": ["{a} de {n}", "{a} de {n}", "{a} of {n}"],

    // --- Leyenda ---
    "lg.title_count": ["Servicios {mode}", "Serveis {mode}", "Services {mode}"],
    "lg.title_score": ["Puntuación de accesibilidad", "Puntuació d'accessibilitat", "Accessibility score"],
    "lg.sub_score": ["100 = todos los servicios {mode}", "100 = tots els serveis {mode}", "100 = all services {mode}"],
    "lg.partial": ["crédito parcial hasta {far}", "crèdit parcial fins a {far}", "partial credit up to {far}"],
    "lg.h_count": ["Nº de servicios", "Nre. de serveis", "No. of services"],
    "lg.pct_pop": [" · % población", " · % població", " · % population"],
    "lg.count_none": ["0 — ninguno", "0 — cap", "0 — none"],
    "lg.count_all": ["{n} — todos", "{n} — tots", "{n} — all"],
    "lg.view_score": ["Puntuación", "Puntuació", "Score"],
    "lg.view_prio": ["Prioridad", "Prioritat", "Priority"],
    "lg.h_prio": ["Hab. × déficit de servicios", "Hab. × dèficit de serveis", "Inhab. × service deficit"],
    "lg.areas": ["{n} áreas", "{n} àrees", "{n} areas"],
    "lg.h_score": ["Puntuación", "Puntuació", "Score"],
    "lg.band_all": ["100 · todos", "100 · tots", "100 · all"],
    "lg.h_access": ["Población con acceso", "Població amb accés", "Population with access"],
    "lg.all": ["Todos los servicios", "Tots els serveis", "All services"],
    "lg.none": ["Ningún servicio", "Cap servei", "No service"],
    "lg.mean": ["Puntuación media", "Puntuació mitjana", "Mean score"],
    "lg.nodata": ["Sin datos", "Sense dades", "No data"],
    "tn.freq": ["Tiempo = el menor entre ir andando y en transporte público, esperando en cada subida la mitad del intervalo de esa línea (laborable, 7–21 h){pen}; trasbordos a pie de hasta 250 m.",
      "Temps = el menor entre anar a peu i en transport públic, esperant a cada pujada la meitat de l'interval d'aquella línia (feiner, 7–21 h){pen}; transbords a peu de fins a 250 m.",
      "Time = the shorter of walking and public transport, waiting at each boarding for half that line's headway (weekday, 7–21 h){pen}; walking transfers up to 250 m."],
    "tn.pen": [" más {p} min por trasbordo", " més {p} min per transbord", " plus {p} min per transfer"],
    "tn.fallback": ["<b>Datos de transporte antiguos: se ha usado el modelo simplificado.</b> ",
      "<b>Dades de transport antigues: s'ha fet servir el model simplificat.</b> ",
      "<b>Old transit data: the simplified model was used.</b> "],
    "tn.simple": ["Modelo simplificado: solo cuenta lo que se alcanza subiendo a un transporte (espera media solo en la primera parada, trasbordos sin espera), no los trayectos a pie. Donde hay poco servicio los resultados son pesimistas.",
      "Model simplificat: només compta el que s'assoleix pujant a un transport (espera mitjana només a la primera parada, transbords sense espera), no els trajectes a peu. On hi ha poc servei els resultats són pessimistes.",
      "Simplified model: only what can be reached by boarding a vehicle counts (average wait only at the first stop, transfers without waiting), not walking trips. Where service is sparse, results are pessimistic."],

    // --- Formulario ---
    "w.aria": ["Peso de {s}", "Pes de {s}", "Weight of {s}"],
    "w.title": ["Peso en la puntuación (0–5)", "Pes en la puntuació (0–5)", "Weight in the score (0–5)"],
    "penalty.none": ["ninguna", "cap", "none"],
    "decay.none": ["sin crédito parcial", "sense crèdit parcial", "no partial credit"],

    // --- Captura ---
    "cap.header": ["QUINZEMIN · CIUDAD DE LOS 15 MINUTOS", "QUINZEMIN · CIUTAT DELS 15 MINUTS", "QUINZEMIN · 15-MINUTE CITY"],
    "cap.mode.distance": ["Distancia en línea recta", "Distància en línia recta", "Straight-line distance"],
    "cap.mode.network": ["Distancia por la red peatonal", "Distància per la xarxa de vianants", "Walking network distance"],
    "cap.mode.transit": ["Transporte público", "Transport públic", "Public transport"],
    "cap.pen": [" · +{p} min por trasbordo", " · +{p} min per transbord", " · +{p} min per transfer"],
    "cap.partial": ["Crédito parcial hasta {far}", "Crèdit parcial fins a {far}", "Partial credit up to {far}"],
    "cap.nsvc": ["{n} servicios", "{n} serveis", "{n} services"],
    "cap.weights": [" · pesos: {list}", " · pesos: {list}", " · weights: {list}"],
    "cap.same": [" · mismo peso", " · mateix pes", " · equal weights"],
    "cap.h_count": ["Nº de servicios accesibles", "Nre. de serveis accessibles", "No. of accessible services"],
    "cap.h_prio": ["Prioridad · habitantes × déficit", "Prioritat · habitants × dèficit", "Priority · inhabitants × deficit"],
    "cap.sections": ["{n} secciones", "{n} seccions", "{n} sections"],
    "cap.h_score": ["Puntuación 0–100", "Puntuació 0–100", "Score 0–100"],
    "cap.generated": ["Generado el {d}", "Generat el {d}", "Generated on {d}"],
    "cap.credits": ["Mapa base: © OpenStreetMap contributors · Datos: {a}", "Mapa base: © OpenStreetMap contributors · Dades: {a}",
      "Base map: © OpenStreetMap contributors · Data: {a}"],
    "cap.generating": ["Generando la captura…", "Generant la captura…", "Generating screenshot…"],
    "cap.done": ["Captura descargada: {f}", "Captura baixada: {f}", "Screenshot downloaded: {f}"],
    "cap.missing": ["Captura descargada (faltan {n} teselas del mapa base).", "Captura baixada (falten {n} tessel·les del mapa base).",
      "Screenshot downloaded ({n} base-map tiles missing)."],
    "cap.failed": ["No se pudo generar la captura: {e}", "No s'ha pogut generar la captura: {e}", "Could not generate the screenshot: {e}"],
    // fragmentos del nombre de archivo de la captura
    "fn.distance": ["linea-recta", "linia-recta", "straight-line"],
    "fn.network": ["red", "xarxa", "walk"],
    "fn.transit": ["transporte", "transport", "transit"],
    "fn.waits": ["esperas", "esperes", "waits"],
    "fn.count": ["num-servicios", "nre-serveis", "num-services"],
    "fn.prio": ["prioridad", "prioritat", "priority"],
    "fn.score": ["puntuacion", "puntuacio", "score"],
    "fn.without": ["sin", "sense", "no"],

    // --- Servicios (por identificador; si no está, se usa la etiqueta de los datos) ---
    "svc.salud": ["Centros de Salud", "Centres de salut", "Health centres"],
    "svc.primaria": ["Ed. Primaria", "Ed. primària", "Primary education"],
    "svc.secundaria": ["Ed. Secundaria", "Ed. secundària", "Secondary education"],
    "svc.educacion": ["Educación", "Educació", "Education"],
    "svc.transporte": ["Transporte Público", "Transport públic", "Public transport"],
    "svc.supermercados": ["Supermercados", "Supermercats", "Supermarkets"],
    "svc.zonas_verdes": ["Zonas Verdes", "Zones verdes", "Green spaces"],
    "svc.cultura": ["Cultura", "Cultura", "Culture"],
    "svc.farmacias": ["Farmacias", "Farmàcies", "Pharmacies"]
  };

  var IDX = { es: 0, ca: 1, en: 2 };

  function detect() {
    var q = new URLSearchParams(location.search).get("lang");
    if (q && q in IDX) return q;
    try {
      var saved = localStorage.getItem("qm-lang");
      if (saved && saved in IDX) return saved;
    } catch (e) { /* almacenamiento no disponible */ }
    var nav = (navigator.languages || [navigator.language || "es"]).map(function (l) { return l.slice(0, 2).toLowerCase(); });
    for (var i = 0; i < nav.length; i++) if (nav[i] in IDX) return nav[i];
    return "es";
  }

  var I18N = {
    langs: LANGS,
    lang: detect(),
    flag: flag,
    locale: function () { return LANGS[IDX[I18N.lang]].locale; },
    has: function (key) { return key in T; },
    t: function (key, vars, fallback) {
      var row = T[key], s = row ? row[IDX[I18N.lang]] : (fallback !== undefined ? fallback : key);
      if (vars) s = s.replace(/\{(\w+)\}/g, function (m, k) { return k in vars ? vars[k] : m; });
      return s;
    },
    set: function (code) {
      if (!(code in IDX)) return;
      I18N.lang = code;
      try { localStorage.setItem("qm-lang", code); } catch (e) { /* sin almacenamiento: no se recuerda */ }
    },
    // Textos estáticos de la página (atributos data-i18n*)
    apply: function (root) {
      root = root || document;
      document.documentElement.lang = I18N.lang;
      document.title = I18N.t("doc.title");
      var md = document.querySelector('meta[name="description"]');
      if (md) md.setAttribute("content", I18N.t("doc.description"));
      root.querySelectorAll("[data-i18n]").forEach(function (el) { el.textContent = I18N.t(el.dataset.i18n); });
      root.querySelectorAll("[data-i18n-html]").forEach(function (el) { el.innerHTML = I18N.t(el.dataset.i18nHtml); });
      root.querySelectorAll("[data-i18n-placeholder]").forEach(function (el) { el.placeholder = I18N.t(el.dataset.i18nPlaceholder); });
      root.querySelectorAll("[data-i18n-title]").forEach(function (el) { el.title = I18N.t(el.dataset.i18nTitle); });
      root.querySelectorAll("[data-i18n-aria]").forEach(function (el) { el.setAttribute("aria-label", I18N.t(el.dataset.i18nAria)); });
    }
  };
  window.QM_I18N = I18N;
})();
