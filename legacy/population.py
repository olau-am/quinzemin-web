"""
Población por área de análisis (sección censal).

Fuente por defecto: INE — Atlas de Distribución de Renta de los Hogares (ADRH),
tabla "Indicadores demográficos" de la provincia, concepto "Población"
(Padrón continuo a 1 de enero). API JSON de INEbase (servicios.ine.es/wstempus).

  download_data.py → download()  → data/population.csv  (codigo, poblacion, anyo)
  main.py          → assign()    → Serie de habitantes alineada con el GeoDataFrame de áreas

Alternativa: si la capa de áreas ya incluye la población, basta con indicar
city.population.field y no se descarga nada.
"""
import json
import os

import numpy as np
import pandas as pd
import requests

POPULATION_CSV = "data/population.csv"
_HEADERS = {"User-Agent": "quinzemin/1.0 (ciudad-15-minutos)"}


def _parse_ine(rows: list, municipality: str) -> pd.DataFrame:
    """JSON de DATOS_TABLA (tip=AM) → DataFrame con las secciones del municipio."""
    out = []
    for r in rows:
        code = next((m.get("Codigo", "") for m in r.get("MetaData", [])
                     if len(m.get("Codigo", "")) == 10), "")
        if not code.startswith(municipality):
            continue
        data = [d for d in r.get("Data", []) if d.get("Valor") is not None]
        if not data:
            continue
        last = max(data, key=lambda d: d.get("Anyo", 0))
        out.append({"codigo": code, "poblacion": int(last["Valor"]), "anyo": last.get("Anyo")})
    return pd.DataFrame(out, columns=["codigo", "poblacion", "anyo"])


def download(cfg: dict, dest: str = POPULATION_CSV) -> None:
    pop_cfg = cfg.get("city", {}).get("population") or {}
    if not pop_cfg.get("url"):
        return
    resp = requests.get(pop_cfg["url"], headers=_HEADERS, timeout=300)
    resp.raise_for_status()
    # La API del INE responde en ISO-8859-1 sin declararlo
    rows = json.loads(resp.content.decode("latin-1"))
    df = _parse_ine(rows, str(pop_cfg["municipality"]))
    if df.empty:
        raise RuntimeError(f"La tabla no contiene secciones del municipio {pop_cfg['municipality']}")
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    df.to_csv(dest, index=False)
    years = ", ".join(str(y) for y in sorted(df["anyo"].dropna().unique()))
    print(f"✓ Población: {dest} ({len(df)} secciones, {fmt_int(df['poblacion'].sum())} hab., año {years})")


def _area_codes(areas, template: str) -> pd.Series:
    """Construye el código INE de cada área con la plantilla (campos convertidos a int si se puede)."""
    def fmt(row):
        vals = {}
        for k, v in row.items():
            try:
                vals[k] = int(v)
            except (TypeError, ValueError):
                vals[k] = v
        return template.format(**vals)
    return areas.drop(columns="geometry").apply(fmt, axis=1)


def assign(cfg: dict, areas) -> "pd.Series | None":
    """
    Devuelve una Serie (índice de `areas`) con los habitantes de cada área, o None si no
    hay población configurada/disponible. Las secciones partidas en varios polígonos
    (mismo código) se reparten de forma proporcional a la superficie de cada parte.
    """
    pop_cfg = cfg.get("city", {}).get("population") or {}
    if not pop_cfg:
        return None

    field = pop_cfg.get("field")
    if field:
        if field not in areas.columns:
            print(f"⚠ La capa de áreas no tiene el campo de población '{field}'")
            return None
        return pd.to_numeric(areas[field], errors="coerce").fillna(0)

    if not os.path.exists(POPULATION_CSV):
        print(f"⚠ No existe {POPULATION_CSV} — ejecuta download_data.py (sin datos de población)")
        return None

    table = pd.read_csv(POPULATION_CSV, dtype={"codigo": str})
    codes = _area_codes(areas, pop_cfg["area_code"])
    by_code = table.set_index("codigo")["poblacion"]

    # Reparto por superficie entre los polígonos que comparten código
    area_m2 = areas.geometry.area
    share = area_m2 / area_m2.groupby(codes).transform("sum")
    pop = codes.map(by_code) * share

    missing = codes[pop.isna()].unique()
    if len(missing):
        print(f"⚠ {len(missing)} áreas sin dato de población (se cuentan como 0): "
              f"{', '.join(missing[:10])}{'…' if len(missing) > 10 else ''}")
    orphan = table[~table["codigo"].isin(set(codes))]
    if len(orphan):
        print(f"⚠ {len(orphan)} secciones del INE sin polígono en la capa de áreas "
              f"({fmt_int(orphan['poblacion'].sum())} hab. fuera del análisis): "
              f"{', '.join(orphan['codigo'])}")
    years = sorted(table["anyo"].dropna().astype(int).unique())
    pop.attrs["year"] = years[-1] if years else None
    return pop.fillna(0)


def fmt_int(n: float) -> str:
    """Entero con separador de miles español (829.010)."""
    return f"{int(round(n)):,}".replace(",", ".")


def fmt_pct(x: float) -> str:
    return f"{x:.1f}".replace(".", ",") + " %"


def coverage(pop, ok: dict, score=None) -> dict:
    """
    Indicadores de cobertura ponderados por población.
      ok    → {svc_id: Serie booleana (acceso dentro del umbral)}
      score → Serie de puntuación 0–100 (opcional)
    """
    total = float(pop.sum())
    if total <= 0:
        return {}
    w = pop / total
    per_svc = {k: float((w * v.astype(float)).sum() * 100) for k, v in ok.items()}
    all_ok = np.logical_and.reduce([v.to_numpy(bool) for v in ok.values()]) if ok else None
    res = {
        "total": total,
        "per_service": per_svc,
        "all": float(w[all_ok].sum() * 100) if all_ok is not None else 0.0,
        "none": float(w[~np.logical_or.reduce([v.to_numpy(bool) for v in ok.values()])].sum() * 100)
                if ok else 0.0,
    }
    if score is not None:
        res["mean_score"] = float((w * score).sum())
    return res
