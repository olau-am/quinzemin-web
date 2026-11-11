# Despliegue en Google Cloud (alta inicial)

Configuración que se hace **una sola vez** a mano con `gcloud`. Después, cada push a `main` despliega
solo con GitHub Actions (`.github/workflows/deploy-api.yml`, fase 5).

Resultado:

| Recurso | Nombre | Notas |
|---|---|---|
| Proyecto | `$PROJECT_ID` | con cuenta de facturación y presupuesto con alerta a 1 € |
| Artifact Registry | `quinzemin` (Docker, `europe-southwest1`) | limpieza automática: se quedan las 2 últimas imágenes |
| Bucket | `gs://$BUCKET` (`europe-southwest1`) | privado; `runs/` y `jobs/` se borran a los 30 días |
| Cloud Run service | `quinzemin-api` | 2 vCPU / 4 GiB, máx. 2 instancias, timeout 15 min, público |
| Cloud Run Job | `quinzemin-prepare` | 2 vCPU / 4 GiB, hasta 1 h, sin reintentos |
| Cuentas de servicio | `quinzemin-api`, `quinzemin-prepare`, `github-deployer` | mínimos privilegios |
| Workload Identity Federation | pool `github`, proveedor `quinzemin-web` | GitHub Actions sin claves JSON |

Todo en **`europe-southwest1`** (Madrid): es región Tier 1 de Cloud Run, así que entra en su capa
gratuita, y queda cerca de los usuarios. El bucket va en la misma región que Cloud Run aunque los 5 GB
gratis de Cloud Storage solo existan en regiones de EE. UU.: la API y el Job leen los bundles (la red
peatonal ocupa decenas de MB por ciudad) en cada arranque en frío, y el tráfico entre continentes
costaría más que guardar los datos en Madrid (~1–3 GB, céntimos al mes).

> Los comandos son para **Git Bash** (usan variables de shell y heredocs `<<'EOF'`, que en PowerShell
> no funcionan igual). Ejecútalos todos en la misma terminal.

---

## 0. Instalar y autenticar gcloud

```bash
winget install --id Google.CloudSDK -e      # después, abrir una terminal nueva
gcloud auth login                           # abre el navegador
gcloud components update
```

## 1. Variables

Elige un `PROJECT_ID` (6–30 caracteres, minúsculas, dígitos y guiones, **único en todo Google Cloud**;
no se puede cambiar después) y un nombre de bucket (también único global).

```bash
PROJECT_ID=quinzemin-web            # cámbialo si ya está cogido
REGION=europe-southwest1    # Madrid
BUCKET=${PROJECT_ID}-data
GITHUB_REPO=olau-am/quinzemin-web   # owner/repo en GitHub
```

Guarda estas líneas: los pasos siguientes las usan (si cierras la terminal, vuelve a pegarlas).

## 2. Proyecto y facturación

```bash
gcloud projects create $PROJECT_ID --name="quinzemin"
gcloud config set project $PROJECT_ID
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
```

Cloud Run exige una **cuenta de facturación** aunque el uso quede dentro de la capa gratuita. Si no
tienes ninguna, créala en la consola (pide tarjeta): <https://console.cloud.google.com/billing/create>.

```bash
gcloud billing accounts list                     # copia el ACCOUNT_ID (XXXXXX-XXXXXX-XXXXXX)
BILLING=XXXXXX-XXXXXX-XXXXXX
gcloud billing projects link $PROJECT_ID --billing-account=$BILLING
```

## 3. Activar las APIs

```bash
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  storage.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  cloudresourcemanager.googleapis.com \
  billingbudgets.googleapis.com
```

## 4. Presupuesto con alerta a 1 €

Un presupuesto **solo avisa por correo, no corta el servicio**. El límite real lo ponen
`max-instances=2`, la cola y los límites por IP de la API.

```bash
gcloud billing budgets create \
  --billing-account=$BILLING \
  --display-name="quinzemin 1 EUR" \
  --budget-amount=1EUR \
  --filter-projects=projects/$PROJECT_ID \
  --threshold-rule=percent=0.5 \
  --threshold-rule=percent=0.9 \
  --threshold-rule=percent=1.0
```

La moneda tiene que coincidir con la de la cuenta de facturación (si la tuya está en USD, pon `1USD`).

## 5. Artifact Registry con limpieza

La capa gratuita es de 0,5 GB y la imagen rondará 0,6–0,8 GB, así que guardar 2 versiones puede costar
algún céntimo al mes.

```bash
gcloud artifacts repositories create quinzemin \
  --repository-format=docker --location=$REGION \
  --description="Imágenes de quinzemin-web"

cat > ar-cleanup.json <<'EOF'
[
  {"name": "conservar-2-ultimas", "action": {"type": "Keep"},
   "mostRecentVersions": {"keepCount": 2}},
  {"name": "borrar-resto", "action": {"type": "Delete"},
   "condition": {"tagState": "any", "olderThan": "1d"}}
]
EOF
gcloud artifacts repositories set-cleanup-policies quinzemin \
  --location=$REGION --policy=ar-cleanup.json --no-dry-run
rm ar-cleanup.json
```

(La regla `Keep` prevalece sobre la `Delete`.)

## 6. Bucket de Cloud Storage

Privado; la API es quien lee y sirve los datos.

```bash
gcloud storage buckets create gs://$BUCKET \
  --location=$REGION --default-storage-class=STANDARD \
  --uniform-bucket-level-access --public-access-prevention

# Sin "soft delete" (por defecto guarda 7 días lo borrado y eso también ocupa)
gcloud storage buckets update gs://$BUCKET --clear-soft-delete

# Los resultados cacheados (runs/) y el estado de los jobs (jobs/) caducan a los 30 días
cat > gcs-lifecycle.json <<'EOF'
{"rule": [{"action": {"type": "Delete"},
           "condition": {"age": 30, "matchesPrefix": ["runs/", "jobs/"]}}]}
EOF
gcloud storage buckets update gs://$BUCKET --lifecycle-file=gcs-lifecycle.json
rm gcs-lifecycle.json
```

## 7. Cuentas de servicio

```bash
gcloud iam service-accounts create quinzemin-api     --display-name="quinzemin API (Cloud Run)"
gcloud iam service-accounts create quinzemin-prepare --display-name="quinzemin preparar ciudades (Job)"
gcloud iam service-accounts create github-deployer   --display-name="GitHub Actions deploy"

SA_API=quinzemin-api@$PROJECT_ID.iam.gserviceaccount.com
SA_PREP=quinzemin-prepare@$PROJECT_ID.iam.gserviceaccount.com
SA_DEPLOY=github-deployer@$PROJECT_ID.iam.gserviceaccount.com
```

Permisos en tiempo de ejecución: la API y el Job leen y escriben objetos **solo en el bucket**
(`objectUser` incluye crear con la precondición `ifGenerationMatch=0` que usa el lock).

```bash
for SA in $SA_API $SA_PREP; do
  gcloud storage buckets add-iam-policy-binding gs://$BUCKET \
    --member=serviceAccount:$SA --role=roles/storage.objectUser
done
```

Permisos del despliegue (GitHub Actions): subir imágenes, desplegar el servicio y el Job, y asignarles
sus cuentas de servicio.

```bash
gcloud artifacts repositories add-iam-policy-binding quinzemin --location=$REGION \
  --member=serviceAccount:$SA_DEPLOY --role=roles/artifactregistry.writer
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member=serviceAccount:$SA_DEPLOY --role=roles/run.developer
for SA in $SA_API $SA_PREP; do
  gcloud iam service-accounts add-iam-policy-binding $SA \
    --member=serviceAccount:$SA_DEPLOY --role=roles/iam.serviceAccountUser
done
```

## 8. Workload Identity Federation (GitHub Actions sin claves)

Solo los workflows del repo `$GITHUB_REPO` en la rama `main` pueden actuar como `github-deployer`.

```bash
gcloud iam workload-identity-pools create github \
  --location=global --display-name="GitHub Actions"

gcloud iam workload-identity-pools providers create-oidc quinzemin-web \
  --location=global --workload-identity-pool=github \
  --display-name="quinzemin-web" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository=='$GITHUB_REPO' && assertion.ref=='refs/heads/main'"

gcloud iam service-accounts add-iam-policy-binding $SA_DEPLOY \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$GITHUB_REPO"

echo "GCP_WIF_PROVIDER=projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/quinzemin-web"
```

## 9. Crear el servicio y el Job con imágenes de ejemplo

Así ya existen con su configuración (y la URL pública) antes de tener la imagen propia. El workflow de
GitHub solo cambiará la imagen.

```bash
ALLOWED_ORIGINS=https://olau-am.github.io

gcloud run deploy quinzemin-api \
  --image=us-docker.pkg.dev/cloudrun/container/hello \
  --region=$REGION --service-account=$SA_API \
  --allow-unauthenticated \
  --cpu=2 --memory=4Gi --timeout=900 \
  --min-instances=0 --max-instances=2 \
  --set-env-vars=QM_STORAGE=gcs,QM_BUCKET=$BUCKET,QM_REGION=$REGION,QM_PREPARE_JOB=quinzemin-prepare,QM_ALLOWED_ORIGINS=$ALLOWED_ORIGINS

gcloud run jobs create quinzemin-prepare \
  --image=us-docker.pkg.dev/cloudrun/container/job \
  --region=$REGION --service-account=$SA_PREP \
  --cpu=2 --memory=4Gi --task-timeout=3600 --max-retries=0 --tasks=1 \
  --set-env-vars=QM_STORAGE=gcs,QM_BUCKET=$BUCKET
```

- Facturación **por petición** (la opción por defecto): solo hay CPU mientras una petición está abierta,
  y es la que entra en la capa gratuita (2 M de peticiones, 180.000 vCPU·s y 360.000 GiB·s al mes).
- Las variables `QM_*` son el contrato con el código: la API las leerá en la fase 3.

La API lanza el Job con parámetros (el código INE del municipio), así que su cuenta necesita ejecutarlo
con *overrides*, solo sobre ese Job:

```bash
gcloud run jobs add-iam-policy-binding quinzemin-prepare --region=$REGION \
  --member=serviceAccount:$SA_API --role=roles/run.jobsExecutorWithOverrides
```

Comprobación:

```bash
API_URL=$(gcloud run services describe quinzemin-api --region=$REGION --format='value(status.url)')
echo $API_URL
curl -s $API_URL | head -5          # página "Congratulations" de la imagen de ejemplo
gcloud run jobs execute quinzemin-prepare --region=$REGION --wait
```

## 10. Variables del repositorio en GitHub

En GitHub → repo → *Settings → Secrets and variables → Actions → Variables* (no son secretos: con WIF
no hay ninguna clave que guardar):

| Variable | Valor |
|---|---|
| `GCP_PROJECT_ID` | `$PROJECT_ID` |
| `GCP_REGION` | `europe-southwest1` |
| `GCP_WIF_PROVIDER` | la línea que imprimió el paso 8 |
| `GCP_DEPLOY_SA` | `github-deployer@$PROJECT_ID.iam.gserviceaccount.com` |
| `GCS_BUCKET` | `$BUCKET` |
| `API_URL` | la URL del paso 9 (la usa el frontend) |

---

## Costes a vigilar

- **Tráfico de salida**: la capa gratuita de Cloud Run solo cubre 1 GiB al mes dentro de Norteamérica.
  Las respuestas a navegadores de Europa se cobran, pero con resultados de ~100–300 KB serían céntimos.
- **Cloud Storage** en Madrid: sin capa gratuita; se paga el almacenamiento (céntimos para unos pocos GB).
- **Artifact Registry**: lo que pase de 0,5 GB (ver paso 5).
- Revisar de vez en cuando *Facturación → Informes* filtrando por el proyecto.

## Desarrollo local contra GCS (opcional)

```bash
gcloud auth application-default login     # credenciales para google-cloud-storage en local
```

Con `QM_STORAGE=local` (por defecto en `docker-compose.yml`) no hace falta nada de Google Cloud.
