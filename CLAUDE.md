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
| `quinzemin/` | paquete: `network`, `gtfs`, `population`, `download`, `config` copiados de `quinzemin/src`; aquí irán `engine.py`, `storage.py` y `prepare.py` |
| `config/cities/46250.yaml` | configuración de València (fuentes municipales), antes `config.yaml` |
| `legacy/` | copia **congelada** del `src/` original para el snapshot de regresión; no editar |
| `scripts/snapshot_legacy.py` | genera `tests/fixtures/legacy/` (3 modos × 2 vistas) |
| `tests/` | pytest; `tests/fixtures/legacy/` = resultados de referencia del código original |
| `data/` | datos descargados y bundles locales (gitignored) |

Pendiente según las fases del handoff (§6): `api/`, `docs/` (SPA), `Dockerfile`, `docker-compose.yml`,
`DEPLOY.md` y `.github/workflows/deploy-api.yml`.

## Repo original

`c:\Users\olau0\Documents\GitHub\quinzemin` (GitHub `olau-am/quinzemin`). Tiene cambios sin commitear
(vista PUNTUACIÓN y `src/population.py`) y `docs/index.html` sobrescrito por una prueba en transit:
**no commitear allí sin preguntar al usuario**.
