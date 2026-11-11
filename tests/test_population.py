import json

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

from quinzemin import population


def _ine_row(code: str, valor, anyo=2024):
    return {
        "MetaData": [{"Codigo": code[:5]}, {"Codigo": code}],
        "Data": [] if valor is None else [{"Anyo": anyo - 1, "Valor": 1}, {"Anyo": anyo, "Valor": valor}],
    }


def test_parse_ine_filtra_municipio_y_toma_ultimo_anyo():
    rows = [
        _ine_row("4625001001", 1200),
        _ine_row("4625001002", None),      # fila con Data vacío
        _ine_row("4607801001", 900),       # otro municipio
    ]
    df = population._parse_ine(rows, "46250")
    assert df.to_dict("records") == [{"codigo": "4625001001", "poblacion": 1200, "anyo": 2024}]


def test_parse_ine_respuesta_latin1():
    # La API del INE responde en ISO-8859-1 sin declararlo
    payload = [{**_ine_row("4625001001", 10), "Nombre": "València sección 01001. Población. "}]
    raw = json.dumps(payload, ensure_ascii=False).encode("latin-1")
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    rows = json.loads(raw.decode("latin-1"))
    assert rows[0]["Nombre"].startswith("València")
    assert population._parse_ine(rows, "46250")["poblacion"].tolist() == [10]


def test_assign_reparte_secciones_partidas_por_superficie(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    pd.DataFrame({"codigo": ["4625015008", "4625001001"], "poblacion": [300, 100],
                  "anyo": [2024, 2024]}).to_csv("data/population.csv", index=False)
    areas = gpd.GeoDataFrame(
        {"coddistrit": [15, 15, 1], "codsecc": [8, 8, 1]},
        geometry=[box(0, 0, 2, 1), box(2, 0, 3, 1), box(0, 5, 1, 6)], crs="EPSG:25830",
    )
    cfg = {"city": {"population": {"area_code": "46250{coddistrit:02d}{codsecc:03d}"}}}
    pop = population.assign(cfg, areas)
    np.testing.assert_allclose(pop.to_numpy(), [200, 100, 100])
    assert pop.attrs["year"] == 2024


def test_coverage():
    pop = pd.Series([100.0, 300.0, 600.0])
    ok = {"a": pd.Series([True, True, False]), "b": pd.Series([True, False, False])}
    cov = population.coverage(pop, ok, score=pd.Series([100.0, 50.0, 0.0]))
    assert cov["total"] == 1000
    assert cov["per_service"] == pytest.approx({"a": 40.0, "b": 10.0})
    assert cov["all"] == pytest.approx(10.0)
    assert cov["none"] == pytest.approx(60.0)
    assert cov["mean_score"] == pytest.approx(25.0)


def test_formatos_es():
    assert population.fmt_int(829010) == "829.010"
    assert population.fmt_pct(54.62) == "54,6 %"
