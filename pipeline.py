"""
Macro data pipeline: ECB Data Portal + FRED -> SQLite, monthly and lag-aware.

Usage:
    put FRED_API_KEY=your_key in a .env file next to this script (free at fred.stlouisfed.org)
    python pipeline.py --db macro.db --start 2000-01

Tables written:
    observations   long format, raw monthly values (upserted, revisions overwrite)
    series_meta    per-series status, last period, last error
    panel_monthly  wide, publication-lag-shifted panel + derived features
                   (row t = what was knowable at the end of month t)
"""
import argparse
import io
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

log = logging.getLogger("pipeline")


def load_dotenv(path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")):
    """Minimal .env loader (KEY=VALUE per line); real env vars take precedence."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_dotenv()

# kind: rate  -> feature = 12m change in percentage points
#       index -> feature = 12m % change
#       level -> feature = 12m % change
# lag: months between end of reference period and public release (conservative)
SERIES = {
    "EURIBOR_3M":     dict(source="ecb",  key="FM/M.U2.EUR.RT.MM.EURIBOR3MD_.HSTA", kind="rate",  lag=0),
    "EURUSD":         dict(source="ecb",  key="EXR/M.USD.EUR.SP00.A",               kind="level", lag=0),
    # HICP dataset replaced ICP on 2026-02-04 (ECOICOP 2); old ICP keys stop at 2025-12
    "HICP_DE":        dict(source="ecb",  key="HICP/M.DE.N.000000.4D0.INX",         kind="index", lag=1),
    "HICP_FOOD_DE":   dict(source="ecb",  key="HICP/M.DE.N.010000.4D0.INX",         kind="index", lag=1),
    "HICP_ENERGY_DE": dict(source="ecb",  key="HICP/M.DE.N.NRGY00.4D0.INX",         kind="index", lag=1),
    # Germany is not in ECB STS/STBS; Eurostat: industry B-D, seasonally+calendar adj., 2021=100
    "IP_DE":          dict(source="eurostat", key="sts_inpr_m?geo=DE&nace_r2=B-D&s_adj=SCA&unit=I21&indic_bt=PRD",
                           kind="index", lag=2),
    "EU_NATGAS":      dict(source="fred", key="PNGASEUUSDM",                        kind="level", lag=1),
    "BRENT":          dict(source="fred", key="DCOILBRENTEU",                       kind="level", lag=0),
    # electricity and gas separately (HICP, ECOICOP 2 codes)
    "HICP_ELEC_DE":   dict(source="ecb",  key="HICP/M.DE.N.045100.4D0.INX",         kind="index", lag=1),
    "HICP_GAS_DE":    dict(source="ecb",  key="HICP/M.DE.N.045200.4D0.INX",         kind="index", lag=1),
    # demand: retail and food-service sales volumes, consumer confidence (Eurostat)
    "RETAIL_DE":      dict(source="eurostat", key="sts_trtu_m?geo=DE&nace_r2=G47&s_adj=SCA&unit=I21&indic_bt=VOL_SLS", kind="index", lag=2),
    "FOODSERV_DE":    dict(source="eurostat", key="sts_setu_m?geo=DE&nace_r2=I56&s_adj=SCA&unit=I21&indic_bt=VOL_SLS", kind="index", lag=2),
    "CONF_DE":        dict(source="eurostat", key="ei_bsco_m?geo=DE&indic=BS-CSMCI&s_adj=SA&unit=BAL", kind="rate", lag=0),
    # producer prices by sector (Eurostat, NACE C10 food, C24 metals, C17 paper, C22 plastics, C16 wood)
    "PPI_C10_DE":     dict(source="eurostat", key="sts_inpp_m?geo=DE&nace_r2=C10&s_adj=NSA&unit=I21&indic_bt=PRC_PRR", kind="index", lag=1),
    "PPI_C24_DE":     dict(source="eurostat", key="sts_inpp_m?geo=DE&nace_r2=C24&s_adj=NSA&unit=I21&indic_bt=PRC_PRR", kind="index", lag=1),
    "PPI_C17_DE":     dict(source="eurostat", key="sts_inpp_m?geo=DE&nace_r2=C17&s_adj=NSA&unit=I21&indic_bt=PRC_PRR", kind="index", lag=1),
    "PPI_C22_DE":     dict(source="eurostat", key="sts_inpp_m?geo=DE&nace_r2=C22&s_adj=NSA&unit=I21&indic_bt=PRC_PRR", kind="index", lag=1),
    "PPI_C16_DE":     dict(source="eurostat", key="sts_inpp_m?geo=DE&nace_r2=C16&s_adj=NSA&unit=I21&indic_bt=PRC_PRR", kind="index", lag=1),
    # rates for hedge costs: ECB AAA euro area spot curve, US 1-year Treasury
    "YC_1Y":          dict(source="ecb",  key="YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_1Y", kind="rate",  lag=0),
    "YC_3Y":          dict(source="ecb",  key="YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_3Y", kind="rate",  lag=0),
    "US_1Y":          dict(source="fred", key="DGS1",                               kind="rate",  lag=0),
}

ECB_URL = "https://data-api.ecb.europa.eu/service/data/{key}"
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


# ---------------------------------------------------------------- HTTP
def http_get(url, params=None, retries=4, backoff=2.0, timeout=30):
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
            if attempt == retries:
                raise
            wait = backoff ** attempt
            log.warning("%s (attempt %d/%d), retrying in %.0fs", e, attempt, retries, wait)
            time.sleep(wait)


# ---------------------------------------------------------------- fetchers
def to_monthly(s: pd.Series) -> pd.Series:
    """Any frequency -> monthly mean, indexed by 'YYYY-MM' strings."""
    s = pd.to_numeric(s, errors="coerce").dropna()
    s.index = pd.to_datetime(s.index.astype(str).str.replace("M", "-", regex=False))
    m = s.groupby(s.index.to_period("M")).mean()
    m.index = m.index.astype(str)
    return m


def fetch_ecb(key, start):
    r = http_get(ECB_URL.format(key=key),
                 params={"format": "csvdata", "startPeriod": start})
    df = pd.read_csv(io.StringIO(r.text), usecols=["TIME_PERIOD", "OBS_VALUE"])
    if df.empty:
        raise ValueError("empty response")
    return to_monthly(df.set_index("TIME_PERIOD")["OBS_VALUE"])


def fetch_fred(key, start):
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        raise RuntimeError("FRED_API_KEY not set")
    r = http_get(FRED_URL, params={
        "series_id": key, "api_key": api_key, "file_type": "json",
        "observation_start": f"{start}-01",
    })
    obs = r.json().get("observations", [])
    if not obs:
        raise ValueError("empty response")
    df = pd.DataFrame(obs)
    df["value"] = df["value"].replace(".", np.nan)  # FRED marks missing as "."
    return to_monthly(df.set_index("date")["value"])


EUROSTAT_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{key}"


def fetch_eurostat(key, start):
    """JSON-stat 2.0; filters must pin every dimension except time to one value."""
    sep = "&" if "?" in key else "?"
    r = http_get(EUROSTAT_URL.format(key=key) + f"{sep}format=JSON&lang=EN&sinceTimePeriod={start}")
    js = r.json()
    for dim, size in zip(js["id"], js["size"]):
        if dim != "time" and size != 1:
            raise ValueError(f"filter not unique: {dim} has {size} values")
    t_idx = js["dimension"]["time"]["category"]["index"]   # period -> position
    pos2t = {v: k for k, v in t_idx.items()}
    vals = {pos2t[int(i)]: v for i, v in js.get("value", {}).items()}
    if not vals:
        raise ValueError("empty response")
    return to_monthly(pd.Series(vals))


FETCHERS = {"ecb": fetch_ecb, "fred": fetch_fred, "eurostat": fetch_eurostat}


# ---------------------------------------------------------------- storage
SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    series_id  TEXT NOT NULL,
    period     TEXT NOT NULL,          -- YYYY-MM
    value      REAL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (series_id, period)
);
CREATE TABLE IF NOT EXISTS series_meta (
    series_id   TEXT PRIMARY KEY,
    source      TEXT, key TEXT, kind TEXT, lag INTEGER,
    last_period TEXT, last_fetch TEXT, status TEXT, error TEXT
);
"""


def upsert(conn, sid, s: pd.Series, now):
    old = dict(conn.execute(
        "SELECT period, value FROM observations WHERE series_id=?", (sid,)).fetchall())
    revised = sum(1 for p, v in s.items()
                  if p in old and old[p] is not None and not np.isclose(old[p], v))
    conn.executemany(
        """INSERT INTO observations (series_id, period, value, fetched_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(series_id, period) DO UPDATE
           SET value=excluded.value, fetched_at=excluded.fetched_at""",
        [(sid, p, float(v), now) for p, v in s.items()])
    return len(s), revised


def set_meta(conn, sid, cfg, now, status, last_period=None, error=None):
    conn.execute(
        """INSERT INTO series_meta VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(series_id) DO UPDATE SET
             last_period=COALESCE(excluded.last_period, series_meta.last_period),
             last_fetch=excluded.last_fetch, status=excluded.status, error=excluded.error""",
        (sid, cfg["source"], cfg["key"], cfg["kind"], cfg["lag"],
         last_period, now, status, error))


# ---------------------------------------------------------------- panel
def build_panel(conn, end=None):
    raw = pd.read_sql("SELECT series_id, period, value FROM observations", conn)
    if raw.empty:
        raise RuntimeError("no observations in DB")
    wide = raw.pivot(index="period", columns="series_id", values="value")
    wide.index = pd.PeriodIndex(wide.index, freq="M")

    end = pd.Period(end or datetime.now(timezone.utc).strftime("%Y-%m"), freq="M")
    full = pd.period_range(wide.index.min(), end, freq="M")
    wide = wide.reindex(full)

    out = pd.DataFrame(index=full)
    for sid, cfg in SERIES.items():
        if sid not in wide:
            continue
        s = wide[sid].shift(cfg["lag"])          # only what was published by month t
        out[sid] = s
        if cfg["kind"] == "rate":
            out[f"{sid}_chg12"] = s - s.shift(12)               # pp
        else:
            out[f"{sid}_yoy"] = s / s.shift(12) - 1             # %
        out[f"{sid}_mom3"] = s / s.shift(3) - 1 if cfg["kind"] != "rate" else s - s.shift(3)

    out.index = out.index.astype(str)
    out.index.name = "period"
    out.to_sql("panel_monthly", conn, if_exists="replace")
    return out


def validate(panel, months_stale=3):
    issues = []
    for sid in SERIES:
        if sid not in panel:
            issues.append(f"{sid}: missing entirely")
            continue
        s = panel[sid]
        first, last = s.first_valid_index(), s.last_valid_index()
        if first is None:
            issues.append(f"{sid}: no data")
            continue
        inner = s.loc[first:last]
        if inner.isna().any():
            issues.append(f"{sid}: {int(inner.isna().sum())} internal gaps")
        tail = len(s.loc[last:]) - 1
        if tail > months_stale:
            issues.append(f"{sid}: stale, last value {last} ({tail} months behind)")
    return issues


# ---------------------------------------------------------------- main
def run(db, start):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    ok = 0
    for sid, cfg in SERIES.items():
        try:
            s = FETCHERS[cfg["source"]](cfg["key"], start)
            n, rev = upsert(conn, sid, s, now)
            set_meta(conn, sid, cfg, now, "ok", last_period=s.index.max())
            log.info("%-15s %4d rows, %s to %s, %d revised", sid, n, s.index.min(), s.index.max(), rev)
            ok += 1
        except Exception as e:  # keep going; one bad series must not kill the run
            set_meta(conn, sid, cfg, now, "failed", error=str(e)[:500])
            log.error("%-15s FAILED: %s", sid, e)
        conn.commit()

    if ok == 0:
        log.error("all series failed")
        return 1
    panel = build_panel(conn)
    for msg in validate(panel):
        log.warning("validation: %s", msg)
    log.info("panel_monthly: %d rows x %d cols", *panel.shape)
    conn.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="macro.db")
    ap.add_argument("--start", default="2000-01")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(run(args.db, args.start))
