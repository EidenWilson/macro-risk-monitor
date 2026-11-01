"""Shared fixtures for the Python unit tests (no network, no browser).

    pip install -r requirements-dev.txt
    pytest
"""
import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from macro_config import SERIES  # noqa: E402


def synthetic_raw(start="2000-01", end="2026-09", seed=7):
    """A wide monthly panel shaped like macro.db's observations, with every series the pipeline fetches.

    Prices are random walks with drift; growth has a few recessions; rates move slowly. Realistic enough for the
    model to find all four regimes and fit a 4-state HMM, and fully deterministic."""
    rng = np.random.default_rng(seed)
    idx = pd.period_range(start, end, freq="M").astype(str)
    n = len(idx)
    t = np.arange(n)
    cols = {}
    cycle = np.sin(t / 18.0)                                    # a business cycle of roughly nine years
    for sid, cfg in SERIES.items():
        if cfg["kind"] == "rate":
            base = {"EURIBOR_3M": 2.0, "YC_1Y": 2.0, "YC_3Y": 2.3, "US_1Y": 2.5, "CONF_DE": -10.0}.get(sid, 1.0)
            x = base + 1.5 * cycle + np.cumsum(rng.normal(0, 0.03, n))
        else:
            drift = 0.0015 if "HICP" in sid or "PPI" in sid else 0.0005
            shock = np.where((t > 260) & (t < 280), 0.02, 0.0) if ("ENERGY" in sid or "GAS" in sid or "ELEC" in sid) else 0
            growth = 0.003 * cycle if sid in ("IP_DE", "RETAIL_DE", "FOODSERV_DE") else 0
            x = 100 * np.exp(np.cumsum(drift + growth + shock + rng.normal(0, 0.006, n)))
            if sid == "EURUSD":
                x = 1.15 + 0.1 * cycle + np.cumsum(rng.normal(0, 0.005, n))
        cols[sid] = x
    df = pd.DataFrame(cols, index=pd.Index(idx, name="period"))
    # publication: slower series end earlier, as they do in real life
    for sid, cfg in SERIES.items():
        if cfg["lag"] >= 2:
            df.loc[df.index[-cfg["lag"] + 1:], sid] = np.nan
    return df


@pytest.fixture(scope="session")
def raw():
    return synthetic_raw()


@pytest.fixture(scope="session")
def raw_csv(tmp_path_factory, raw):
    p = tmp_path_factory.mktemp("data") / "raw.csv"
    raw.to_csv(p)
    return p
