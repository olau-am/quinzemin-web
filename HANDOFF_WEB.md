# Handoff: quinzemin-web (servir quinzemin como aplicación web)

> Documento autocontenido para el agente que creará el **nuevo proyecto**. Resume el sistema
> actual, lo ya medido y verificado, las decisiones del usuario y el plan de construcción.
> Idioma del usuario y del proyecto: **español** (la memoria del TFM, DOCUMENTACIO.md, está en catalán).

---

## 1. Contexto

**quinzemin** es el prototipo de un TFM sobre la *ciudad de los 15 minutos*. Para cada sección censal de València calcula si hay acceso a 8 servicios básicos y lo pinta en un mapa coroplético.

- Repo actual: `c:\Users\olau0\Documents\GitHub\quinzemin` (GitHub `olau-am/quinzemin`, rama `main`; GitHub Pages sirve `docs/`).
- Entorno de desarrollo: Windows 11, Python 3.13 en `.venv/`, Git Bash y PowerShell. La consola usa cp1252, así que hay que poner `PYTHONIOENCODING=utf-8` para los ✓/⚠ que imprimen los scripts.

Hoy todo se ejecuta en local:
1. `python src/download_data.py` descarga los datos en `data/` (gitignored).
2. `python src/main.py` genera un HTML de folium estático en `docs/index.html`.
3. Cambiar de modo, distancia o vista exige editar `config.yaml` y volver a ejecutar.

`docs/configurar.html` es un formulario estático que solo genera un `config.yaml`.

**Lo que quiere el usuario:** una **web pública y gratuita** que:
- abra al instante con València precalculada;
- permita cambiar desde un formulario el modo de cálculo, los umbrales, la vista, los servicios y los pesos, y **recalcular en el servidor**;
- permita preparar **cualquier municipio de España**.

## 2. Decisiones ya tomadas con el usuario (no volver a preguntar)

| Tema | Decisión |
|---|---|
| Alcance del recálculo | València **y otras ciudades** |
| Ámbito de ciudades | **Solo España**: secciones censales y población del INE |
| Quién añade ciudades | **Cualquier visitante**, en cola y con límites; quedan guardadas para todos |
| Servicios en ciudades nuevas | Plantilla **OSM (Overpass)**; transit solo con **GTFS opcional** (catálogo Mobility Database o URL del usuario) |
| Acceso | **Público con límites** (caché, cola, límite por IP) |
| Frontend | **Leaflet en el cliente**: el servidor devuelve JSON y el navegador pinta. Se abandona folium en la web |
| Hosting | **Google Cloud Run** (API, contenedor Docker) + **GitHub Pages** (frontend y València precalculada) |
| Despliegue | **GitHub Actions** en push a `main` |

Pendiente de confirmar con el usuario al empezar:
- nombre y ubicación del nuevo repo (p. ej. `olau-am/quinzemin-web`, o carpetas nuevas dentro del repo actual);
- ID del proyecto GCP, que necesita una cuenta de facturación con tarjeta aunque el uso sea gratuito.

## 3. Sistema actual (código a reutilizar)

| Fichero | Qué hace | Qué reutilizar |
|---|---|---|
| `config.yaml` | ciudad, áreas, población, `analysis` (modo, umbrales, scoring, red, GTFS), `map` (vista), servicios | estructura de parámetros y servicios de València |
| `src/config.py` | `load()`, `build_attr()` (atribuciones de Leaflet) | `build_attr` |
| `src/download_data.py` | descargas por URL u Overpass (3 mirrors, `_osm_to_geojson`), fuentes múltiples por servicio, GTFS y red peatonal (`_download_walk_graph` con OSMnx, `network_type="walk"`, buffer de 1 km) | casi todo, para preparar ciudades |
| `src/main.py` | bucle por servicio en los 3 modos, `_nearest`, `_credit`, puntuación, prioridad, cobertura, leyenda, emojis y folium | **separar el motor de la presentación** |
| `src/network.py` | `WalkNetwork`: carga GraphML, snapping con cKDTree, `destinations()` (los parques con `access: polygon` usan sus nodos interiores), `nearest_service()` (Dijkstra multi-origen con nodos virtuales y scipy) e `isochrone()` | todo; añadir `to_npz`/`from_npz` |
| `src/gtfs.py` | `TransitGraph`: grafo de paradas desde GTFS, `reachable_from()` (Dijkstra con heapq) y `service_min_time()` | **reescribir `service_min_time`** (ver §4) |
| `src/population.py` | descarga de la población INE (`_parse_ine`, `download`), `assign()` (código de sección por plantilla y reparto por superficie de las secciones partidas), `coverage()`, `fmt_int`, `fmt_pct` | todo; parametrizar la tabla por provincia |
| `src/map_network.py`, `src/map_gtfs.py`, `src/map_distance.py` | mapas auxiliares de isócronas (folium) | no hacen falta en la web |
| `docs/configurar.html` | formulario y generador de YAML (Nominatim, js-yaml) | ideas de UI |

### Modos de cálculo (`analysis.mode`)

En los tres modos, la medida se toma desde el **centroide** de cada sección hasta el servicio más cercano.

- **`distance`**: distancia euclídea en UTM (CRS de `areas.estimate_utm_crs()`); el umbral es `distance_m`, 800 m.
- **`network`**: distancia a pie por la red de OSM. La distancia de enganche se suma al recorrido. Hay un Dijkstra multi-origen por categoría de servicio. Se descartan los servicios a más de `max_snap_m` (500 m) de la red. Columnas extra: `snap_m` y `rodeo_<svc>`.
- **`transit`**: GTFS de Metrovalencia y EMT. Umbral `time_minutes` (15), 80 m/min a pie y como máximo 500 m hasta la parada.

Columnas por servicio: `dist_{id}_m` (en minutos si es transit), `nombre_{id}`, `{id}_ok` y `cred_{id}`. Además: `score` (recuento 0..N).

### Vista PUNTUACIÓN (`map.view: score`), recién implementada y **sin commitear**

- **Crédito por servicio** (`_credit`): 1 hasta el umbral y caída lineal hasta 0 en umbral × `analysis.scoring.decay` (2.0; con 1 el criterio es binario).
  - Unidades: metros en distance/network, segundos en transit.
  - En transit, `reachable_from` y `service_min_time` se ejecutan con `budget × decay`; `{id}_ok` sigue siendo `≤ time_minutes`.
- **Puntuación**: `puntuacion = 100·Σ weight·crédito / Σ weight`. `weight` es opcional por servicio; un servicio sin datos cuenta con crédito 0.
- **Prioridad**: `prioridad = poblacion·(100−puntuacion)/100`.
  - En la leyenda, un selector cambia entre Puntuación y Prioridad.
  - La prioridad usa clases por cuantiles de los valores > 0, en la escala YlOrRd.
- **Tramos de la puntuación**: no son equidistantes (`SCORE_BANDS`): 100 / 90–100 / 75–90 / 50–75 / <50, con RdYlGn en 0,95 / 0,75 / 0,55 / 0,30 / 0,08. Motivo: con tramos de 20, el 95 % de la población cae en 80–100.
- **Leyenda con población** (también en la vista `count`):
  - % de población por tramo y por servicio;
  - "Todos los servicios", "Ningún servicio" y "Puntuación media";
  - se guarda en `data/cobertura_poblacion.csv`.
- **Bug corregido de paso**: el JS que oculta los emojis según el zoom se emitía en el `<body>`, antes de crear el mapa, y fallaba (los emojis se veían siempre). Ahora se inyecta con un `MacroElement` (`_add_script`). En la SPA no aplica, pero hay que cuidar el orden de los scripts.
- **Estado del repo**: hay cambios sin commitear en `config.yaml`, `src/main.py`, `src/download_data.py`, `src/population.py` (nuevo), `docs/configurar.html` (selector de vista) y en los tres documentos.
  - `docs/index.html` quedó sobrescrito con la salida de una prueba en transit: **hay que regenerarlo** con el `config.yaml` real (`python src/main.py`, network + score) antes de commitear.
  - **Preguntar al usuario antes de commitear.**

## 4. Datos y hechos verificados

### Población (INE, oficial y abierta; atribución "© INE")

- Fuente: Atlas de Distribución de Renta de los Hogares (ADRH), operación **353** de la API JSON de INEbase, tabla "Indicadores demográficos".
- **Una tabla por provincia**. La de Valencia es la **31258**.
- Llamada: `https://servicios.ine.es/wstempus/js/ES/DATOS_TABLA/31258?nult=1&tv=260:17338&tip=AM`.
  - `260:17338` es el concepto "Población"; `tip=AM` añade los metadatos con los códigos.
  - La respuesta viene en **ISO-8859-1** sin declararlo: decodificar como latin-1.
  - Algunas filas traen `Data` vacío.
- El código de sección es de 10 dígitos (CUSEC = provincia, municipio, distrito y sección). València = municipio `46250`, por ejemplo `4625001001`.
- Lista de tablas: `TABLAS_OPERACION/353`, con 54 tablas "Indicadores demográficos" (ids 30814…31303 y 53776). Van ordenadas por código de provincia; se comprobó Sevilla 31213, Soria 31222, Tarragona 31231 y Valencia 31258. **Generar el mapa provincia → tabla programáticamente** comprobando el código de la primera serie, sin suponer el orden.
- València 2024: 588 secciones con dato y 829.010 hab.
  - La capa municipal actual (geoportal, 589 polígonos y 584 códigos) casa al 100 % con 2024.
  - Quedan 4 secciones del INE sin polígono (02044, 11024, 11037, 13011), con 3.251 hab.
  - Hay secciones partidas en varios polígonos: 15008 ×2, 15036 ×2 y 17001 ×4.
  - Años anteriores casan peor: 2020 deja 13 sin polígono.
- **Cartografía nacional de secciones del INE**: `https://www.ine.es/prodyser/cartografia/seccionado_2024.zip` (65,7 MB, verificado). Usarla para todas las ciudades, **también València**: casa con el ADRH del mismo año y elimina las 4 huérfanas.
- `data/population_valencia.json` es una fuente heredada sin origen abierto (descartada en `notes/sources 1.txt`). **No usarla.**

### Servicios y transporte

- València usa fuentes municipales y autonómicas:
  - salud: WFS del ICV/GVA (devuelve toda la Comunitat; se filtra por el snapping de la red);
  - educación: VLCi, filtrada por `dgenerica`;
  - zonas verdes: geoportal;
  - Overpass para supermercados, cultura, farmacias y paradas de bus.
  - Las URLs están en `config.yaml`.
- Para ciudades nuevas, crear una plantilla Overpass equivalente:
  - salud: `amenity=clinic|doctors`, `healthcare=centre`;
  - educación: `amenity=school` (primaria y secundaria no se distinguen bien en OSM, así que hay que decidir con el usuario si se unen);
  - `shop=supermarket`, `leisure=park`, `amenity=pharmacy`, `library|theatre|cinema|arts_centre`;
  - paradas: `highway=bus_stop`, `railway=station|tram_stop`.
- Catálogo GTFS: Mobility Database, CSV público `https://files.mobilitydatabase.org/feeds_v2.csv` (2,7 MB, verificado, sin autenticación). Filtrar por `ES` y por bbox.
- Atribuciones obligatorias: © OpenStreetMap contributors (ODbL), INE y cada fuente GTFS. Ver README.

### Rendimiento medido (València, 589 secciones, este PC)

- **network**: ~27 s en total. La mayor parte es leer el GraphML de 80 MB con osmnx (62.852 nodos y 94.902 aristas); los 8 Dijkstra son rápidos.
- **transit** en vista score (presupuesto de 30 min): **47 min**.
  - Cuello de botella: `TransitGraph.service_min_time` hace `iterrows` de todos los elementos del servicio × `_nearby` para cada una de las 589 áreas.
  - **Hay que reescribirlo antes de exponerlo en la web**: precalcular por servicio `(parada, t_andando, feature)` una sola vez y, por área, tomar el mínimo sobre las paradas alcanzables. Objetivo: < 1 min.

### Resultados de referencia (800 m / 15 min, población INE 2024 asignada: 825.759 hab.)

Útiles como test de regresión:

| Modo | Acceso a los 8 servicios | Otros datos |
|---|---|---|
| distance | 88,6 % | |
| network | 54,6 % | salud 65,0 %; ningún servicio 1,6 %; puntuación media 95,6 |
| transit | 97,7 % | puntuación media 98,3 |

Secciones con acceso en network: salud 386, primaria 556, secundaria 526, transporte 576, supermercados 561, zonas verdes 579, cultura 485, farmacias 560 (de 589).

### Capa gratuita de Google Cloud (comprobada en nov. 2026)

- **Cloud Run**, facturación por petición: 2 M de peticiones, 180.000 vCPU·s y 360.000 GiB·s al mes en regiones Tier 1. Los Jobs comparten la cuota de instancia: 240.000 vCPU·s y 450.000 GiB·s.
- **Cloud Storage**: 5 GB gratis solo en `us-central1`, `us-east1` y `us-west1` → usar **`us-central1`** para todo.
- **Artifact Registry**: 0,5 GB gratis. La imagen rondará 0,6–0,8 GB, así que hay que poner una política de limpieza (puede costar céntimos).
- Con facturación por petición **solo hay CPU mientras la petición está abierta**: no se puede calcular en segundo plano después de responder.

## 5. Arquitectura propuesta

```
GitHub Pages (docs/)                         Google Cloud (us-central1)
┌───────────────────────────┐   fetch   ┌──────────────────────────────────────────┐
│ index.html (Leaflet SPA)  │ ────────▶ │ Cloud Run service "quinzemin-api"        │
│ panel de configuración    │           │  FastAPI · 2 vCPU / 4 GiB · max 2 inst.  │
│ leyenda / tooltips        │ ◀──────── │  POST /api/runs  (recalcula, cachea)     │
│ buscador de municipios    │   JSON    │  POST /api/cities/{ine}/prepare ─┐       │
│ data/cities/46250/*       │           │  GET  /api/jobs/{id}             │       │
│ data/municipios.json      │           └──────────────┬───────────────────┼───────┘
└───────────────────────────┘                          ▼                   ▼
                                        Cloud Storage (bucket)        Cloud Run Job
                                        spain/  cities/{ine}/          "quinzemin-prepare"
                                        runs/{ine}/{hash}.json  jobs/  (hasta 1 h, 4 GiB)
```

- **Recálculo síncrono** dentro de la petición (timeout del servicio de 15 min).
  - Semáforo de 1 cálculo por instancia; si está ocupado, `429` + `Retry-After`.
  - Caché en GCS por `sha256(parámetros normalizados)`.
  - Caché en memoria de los bundles ya cargados (grafo y transit).
- **Preparar una ciudad** es un Cloud Run **Job** que la API lanza con la API Admin v2 de Cloud Run.
  - El estado vive en `jobs/{id}.json`.
  - Lock con la precondición `ifGenerationMatch=0` de GCS, para no preparar dos veces la misma ciudad.
  - Sin base de datos.
- **Una imagen Docker** con dos entrypoints: `uvicorn` (API) y `python -m quinzemin.prepare <ine>` (Job). Usar `python:3.13-slim` y un usuario no root.
- **Bundle por ciudad** (`cities/{ine}/`):

  | Fichero | Contenido |
  |---|---|
  | `meta.json` | metadatos de la ciudad |
  | `areas.geojson` | geometría simplificada + CUSEC + población; el navegador lo descarga **una vez por ciudad** |
  | `areas.parquet` | secciones en UTM para el cálculo |
  | `services/{id}.parquet` | servicios ya filtrados |
  | `walk_graph.npz` | `node_xy, u, v, w`; **sin osmnx en runtime** |
  | `transit.pkl` | grafo de transporte, opcional |

- **Resultados sin geometría**: `{cusec: {puntuacion, score, poblacion, prioridad, dist_*, nombre_*, ok_*}}` + `stats`.
  - `stats` incluye la cobertura, los % por tramo y los cortes de prioridad, ya calculados en el servidor para no duplicar la lógica en JS.
  - Así ocupan ~100–300 KB comprimidos.
- **Límites**:
  - CORS solo desde el dominio de Pages;
  - por IP: 20 recálculos por hora y 2 preparaciones al día;
  - global: 1 preparación a la vez y ≤ 5 ciudades nuevas al día;
  - municipio ≤ 1,5 M hab. o ≤ 400 km² (configurable);
  - parámetros validados con pydantic: 100–3000 m, 5–60 min, decay 1–3, pesos 0–5;
  - URLs de GTFS del usuario: solo https, ≤ 200 MB, sin IPs privadas (SSRF);
  - `max-instances=2`;
  - alerta de presupuesto a 1 €.

## 6. Fases de construcción

1. **Motor** (`quinzemin/engine.py`): `compute(bundle, params) -> Result`.
   - Sale del bucle de `src/main.py` y reutiliza `_nearest`, `_credit`, `SCORE_BANDS`, `_prio_breaks` y `population.coverage`.
   - Añadir `Storage` (`LocalStorage` / `GCSStorage`), `WalkNetwork.to_npz/from_npz` y la **reescritura de `service_min_time`**.
   - Mantener un CLI local equivalente a `main.py` con renderizado folium para la memoria del TFM.
   - Antes de tocar nada, guardar un **snapshot** de las columnas del `main.py` actual en los 3 modos × 2 vistas y usarlo como test de regresión.
2. **Preparación de ciudades** (`quinzemin/prepare.py`):
   - catálogo nacional cacheado en `spain/`: GeoParquet del seccionado por provincia, `municipios.json` y `adrh_tablas.json`;
   - por municipio: secciones, población, servicios OSM, red → npz, GTFS opcional;
   - `config/cities/46250.yaml` conserva las fuentes municipales de València.
3. **API** (`api/app.py`, FastAPI):

   | Ruta | Función |
   |---|---|
   | `GET /api/health` | estado |
   | `GET /api/cities` | ciudades preparadas |
   | `GET /api/cities/{ine}` | metadatos de la ciudad |
   | `POST /api/cities/{ine}/prepare` | lanza el Job |
   | `GET /api/jobs/{id}` | estado y progreso |
   | `POST /api/runs` | recálculo (o resultado de caché) |
   | `GET /api/runs/{ine}/{hash}/config.yaml` | YAML reproducible en local |

   Añadir `docker-compose.yml` para desarrollo con `LocalStorage`.
4. **Frontend SPA** en `docs/`: `index.html` + `app.js` + `app.css`, sin build, Leaflet 1.9.4 desde cdnjs.
   - Carga instantánea de `data/cities/46250/` (precálculo network/800 m/score generado por `scripts/precompute.py`).
   - Panel: municipio (buscador y estado preparada/preparar), modo, umbral, vista, decay, servicios y pesos, GTFS, Recalcular y Descargar YAML.
   - Leyenda, selector Puntuación/Prioridad, tooltip y emojis desde zoom 14: portar el diseño de `_legend_html` del `main.py` actual.
   - Estados: calculando, en cola, preparando (progreso) y error.
   - URL compartible por hash.
   - `configurar.html` pasa a redirigir a la SPA; los `docs/mapa_*.html` se conservan.
5. **Infraestructura y CI**:
   - `DEPLOY.md` con la configuración inicial por `gcloud`: proyecto, presupuesto, Artifact Registry con limpieza, bucket con lifecycle de `runs/` a 30 días, cuenta de servicio con mínimos privilegios y Workload Identity Federation (sin claves JSON);
   - `.github/workflows/deploy-api.yml` (build → push → `gcloud run deploy` + `gcloud run jobs update`) y `tests.yml`.
6. **Documentación**: README (uso web y atribuciones INE/OSM/GTFS) y las secciones nuevas en DOCUMENTACIO (catalán) y TECHNICAL_DOC.

## 7. Verificación

1. **Tests de regresión** del motor contra el snapshot. Diferencia esperada única: las 4 secciones que aporta la cartografía INE. También tests unitarios de `_credit`, `coverage`, el hash de parámetros y `_parse_ine` (incluida la decodificación latin-1).
2. **Rendimiento**: transit antes (47 min) y después (< 1 min); carga de `npz` frente a GraphML (27 s).
3. **Local**: `docker compose up`.
   - `POST /api/runs` de València network/score reproduce los datos de §4, y la 2.ª llamada sale de caché.
   - `python -m quinzemin.prepare 46078` (Burjassot, pequeño) genera el bundle completo.
4. **Frontend**: `python -m http.server` sobre `docs/` contra la API local. Capturas con Chrome headless (`chrome --headless=new --screenshot --virtual-time-budget=10000`) de la carga inicial, un recálculo, la vista de prioridad y la preparación de una ciudad, sin errores en la consola (`--enable-logging=stderr`).
5. **Nube**: smoke test de `/api/health` y `/api/runs` tras el primer deploy; preparar un municipio pequeño desde la web; comprobar en facturación que el consumo sigue en la capa gratuita.
