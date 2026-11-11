# legacy/

Copia **congelada** de `quinzemin/src` (repo `olau-am/quinzemin`, estado del 11/11/2026 con los cambios
sin commitear de la vista PUNTUACIÓN) y de su `config.yaml`. **No se modifica**: solo sirve para
generar el snapshot de regresión del motor nuevo (`scripts/snapshot_legacy.py`).

Único cambio respecto al original: `config.py` lee `legacy/config.yaml` en lugar de `../config.yaml`.

## Datos de entrada

`legacy/workdir/data/` (gitignored, ~93 MB) es una copia de `quinzemin/data/` con los ficheros que usa
`main.py`. Sus sumas SHA-256 están en `tests/fixtures/legacy/INPUTS.sha256`. Para recrearla:

```bash
mkdir -p legacy/workdir/data
for f in areas.geojson population.csv salud.geojson primaria.geojson secundaria.geojson \
         transporte.geojson supermercados.geojson zonas_verdes.geojson cultura.geojson \
         farmacias.geojson gtfs_metro.zip gtfs_emt.zip walk_graph.graphml; do
  cp -p ../quinzemin/data/$f legacy/workdir/data/
done
(cd legacy/workdir/data && sha256sum -c ../../../tests/fixtures/legacy/INPUTS.sha256)
```

## Snapshot

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/snapshot_legacy.py   # 3 modos × 2 vistas
```

Resultados en `tests/fixtures/legacy/`. Las áreas son los 589 polígonos del geoportal municipal; la
columna `cusec` (10 dígitos) permite compararlas con el seccionado nacional del INE, que añadirá las
4 secciones huérfanas (02044, 11024, 11037, 13011).
