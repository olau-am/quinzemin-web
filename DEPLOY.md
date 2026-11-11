# Despliegue

Dos piezas independientes, las dos se actualizan solas con cada push a `main`:

| Pieza | Dónde | Qué sirve | Cómo se despliega |
|---|---|---|---|
| **Web** | GitHub Pages (`main`, carpeta `/docs`) → <https://olau-am.github.io/quinzemin-web/> | mapa Leaflet + València precalculada | GitHub la publica en 1–2 min |
| **API** | Google Cloud Run, servicio `quinzemin-api`, región `europe-southwest1` (Madrid) | recálculo y preparación de ciudades (fases 2–3) | despliegue continuo de Cloud Run: Cloud Build construye el `Dockerfile` de la raíz y publica una revisión nueva |

## Estado actual (11/11/2026)

- ✅ Web publicada con València (modo red peatonal, 800 m, vista puntuación).
- ✅ `quinzemin-api` desplegado desde el repo; `GET /api/health` responde `{"status":"ok",...}`.
- ⏳ Pendiente para las fases 2–3 (ver más abajo): bucket, Job de preparación, cuentas de servicio,
  presupuesto y limpieza de imágenes.

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

## Pendiente (antes de la fase 3)

Todo en `europe-southwest1`. Se puede hacer desde la consola; entre paréntesis, la opción concreta.

1. **Presupuesto con alerta a 1 €**
   (*Facturación → Presupuestos y alertas → Crear*: ámbito el proyecto, importe 1 €, alertas al 50/90/100 %).
   Solo avisa por correo: no corta el servicio. El límite real lo ponen las 2 instancias máximas y los
   límites por IP de la API.

2. **Limpieza de imágenes** (*Artifact Registry → `cloud-run-source-deploy` → Editar → Políticas de
   limpieza*): una política *Conservar las versiones más recientes* = 2 y otra *Eliminar* con antigüedad
   > 1 día. La capa gratuita es de 0,5 GB y cada imagen ocupará unos 0,6–0,8 GB.

3. **Bucket de Cloud Storage** (*Cloud Storage → Crear*): región `europe-southwest1`, clase Standard,
   control de acceso uniforme, prevención de acceso público activada y *eliminación no definitiva*
   (soft delete) desactivada. En *Ciclo de vida*, una regla *Eliminar objeto* con antigüedad 30 días y
   prefijos `runs/` y `jobs/`. Guardará el catálogo nacional (`spain/`), los bundles de cada ciudad
   (`cities/{ine}/`), la caché de resultados (`runs/`) y el estado de las preparaciones (`jobs/`).
   En Madrid no hay capa gratuita de almacenamiento, pero con 1–3 GB son céntimos al mes; tenerlo en la
   misma región que Cloud Run evita pagar tráfico entre continentes.

4. **Cuentas de servicio** (*IAM → Cuentas de servicio*):
   - `quinzemin-api`: identidad del servicio. Permisos: *Usuario de objetos de Storage*
     (`roles/storage.objectUser`) sobre el bucket y *Ejecutor de jobs de Cloud Run con anulaciones*
     (`roles/run.jobsExecutorWithOverrides`) sobre el Job.
   - `quinzemin-prepare`: identidad del Job. Permiso: `roles/storage.objectUser` sobre el bucket.
   - Después, en el servicio `quinzemin-api` → *Editar e implementar nueva revisión → Seguridad*, elegir
     la cuenta `quinzemin-api`.

5. **Job `quinzemin-prepare`** (*Cloud Run → Jobs → Crear*): la misma imagen que el servicio, comando
   `python -m quinzemin.prepare`, 2 vCPU, 4 GiB, tiempo de espera 3600 s, 0 reintentos, cuenta de
   servicio `quinzemin-prepare`. Se crea cuando exista `prepare.py` (fase 2). El despliegue continuo solo
   actualiza el servicio, así que habrá que añadir un `cloudbuild.yaml` que actualice también el Job.

6. **Variables de entorno del servicio** (*Editar e implementar nueva revisión → Variables*), cuando la
   API las use (fase 3):

   | Variable | Valor |
   |---|---|
   | `QM_STORAGE` | `gcs` |
   | `QM_BUCKET` | nombre del bucket |
   | `QM_REGION` | `europe-southwest1` |
   | `QM_PREPARE_JOB` | `quinzemin-prepare` |
   | `QM_ALLOWED_ORIGINS` | `https://olau-am.github.io` |

## Costes a vigilar

- Cloud Run: capa gratuita mensual de 2 M de peticiones, 180.000 vCPU·s y 360.000 GiB·s (Tier 1).
  El tráfico de salida hacia Europa no es gratis, pero con respuestas de ~100–300 KB son céntimos.
- Cloud Build: 2.500 min de compilación gratis al mes; cada despliegue tarda unos 3–5 min.
- Cloud Storage en Madrid y Artifact Registry por encima de 0,5 GB: céntimos.
- Revisar de vez en cuando *Facturación → Informes* filtrando por el proyecto.
