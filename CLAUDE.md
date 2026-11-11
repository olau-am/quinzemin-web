# quinzemin-web

Versión web de **quinzemin** (TFM, ciudad de los 15 minutos): motor de accesibilidad por sección censal +
API FastAPI en Cloud Run + SPA Leaflet en GitHub Pages. **El plan completo, las decisiones ya tomadas con
el usuario y los datos verificados están en [HANDOFF_WEB.md](HANDOFF_WEB.md): leerlo antes de trabajar y
no volver a preguntar lo que ya está decidido (§2).**

## Convenciones

- Idioma del usuario, del código (comentarios, mensajes) y de la documentación: **español**.
  La memoria del TFM (DOCUMENTACIO.md, en el repo original) está en catalán.
- Windows 11 · Python 3.13 en `.venv/` · Git Bash y PowerShell. La consola usa cp1252: ejecutar los
  scripts con `PYTHONIOENCODING=utf-8` (imprimen ✓/⚠).
- Instalar: `.venv/Scripts/python -m pip install -e ".[prepare,api,viz,dev]"`.
- Tests: `.venv/Scripts/python -m pytest` (marcas `data` = necesita datos locales; `slow` = > 1 min).

## Estructura

| Ruta | Qué es |
|---|---|
| `quinzemin/engine.py` | motor: `compute(Bundle, Params) → Result` (tabla por área + `stats`) |
| `quinzemin/bundle.py` | `load_legacy()` (datos del proyecto original) y `params_from_config()` |
| `quinzemin/network.py`, `gtfs.py` | red peatonal (`to_npz`/`from_npz`) y grafo GTFS vectorizado |
| `quinzemin/population.py`, `download.py`, `config.py` | población INE, descargas, YAML |
| `quinzemin/cli.py`, `render_folium.py` | CLI local con mapa folium (memoria del TFM) |
| `api/app.py` | FastAPI (de momento solo `/api/health`) |
| `docs/` | web en GitHub Pages: `index.html`, `app.js`, `app.css`, `data/cities/46250/` |
| `config/cities/46250.yaml` | configuración de València (fuentes municipales), antes `config.yaml` |
| `legacy/` | copia **congelada** del `src/` original para el snapshot de regresión; no editar |
| `scripts/snapshot_legacy.py` | genera `tests/fixtures/legacy/` (3 modos × 2 vistas) |
| `scripts/precompute.py` | genera `docs/data/cities/46250/` |
| `tests/` | pytest; `test_regression_legacy.py` compara el motor con el snapshot |
| `data/` | datos descargados, bundles y salidas locales (gitignored) |

Despliegue (ver [DEPLOY.md](DEPLOY.md)): web en GitHub Pages (`/docs`); API en Cloud Run
`quinzemin-api`, **`europe-southwest1`**, con despliegue continuo de Cloud Run desde el repo
(Cloud Build + `Dockerfile`), **no** GitHub Actions + WIF como decía el handoff.

Fase 1 cerrada. Pendiente: `storage.py` (Local/GCS, con la API), `prepare.py` (fase 2), resto de la API
y `docker-compose.yml` (fase 3), panel de configuración de la web (fase 4).

## Repo original

`c:\Users\olau0\Documents\GitHub\quinzemin` (GitHub `olau-am/quinzemin`). Tiene cambios sin commitear
(vista PUNTUACIÓN y `src/population.py`) y `docs/index.html` sobrescrito por una prueba en transit:
**no commitear allí sin preguntar al usuario**.
