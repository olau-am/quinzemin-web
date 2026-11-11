"""
El motor nuevo debe reproducir el main.py original (tests/fixtures/legacy/, generado con
scripts/snapshot_legacy.py) sobre los mismos datos de entrada (legacy/workdir/data/).
"""
import copy
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from quinzemin import bundle as qb
from quinzemin import engine

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "legacy" / "workdir" / "data"
FIX = ROOT / "tests" / "fixtures" / "legacy"

pytestmark = [
    pytest.mark.data,
    pytest.mark.skipif(not (DATA / "areas.geojson").exists(), reason="faltan legacy/workdir/data"),
]

CASES = [(m, v) for m in engine.MODES for v in engine.VIEWS]
_EMPTY_NAMES = {"nan", "None", ""}


@pytest.fixture(scope="module")
def cfg():
    return yaml.safe_load((ROOT / "legacy" / "config.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def bundle(cfg):
    return qb.load_legacy(cfg, DATA)


def _params(cfg, mode, view):
    c = copy.deepcopy(cfg)
    c["analysis"]["mode"] = mode
    c["map"]["view"] = view
    return qb.params_from_config(c)


def _norm_names(s: pd.Series) -> pd.Series:
    s = s.fillna("—").astype(str)
    return s.where(~s.isin(_EMPTY_NAMES), "—")


@pytest.mark.parametrize("mode,view", CASES, ids=[f"{m}-{v}" for m, v in CASES])
def test_reproduce_legacy(bundle, cfg, mode, view):
    t0 = time.perf_counter()
    res = engine.compute(bundle, _params(cfg, mode, view))
    elapsed = time.perf_counter() - t0
    print(f"\n{mode}/{view}: {elapsed:.1f} s")

    snap = pd.read_csv(FIX / f"{mode}_{view}.csv", dtype={"cusec": str}, keep_default_na=True)
    got = res.table.reset_index(drop=True)
    assert len(got) == len(snap)
    assert (got["cusec"] == snap["cusec"]).all()

    cols = [c for c in snap.columns
            if c.startswith(("dist_", "cred_", "rodeo_")) or c.endswith("_ok")
            or c in ("score", "puntuacion", "poblacion", "prioridad", "snap_m")]
    for c in cols:
        assert c in got.columns, c
        np.testing.assert_allclose(got[c].astype(float), snap[c].astype(float), rtol=0, atol=1e-9,
                                   equal_nan=True, err_msg=c)
    for c in [c for c in snap.columns if c.startswith("nombre_")]:
        diff = _norm_names(got[c]) != _norm_names(snap[c])
        assert not diff.any(), f"{c}: {diff.sum()} diferencias, p. ej. fila {diff.idxmax()}"

    cov = pd.read_csv(FIX / f"{mode}_{view}_cobertura.csv").set_index("servicio")["pct_poblacion"]
    for sid, pct in res.stats["coverage"]["per_service"].items():
        assert round(pct, 2) == pytest.approx(cov[sid], abs=1e-9), sid
    assert round(res.stats["coverage"]["all"], 2) == pytest.approx(cov["_todos"], abs=1e-9)
    assert round(res.stats["coverage"]["none"], 2) == pytest.approx(cov["_ninguno"], abs=1e-9)


def test_npz_igual_que_graphml(cfg, tmp_path):
    from quinzemin.network import WalkNetwork

    utm = qb.load_legacy(cfg, DATA, network=False, transit=False).areas.crs
    a = WalkNetwork.load(str(DATA / "walk_graph.graphml"), utm)
    a.to_npz(tmp_path / "g.npz")
    b = WalkNetwork.from_npz(tmp_path / "g.npz")
    for k in ("_xy", "_u", "_v", "_w"):
        np.testing.assert_array_equal(getattr(a, k), getattr(b, k))
    assert b._utm == a._utm
