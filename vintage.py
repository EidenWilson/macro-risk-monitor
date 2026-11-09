"""
Keep a copy of every refresh: a point-in-time log of the raw data, and a note of what changed.

Each refresh downloads every series' whole history again, and the sources revise past months (on 3 Nov 2026
German food-service sales for August went from -4.7% to -5.4% a year). The pipeline's database is rebuilt on
every run, so without this log the values as first published are lost, and a backtest can only use revised data.

data/vintages.csv has one row for each value the first time a refresh saw it, and again whenever it changed:

    as_of,series_id,period,value
    2026-11-09,FOODSERV_DE,2026-08,98.0

The data as known on a day D is, for each series and month, the last row with as_of <= D (known_on()). A value
that a source drops is logged with an empty value. A series that failed to download is left alone, so a failed
download never reads as deleted history.

Usage (in the refresh, after pipeline.py):
    python vintage.py --db macro.db --log data/vintages.csv [--summary "$GITHUB_STEP_SUMMARY"]
"""
import argparse
import datetime as dt
import pathlib
import sqlite3

import numpy as np
import pandas as pd

KEY = ["series_id", "period"]
COLS = ["as_of", "series_id", "period", "value"]


def read_db(db):
    """The values of this run, for the series that downloaded fine (long format)."""
    with sqlite3.connect(db) as conn:
        obs = pd.read_sql("SELECT series_id, period, value FROM observations", conn)
        ok = pd.read_sql("SELECT series_id FROM series_meta WHERE status = 'ok'", conn)
    return obs[obs.series_id.isin(set(ok.series_id))].reset_index(drop=True)


def read_log(path):
    p = pathlib.Path(path)
    if not p.exists():
        return pd.DataFrame({"as_of": pd.Series(dtype=str), "series_id": pd.Series(dtype=str),
                             "period": pd.Series(dtype=str), "value": pd.Series(dtype=float)})
    return pd.read_csv(p, dtype={"as_of": str, "series_id": str, "period": str, "value": float})


def known_on(log, day=None):
    """The data as known at the end of `day` (YYYY-MM-DD; default: the latest copy), without dropped values."""
    if day is not None:
        log = log[log.as_of <= day]
    last = log.sort_values("as_of", kind="stable").drop_duplicates(KEY, keep="last")   # an empty value (dropped) counts too
    return last.dropna(subset=["value"])[KEY + ["value"]].reset_index(drop=True)


def changes(log, cur):
    """Values new since the last copy, values that changed (old and new), and values a source dropped."""
    prev = known_on(log)
    m = cur.merge(prev, on=KEY, how="outer", suffixes=("", "_old"), indicator=True)
    new = m[m._merge == "left_only"][KEY + ["value"]]
    both = m[m._merge == "both"]
    revised = both[~np.isclose(both.value, both.value_old, rtol=1e-9, atol=1e-12)][KEY + ["value_old", "value"]]
    dropped = m[(m._merge == "right_only") & m.series_id.isin(set(cur.series_id))][KEY + ["value_old"]]
    return new, revised, dropped


def report(day, new, revised, dropped, first=False, top=15):
    """A short Markdown note for the run summary."""
    if first:
        return f"### Data copy {day}\nFirst copy: {len(new):,} values from {new.series_id.nunique()} series.\n"
    if new.empty and revised.empty and dropped.empty:
        return f"### Data copy {day}\nNo value new or changed since the last copy.\n"
    out = [f"### Data copy {day}"]
    if not new.empty:
        per = new.groupby("series_id").period.agg(["min", "max", "count"])
        out.append("New values: " + "; ".join(f"{s} {r['min']}" + (f" to {r['max']}" if r["count"] > 1 else "")
                                             for s, r in per.iterrows()))
    if not revised.empty:
        r = revised.assign(change=(revised.value / revised.value_old - 1) * 100)
        r = r.reindex(r.change.abs().sort_values(ascending=False).index)
        lines = [f"- {x.series_id} {x.period}: {x.value_old:g} to {x.value:g} ({x.change:+.2f}%)" for x in r.head(top).itertuples()]
        more = f"\n- and {len(r) - top} more" if len(r) > top else ""
        out.append(f"Revised ({len(r)}):\n" + "\n".join(lines) + more)
    if not dropped.empty:
        out.append(f"Dropped by the source ({len(dropped)}): " + ", ".join(f"{x.series_id} {x.period}" for x in dropped.head(top).itertuples()))
    return "\n\n".join(out) + "\n"


def update(db, path, day=None):
    """Append this run's changes to the log; returns the Markdown note."""
    day = day or dt.datetime.now(dt.timezone.utc).date().isoformat()
    log, cur = read_log(path), read_db(db)
    first = log.empty
    new, revised, dropped = changes(log, cur)
    add = pd.concat([new, revised[KEY + ["value"]], dropped[KEY].assign(value=np.nan)], ignore_index=True)
    if not add.empty:
        add.insert(0, "as_of", day)
        out = pd.concat([log, add], ignore_index=True).sort_values(COLS[:3], kind="stable")
        pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(path, index=False, float_format="%.12g")
    return report(day, new, revised, dropped, first=first)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    ap.add_argument("--db", default="macro.db")
    ap.add_argument("--log", default="data/vintages.csv")
    ap.add_argument("--summary", help="file to append the note to (GitHub's $GITHUB_STEP_SUMMARY)")
    a = ap.parse_args()
    note = update(a.db, a.log)
    print(note)
    if a.summary:
        with open(a.summary, "a", encoding="utf-8") as fh:
            fh.write(note + "\n")
