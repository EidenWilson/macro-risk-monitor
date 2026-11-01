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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

from macro_config import CRITICAL, SERIES

log = logging.getLogger("pipeline")


DOTENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def load_dotenv(path=DOTENV):
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

# The series list and publication lags live in macro_config.py (shared with build_data.py).

ECB_URL = "https://data-api.ecb.europa.eu/service/data/{key}"
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


# ---------------------------------------------------------------- HTTP
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "macro-risk-monitor/1.1 (+https://github.com/EidenWilson/macro-risk-monitor)"


def http_get(url, params=None, retries=4, backoff=2.0, timeout=30):
    for attempt in range(1, retries + 1):
        try:
            r = SESSION.get(url, params=params, timeout=timeout)
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
    for dim, size in zip(js["id"], js["size"], strict=True):
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
def fetch_one(sid, cfg, start):
    """Fetch one series; returns (sid, series or None, error or None). Runs in a worker thread, never touches the DB."""
    try:
        return sid, FETCHERS[cfg["source"]](cfg["key"], start), None
    except Exception as e:  # keep going; one bad series must not kill the run
        return sid, None, e


def quality_gate(panel, failed, months_stale=3):
    """Problems serious enough to stop the refresh instead of publishing: a critical series failed or went stale."""
    problems = [f"{sid}: fetch failed" for sid in failed if sid in CRITICAL]
    for msg in validate(panel, months_stale):
        if msg.split(":")[0] in CRITICAL and ("stale" in msg or "no data" in msg or "missing" in msg):
            problems.append(msg)
    return problems


def run(db, start, strict=False, workers=6):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    ok, failed = 0, []
    # The 21 downloads are independent, so they run side by side (several times faster than one after another);
    # the database is written from this thread only, in the order of SERIES, so the result is the same.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda kv: fetch_one(kv[0], kv[1], start), SERIES.items()))
    for sid, s, err in results:
        cfg = SERIES[sid]
        if err is None:
            n, rev = upsert(conn, sid, s, now)
            set_meta(conn, sid, cfg, now, "ok", last_period=s.index.max())
            log.info("%-15s %4d rows, %s to %s, %d revised", sid, n, s.index.min(), s.index.max(), rev)
            ok += 1
        else:
            set_meta(conn, sid, cfg, now, "failed", error=str(err)[:500])
            log.error("%-15s FAILED: %s", sid, err)
            failed.append(sid)
        conn.commit()

    if ok == 0:
        log.error("all series failed")
        return 1
    panel = build_panel(conn)
    for msg in validate(panel):
        log.warning("validation: %s", msg)
    log.info("panel_monthly: %d rows x %d cols", *panel.shape)
    conn.close()
    problems = quality_gate(panel, failed)
    for msg in problems:
        log.error("quality gate: %s", msg)
    if strict and problems:
        log.error("--strict: %d critical problem(s), stopping so the last good page stays online", len(problems))
        return 2
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="macro.db")
    ap.add_argument("--start", default="2000-01")
    ap.add_argument("--strict", action="store_true",
                    help="exit with code 2 when a critical series failed or is stale (used by the scheduled refresh)")
    ap.add_argument("--workers", type=int, default=6, help="parallel downloads")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(run(args.db, args.start, strict=args.strict, workers=args.workers))
