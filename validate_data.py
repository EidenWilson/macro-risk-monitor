"""
Check a freshly built data.json before it is published.

    python validate_data.py docs/data.json [--max-age-months 4]

The scheduled refresh used to commit whatever build_data.py produced. A source that changed its format, a
series that stopped updating or a half-empty download would then go straight onto the live page and into the
alert email. This script is the gate in between: it exits with code 1 and lists every problem, and the workflow
stops before committing, so the last good page stays online.
"""
import argparse
import json
import math
import pathlib
import sys
from datetime import date

REGIMES = {"Recovery", "Overheating", "Stagflation", "Contraction"}
REQUIRED_CURRENT = ["asOf", "regime", "since", "euribor", "eurusd", "hicp", "energy", "food", "hmm", "vol", "hmmState"]
REQUIRED_SCENARIOS = ["2022 energy shock", "Last 12 months", "2008 financial crisis", "2011 euro crisis", "2020 pandemic lockdown"]
BASE_FACTORS = ["rate", "fx", "energy", "food", "oil", "cpi"]

# plausible ranges: a value outside them means a unit or parsing error, not an economic event
RANGES = {"euribor": (-2, 15), "eurusd": (0.6, 2.0), "hicp": (-5, 25), "energy": (-60, 150), "food": (-15, 40)}


def months_between(a, b):
    ya, ma = map(int, a.split("-")[:2])
    yb, mb = map(int, b.split("-")[:2])
    return (yb - ya) * 12 + (mb - ma)


def finite(x):
    return x is None or (isinstance(x, (int, float)) and math.isfinite(x))


def check(D, today=None, max_age_months=4):
    today = today or date.today()
    errs = []
    for key in ("current", "factors", "samples", "scenarios", "hist", "minWage", "lockdown"):
        if key not in D:
            errs.append(f"missing top-level key {key!r}")
    if errs:
        return errs
    cur, factors = D["current"], D["factors"]

    for k in REQUIRED_CURRENT:
        if cur.get(k) is None:
            errs.append(f"current.{k} is missing")
    if cur.get("regime") not in REGIMES:
        errs.append(f"current.regime {cur.get('regime')!r} is not one of {sorted(REGIMES)}")
    for k, (lo, hi) in RANGES.items():
        v = cur.get(k)
        if isinstance(v, (int, float)) and not lo <= v <= hi:
            errs.append(f"current.{k} = {v} is outside the plausible range {lo}..{hi}")
    if isinstance(cur.get("hmm"), dict) and cur["hmm"]:
        total = sum(cur["hmm"].values())
        if abs(total - 1) > 0.01:
            errs.append(f"HMM probabilities add up to {total:.3f}, not 1")
    if isinstance(cur.get("asOf"), str):
        age = months_between(cur["asOf"], f"{today.year}-{today.month:02d}")
        if age > max_age_months:
            errs.append(f"data is {age} months old (as of {cur['asOf']}); a source has probably stopped updating")

    missing_base = [f for f in BASE_FACTORS if f not in factors]
    if missing_base:
        errs.append(f"core factors missing: {missing_base}")

    width = 5 + 2 * len(factors)                       # period, regime, hmm, e0, infl0, moves..., vols...
    S = D["samples"]
    if len(S) < 150:
        errs.append(f"only {len(S)} historical windows (expected 150+): the bad-year estimate would be unreliable")
    bad_rows = [row[0] if row else "?" for row in S if len(row) != width]
    if bad_rows:
        errs.append(f"{len(bad_rows)} sample rows have the wrong length (first: {bad_rows[0]})")
    bad_vals = [row[0] for row in S if len(row) == width and not all(finite(x) for x in row[3:])]
    if bad_vals:
        errs.append(f"{len(bad_vals)} sample rows contain NaN or infinite values (first: {bad_vals[0]})")
    regimes_seen = {row[1] for row in S if len(row) > 1}
    if cur.get("regime") in REGIMES and cur["regime"] not in regimes_seen:
        errs.append(f"no historical windows in today's regime {cur['regime']!r}: the regime-conditional range is empty")

    for name in REQUIRED_SCENARIOS:
        sc = D["scenarios"].get(name)
        if not sc:
            errs.append(f"scenario {name!r} is missing")
            continue
        empty = [f for f in BASE_FACTORS if sc.get("moves", {}).get(f) is None]
        if empty:
            errs.append(f"scenario {name!r} has no move for {empty}")

    h = D["hist"]
    n = len(h.get("period", []))
    for k in ("reg", "hmm", "hicp", "ip", "euribor"):
        if len(h.get(k, [])) != n:
            errs.append(f"hist.{k} has {len(h.get(k, []))} values for {n} periods")
    return errs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default="docs/data.json")
    ap.add_argument("--max-age-months", type=int, default=4)
    a = ap.parse_args(argv)
    D = json.loads(pathlib.Path(a.path).read_text(encoding="utf-8"))
    errs = check(D, max_age_months=a.max_age_months)
    if errs:
        print(f"{a.path}: {len(errs)} problem(s)")
        for e in errs:
            print("  -", e)
        return 1
    print(f"{a.path}: ok (as of {D['current']['asOf']}, {len(D['samples'])} windows, regime {D['current']['regime']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
