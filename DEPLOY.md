# Despliegue

Dos piezas independientes, las dos se actualizan solas con cada push a `main`:

| Pieza | Dónde | Qué sirve | Cómo se despliega |
|---|---|---|---|
| **Web** | GitHub Pages (`main`, carpeta `/docs`) → <https://olau-am.github.io/quinzemin-web/> | mapa Leaflet + València precalculada | GitHub la publica en 1–2 min |
| **API** | Google Cloud Run, servicio `quinzemin-api`, región `europe-southwest1` (Madrid) | recálculo y preparación de ciudades (fases 2–3) | despliegue continuo de Cloud Run: Cloud Build construye el `Dockerfile` de la raíz y publica una revisión nueva |

## Estado actual (11/11/2026)

- ✅ Web publicada con València (modo red peatonal, 800 m, vista puntuación).
- ✅ `quinzemin-api` desplegado desde el repo; `GET /api/health` responde `{"status":"ok",...}`.
- ⏳ Pendiente: los pasos de «Puesta en marcha de la API completa» (bucket, Job, cuentas de servicio,
  variables, catálogo) y, aparte, presupuesto y limpieza de imágenes.

## Web (GitHub Pages)

Configuración: repo → *Settings → Pages* → *Deploy from a branch*, rama `main`, carpeta `/docs`.
`docs/.nojekyll` hace que se sirvan los ficheros tal cual.

Para regenerar los datos precalculados de València (tras cambiar el motor o los datos):

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/precompute.py   # → docs/data/cities/46250/
```

Probar en local: `.venv/Scripts/python -m http.server 8000 -d docs` y abrir <http://localhost:8000>.

## API (Cloud Run)

Configuración del servicio `quinzemin-api` (consola de Cloud Run):

| Ajuste | Valor |
|---|---|
| Origen | repositorio `olau-am/quinzemin-web`, rama `main`, tipo de compilación **Dockerfile** (`/Dockerfile`) |
| Región | `europe-southwest1` (Madrid; Tier 1, entra en la capa gratuita) |
| Autenticación | pública (sin autenticar) |
| Facturación | basada en solicitudes (solo hay CPU mientras se atiende una petición) |
| Instancias | mín. 0, máx. 2 |
| Recursos | 2 vCPU, 4 GiB |
| Tiempo de espera | 900 s |

La imagen la construye Cloud Build y la guarda en Artifact Registry, en el repositorio
`cloud-run-source-deploy` de la región.

Probar el contenedor en local (necesita Docker Desktop):

```bash
docker build -t quinzemin .
docker run --rm -p 8080:8080 quinzemin      # http://localhost:8080/api/health
```

Sin Docker: `.venv/Scripts/python -m uvicorn api.app:app --reload` (puerto 8000).

## Puesta en marcha de la API completa (fase 3)

Todo en la consola de Google Cloud y en `europe-southwest1`. En orden: cada paso usa lo anterior.

### 1. Bucket de Cloud Storage
*Cloud Storage → Buckets → Crear*:
- nombre: el que quieras (único en todo Google Cloud), p. ej. `quinzemin-datos-<algo>`;
- ubicación: **Región** `europe-southwest1`; clase **Standard**;
- control de acceso **uniforme** y **prevención de acceso público** activada;
- protección de datos: desmarcar **eliminación no definitiva** (soft delete).

Después, en el bucket → *Ciclo de vida → Añadir regla*: acción **Eliminar objeto**, condición
**Antigüedad 30 días** y **Coincide con el prefijo** `runs/` y `jobs/`.

Guardará `spain/` (catálogo nacional), `cities/{ine}/` (ciudades), `runs/` (caché de resultados),
`jobs/` (estado de las preparaciones) y `locks/`.

### 2. Cuentas de servicio
*IAM y administración → Cuentas de servicio → Crear*:
- `quinzemin-api` (identidad de la API) y `quinzemin-prepare` (identidad del Job).

En el bucket → *Permisos → Conceder acceso*: las dos cuentas con el rol
**Usuario de objetos de Storage** (`roles/storage.objectUser`).

### 3. Job `quinzemin-prepare`
*Cloud Run → Jobs → Crear trabajo*:
- imagen: la que usa ahora el servicio (*Cloud Run → quinzemin-api → Revisiones*, copiar la URL de
  la imagen de la última revisión); cada push la actualizará (paso 6);
- nombre `quinzemin-prepare`, región `europe-southwest1`;
- *Contenedor → Comando* `quinzemin-job` (argumentos vacíos: la API pasa el id del trabajo);
- recursos **2 vCPU** y **4 GiB**; tiempo de espera de la tarea **3600 s**; **0** reintentos; 1 tarea;
- *Variables*: `QM_STORAGE=gcs`, `QM_BUCKET=<bucket>`;
- *Seguridad → Cuenta de servicio*: `quinzemin-prepare`.

En el Job → *Permisos → Añadir principal*: `quinzemin-api` con el rol
**Ejecutor de trabajos de Cloud Run con anulaciones** (`roles/run.jobsExecutorWithOverrides`).

### 4. Servicio `quinzemin-api`
*Cloud Run → quinzemin-api → Editar e implementar nueva revisión*:
- *Variables y secretos*:

  | Variable | Valor |
  |---|---|
  | `QM_STORAGE` | `gcs` |
  | `QM_BUCKET` | el bucket del paso 1 |
  | `QM_REGION` | `europe-southwest1` |
  | `QM_PREPARE_JOB` | `quinzemin-prepare` |
  | `QM_ALLOWED_ORIGINS` | `https://olau-am.github.io` |

- *Seguridad → Cuenta de servicio*: `quinzemin-api`.

### 5. Catálogo nacional (una vez)
*Cloud Run → Jobs → quinzemin-prepare → Ejecutar con anulaciones* → argumentos: `catalog`.
Tarda ~2 min (descarga la cartografía del INE, la población de las 52 provincias y el catálogo de
feeds GTFS). Comprobar después que en el bucket existe `spain/municipios.json`.

### 6. Despliegue de servicio y Job a la vez
El despliegue continuo que creó la consola solo actualiza el servicio. Para que el Job use siempre
la misma imagen: *Cloud Build → Activadores* → el activador de `quinzemin-web` → *Editar* →
*Configuración*: **Archivo de configuración de Cloud Build**, ubicación `cloudbuild.yaml`.

La cuenta de servicio del activador (se ve en el propio activador) necesita, si no los tiene:
**Desarrollador de Cloud Run** (`roles/run.developer`), **Escritor de Artifact Registry** y
**Usuario de cuenta de servicio** sobre `quinzemin-api` y `quinzemin-prepare`.

### 7. Preparar València en la nube
En `https://<URL-del-servicio>/docs` (documentación interactiva de la API) →
`POST /api/cities/{ine}/prepare` → *Try it out* → `ine` = `46250` → *Execute*. Devuelve un trabajo;
su progreso se ve en `GET /api/jobs/{id}` (15–20 min, casi todo esperando a Overpass).

### Comprobación
- `GET /api/health` → `"storage": "gcs"`.
- `GET /api/cities` → València.
- `POST /api/runs` con `{"ine": "46250", "mode": "network"}` → resultado; la segunda vez, cabecera
  `X-Cache: hit`.
- *Facturación → Informes*: el consumo sigue en la capa gratuita.

## Costes a vigilar

- Cloud Run: capa gratuita mensual de 2 M de peticiones, 180.000 vCPU·s y 360.000 GiB·s (Tier 1).
  El tráfico de salida hacia Europa no es gratis, pero con respuestas de ~100–300 KB son céntimos.
- Cloud Build: 2.500 min de compilación gratis al mes; cada despliegue tarda unos 3–5 min.
- Cloud Storage en Madrid y Artifact Registry por encima de 0,5 GB: céntimos.
- Revisar de vez en cuando *Facturación → Informes* filtrando por el proyecto.
