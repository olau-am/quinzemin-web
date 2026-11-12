"""
Catálogo nacional (se genera una vez y se guarda en spain/ del almacenamiento):

  spain/secciones/{cpro}.parquet  secciones censales del INE de la provincia (EPSG:25830)
  spain/poblacion/{cpro}.csv      población por sección (codigo, poblacion, anyo), ADRH del INE
  spain/adrh_tablas.json          provincia → tabla "Indicadores demográficos" del ADRH
  spain/municipios.json           municipios: código, nombre, provincia, secciones, km², habitantes

  PYTHONIOENCODING=utf-8 .venv/Scripts/python -m quinzemin.catalog            # local (./data)
  QM_STORAGE=gcs QM_BUCKET=... python -m quinzemin.catalog                    # en el bucket

Fuentes (© INE): cartografía de secciones censales a 1 de enero (seccionado_{año}.zip) y el Atlas
de Distribución de Renta de los Hogares (operación 353), concepto "Población" (260:17338).
"""
from __future__ import annotations

import argparse
import io
import json
import tempfile
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

from quinzemin import population, storage as qs

SECCIONADO_YEAR = 2024
SECCIONADO_URL = "https://www.ine.es/prodyser/cartografia/seccionado_{year}.zip"
INE_API = "https://servicios.ine.es/wstempus/js/ES"
ADRH_OPERATION = 353
POP_FILTER = "tv=260:17338"          # concepto "Población"
HEADERS = {"User-Agent": "quinzemin/1.0 (ciudad-15-minutos)"}
SECTION_COLS = ["CUSEC", "CUMUN", "NMUN", "CPRO", "NPRO", "CCA", "NCA"]


def ine_json(path: str, timeout: int = 120, retries: int = 3):
    """GET a la API JSON del INE. Responde en UTF-8 o, en algunas rutas, en ISO-8859-1 sin declararlo."""
    last = None
    for i in range(retries):
        try:
            r = requests.get(f"{INE_API}/{path}", headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            try:
                return json.loads(r.content.decode("utf-8"))
            except UnicodeDecodeError:
                return json.loads(r.content.decode("latin-1"))
        except (requests.RequestException, ValueError) as e:
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"INE {path}: {last}")


# ----------------------------------------------------------------------
# Secciones censales
# ----------------------------------------------------------------------

def build_sections(st: qs.Storage, year: int = SECCIONADO_YEAR, zip_path: str | None = None) -> gpd.GeoDataFrame:
    if zip_path is None:
        tmp = Path(tempfile.gettempdir()) / f"seccionado_{year}.zip"
        if not tmp.exists():
            print(f"  Descargando cartografía de secciones {year} (~66 MB)...")
            with requests.get(SECCIONADO_URL.format(year=year), headers=HEADERS, stream=True, timeout=600) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
        zip_path = str(tmp)
    gdf = gpd.read_file(f"zip://{zip_path}")
    gdf = gdf[SECTION_COLS + ["geometry"]]
    for cpro, g in gdf.groupby("CPRO"):
        buf = io.BytesIO()
        g.reset_index(drop=True).to_parquet(buf)
        st.write_bytes(f"spain/secciones/{cpro}.parquet", buf.getvalue())
    print(f"✓ Secciones: {len(gdf)} en {gdf['CPRO'].nunique()} provincias → spain/secciones/")
    return gdf


# ----------------------------------------------------------------------
# Población (ADRH)
# ----------------------------------------------------------------------

def build_population(st: qs.Storage) -> dict:
    tables = [t for t in ine_json(f"TABLAS_OPERACION/{ADRH_OPERATION}")
              if "demogr" in t["Nombre"].lower()]
    print(f"  {len(tables)} tablas 'Indicadores demográficos' en el ADRH")
    mapping = {}
    for t in tables:
        rows = ine_json(f"DATOS_TABLA/{t['Id']}?nult=1&{POP_FILTER}&tip=AM")
        if not isinstance(rows, list):
            # La tabla nacional (todas las provincias) supera el límite de volumen de la API
            print(f"  · tabla {t['Id']}: {rows.get('status', rows)} (se ignora)")
            continue
        df = population._parse_ine(rows, "")
        if df.empty:
            print(f"  · tabla {t['Id']}: sin secciones (se ignora)")
            continue
        provs = df["codigo"].str[:2].unique()
        if len(provs) != 1:
            print(f"  · tabla {t['Id']}: varias provincias {sorted(provs)} (se ignora)")
            continue
        cpro = provs[0]
        st.write_bytes(f"spain/poblacion/{cpro}.csv", df.to_csv(index=False).encode("utf-8"))
        year = int(df["anyo"].max())
        mapping[cpro] = {"tabla": t["Id"], "anyo": year, "secciones": len(df),
                         "poblacion": int(df["poblacion"].sum())}
        print(f"  · {cpro}: tabla {t['Id']}, {len(df)} secciones, {population.fmt_int(df['poblacion'].sum())} hab. ({year})")
    st.write_json("spain/adrh_tablas.json", dict(sorted(mapping.items())), indent=1)
    print(f"✓ Población: {len(mapping)} provincias → spain/poblacion/")
    return mapping


# ----------------------------------------------------------------------
# Municipios
# ----------------------------------------------------------------------

def build_municipalities(st: qs.Storage, sections: gpd.GeoDataFrame) -> list[dict]:
    pops = []
    for key in st.list("spain/poblacion/"):
        pops.append(pd.read_csv(io.BytesIO(st.read_bytes(key)), dtype={"codigo": str}))
    pop = pd.concat(pops) if pops else pd.DataFrame(columns=["codigo", "poblacion", "anyo"])
    pop["cumun"] = pop["codigo"].str[:5]
    by_mun = pop.groupby("cumun").agg(poblacion=("poblacion", "sum"), anyo=("anyo", "max"))

    s = sections.copy()
    s["km2"] = s.geometry.area / 1e6
    mun = s.groupby("CUMUN").agg(nombre=("NMUN", "first"), cpro=("CPRO", "first"),
                                 provincia=("NPRO", "first"), secciones=("CUSEC", "nunique"),
                                 km2=("km2", "sum")).join(by_mun)
    out = [{"ine": ine, "nombre": r.nombre, "cpro": r.cpro, "provincia": r.provincia,
            "secciones": int(r.secciones), "km2": round(float(r.km2), 1),
            "poblacion": None if pd.isna(r.poblacion) else int(r.poblacion),
            "anyo": None if pd.isna(r.anyo) else int(r.anyo)}
           for ine, r in mun.iterrows()]
    out.sort(key=lambda m: (m["nombre"].lower(), m["ine"]))
    st.write_json("spain/municipios.json", out, separators=(",", ":"))
    print(f"✓ Municipios: {len(out)} → spain/municipios.json")
    return out


def build(st: qs.Storage, zip_path: str | None = None) -> None:
    t0 = time.perf_counter()
    sections = build_sections(st, zip_path=zip_path)
    build_population(st)
    build_municipalities(st, sections)
    print(f"Catálogo nacional listo en {time.perf_counter() - t0:.0f} s")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Genera el catálogo nacional (spain/)")
    ap.add_argument("--zip", help="seccionado_YYYY.zip ya descargado")
    a = ap.parse_args(argv)
    build(qs.from_env(), a.zip)


if __name__ == "__main__":
    main()
