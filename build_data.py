"""
Build data.json for the Macro Risk Monitor page from macro.db (or a raw CSV export).

    python build_data.py --db macro.db --out data.json

Outputs: current snapshot, rule-based regimes, HMM state probabilities,
historical 12-month forward moves per month (the sample used for the risk
distribution), named stress scenarios, and the regime history for charts.
"""
import argparse
import json
import logging
import os
import sqlite3
import subprocess
import warnings
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy.stats import multivariate_normal

from macro_config import LAG, MIN_WAGE, min_wage_warning  # publication lags: one copy, shared with pipeline.py

# Only silence the noise we know about (hmmlearn's convergence chatter and pandas' future-default notices);
# a blanket filter would also hide real problems such as division by zero in the moves.
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", module="hmmlearn")
logging.getLogger("hmmlearn").setLevel(logging.ERROR)

# factor name -> (series, kind); kind "pp" = change in percentage points, "pct" = relative change
FACTORS = {
    "rate": ("EURIBOR_3M", "pp"), "fx": ("EURUSD", "pct"), "energy": ("HICP_ENERGY_DE", "pct"),
    "food": ("HICP_FOOD_DE", "pct"), "oil": ("BRENT", "pct"), "cpi": ("HICP_DE", "pct"),
    "elec": ("HICP_ELEC_DE", "pct"), "gas": ("HICP_GAS_DE", "pct"),
    "retail": ("RETAIL_DE", "pct"), "foodserv": ("FOODSERV_DE", "pct"),
    "ppi_food": ("PPI_C10_DE", "pct"), "ppi_metal": ("PPI_C24_DE", "pct"), "ppi_paper": ("PPI_C17_DE", "pct"),
    "ppi_plastic": ("PPI_C22_DE", "pct"), "ppi_wood": ("PPI_C16_DE", "pct"),
    "ip": ("IP_DE", "pct"),
}

SCENARIOS = {
    "2022 energy shock": ("2021-10", "2022-10"),
    "Last 12 months": (None, None),          # filled with the latest 12 months
    "2008 financial crisis": ("2008-09", "2009-09"),
    "2011 euro crisis": ("2011-06", "2012-06"),
    "2020 pandemic lockdown": ("2020-02", "2021-02"),
}

# Covid rules (lockdowns, then 2G/3G access rules) legally closed or restricted restaurants and shops from
# March 2020 until most measures ended in March 2022. That was a public-health order, not a macro shock, so
# sales windows touching it are left out of the history used for the risk range and the backtest, and the
# lockdown is offered as a separate stress scenario instead.
LOCKDOWN = ("2020-03", "2022-03")
DEMAND_FACTORS = {"retail", "foodserv"}
LOCKDOWN_SCENARIO = "2020 pandemic lockdown"


def load_raw(db=None, csv=None):
    if csv:
        return pd.read_csv(csv, index_col="period")
    con = sqlite3.connect(db)
    raw = pd.read_sql("SELECT series_id, period, value FROM observations", con)
    return raw.pivot(index="period", columns="series_id", values="value").sort_index()


def yoy(s):
    return s / s.shift(12) - 1


def classify(k):
    ip = yoy(k["IP_DE"]).rolling(3).mean()
    inf = yoy(k["HICP_DE"]).rolling(3).mean()
    gd, idf = ip.diff(6), inf.diff(6)
    valid = gd.notna() & idf.notna()
    reg = np.where((gd > 0) & (idf <= 0), "Recovery",
          np.where((gd > 0) & (idf > 0), "Overheating",
          np.where((gd <= 0) & (idf > 0), "Stagflation", "Contraction")))
    return pd.Series(reg, index=k.index).where(valid), gd, idf


HMM_NAMES = ["Expansion", "Sluggish growth", "Inflation shock", "Recession"]


def filtered_probs(m, Z):
    """P(state at t | observations up to t) for every t, in one forward pass.

    Same numbers as calling m.predict_proba(Z[:t+1])[-1] for each t (the old code), but O(T) instead of O(T^2):
    each of those calls re-ran forward-backward over the whole prefix."""
    T, K = len(Z), m.n_components
    covs = m.covars_                                   # (K, d, d) for covariance_type="full"
    logb = np.column_stack([multivariate_normal(m.means_[k], covs[k], allow_singular=True).logpdf(Z) for k in range(K)])
    logb = logb.reshape(T, K)
    A = m.transmat_
    out = np.empty((T, K))
    prior = m.startprob_
    for t in range(T):
        la = np.log(np.maximum(prior, 1e-300)) + logb[t]
        la -= la.max()
        a = np.exp(la)
        a /= a.sum()
        out[t] = a
        prior = a @ A
    return out


def hmm_states(k, seed=0):
    """4-state Gaussian HMM on the LEVELS of growth and inflation (3-month averages, as published).
    Returns filtered state probabilities (knowable at each month), state means and the transition matrix."""
    from hmmlearn.hmm import GaussianHMM
    X = pd.concat([yoy(k["IP_DE"]).rolling(3).mean() * 100, yoy(k["HICP_DE"]).rolling(3).mean() * 100], axis=1).dropna()
    X.columns = ["g", "i"]
    mu, sd = X.mean(), X.std()
    Z = ((X - mu) / sd).values
    best = None
    for s in range(seed, seed + 10):
        m = GaussianHMM(n_components=4, covariance_type="full", n_iter=500, random_state=s).fit(Z)
        ll = m.score(Z)
        if best is None or ll > best[0]:
            best = (ll, m)
    m = best[1]
    means = m.means_ * sd.values + mu.values
    def label(g, i):
        if i > 4: return "Inflation shock"
        if g < -6: return "Recession"
        if g > 2: return "Expansion"
        return "Sluggish growth"
    names = [label(g, i) for g, i in means]
    if len(set(names)) < 4:                       # fall back to ordering if two states share a label
        order = np.argsort(means[:, 0])
        names = [None] * 4
        for rank, idx in enumerate(order):
            names[idx] = ["Recession", "Sluggish growth", "Expansion", "Expansion+"][rank]
        names[int(np.argmax(means[:, 1]))] = "Inflation shock"
    post = filtered_probs(m, Z)
    probs = pd.DataFrame(post, index=X.index, columns=names)
    trans = {names[a]: {names[b]: round(float(m.transmat_[a, b]), 3) for b in range(4)} for a in range(4)}
    stmeans = {names[a]: {"growth": round(float(means[a, 0]), 2), "inflation": round(float(means[a, 1]), 2)} for a in range(4)}
    return probs, stmeans, trans


def move(r, f, a, b):
    s, kind = FACTORS[f]
    if s not in r or pd.isna(r[s].get(a)) or pd.isna(r[s].get(b)):
        return None
    return float(r[s][b] - r[s][a]) if kind == "pp" else float(r[s][b] / r[s][a] - 1)


def git_sha():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              timeout=5, cwd=os.path.dirname(os.path.abspath(__file__))).stdout.strip() or None
    except Exception:
        return None


def series_status(db):
    """Last period and fetch status per series, from the pipeline's series_meta table (None for a CSV build)."""
    if not db:
        return None
    con = sqlite3.connect(db)
    try:
        rows = con.execute("SELECT series_id, last_period, status FROM series_meta ORDER BY series_id").fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()
    return {sid: {"last": lp, "status": st} for sid, lp, st in rows}


def unchanged_meta(out, D):
    """The previous file's provenance when nothing else in the data changed, else None. Without this the build time
    alone would make every refresh commit a new data.json and a new 690 KB page, even when no source published anything."""
    try:
        with open(out) as fh:
            old = json.load(fh)
    except (OSError, ValueError):
        return None
    strip = lambda x: {k: v for k, v in x.items() if k != "meta"}
    return old.get("meta") if "meta" in old and strip(old) == json.loads(json.dumps(strip(D))) else None


def main(db=None, csv=None, out="data.json"):
    msg = min_wage_warning()
    if msg:
        print("warning:", msg)
    r = load_raw(db, csv)
    # last complete month = latest month where Euribor, the dollar rate and headline, energy and food inflation all
    # exist. Headline inflation can come out a week or two before its parts: on 9 Nov 2026 October was in for the
    # headline but not for food, and the data check stopped the page from showing October with no food prices.
    asof = r.dropna(subset=[c for c in ("EURIBOR_3M", "EURUSD", "HICP_DE", "HICP_ENERGY_DE", "HICP_FOOD_DE") if c in r]).index.max()
    r = r.loc[:asof]
    k = pd.DataFrame({c: r[c].shift(LAG.get(c, 0)) for c in r.columns})
    reg, gd, idf = classify(k)
    probs, stmeans, trans = hmm_states(k)
    hstate = probs.idxmax(axis=1)

    factors = [f for f, (s, _) in FACTORS.items() if s in r.columns and r[s].notna().sum() > 60]
    fwd = {}
    for f in factors:
        s, kind = FACTORS[f]
        fwd[f] = (r[s].shift(-12) - r[s]) if kind == "pp" else (r[s].shift(-12) / r[s] - 1)
    F = pd.DataFrame(fwd)
    in_lockdown = (r.index >= LOCKDOWN[0]) & (r.index <= LOCKDOWN[1])
    # windows that start up to 12 months before the lockdown and end after it began overlap it
    starts = pd.PeriodIndex(r.index, freq="M")
    lo, hi = pd.Period(LOCKDOWN[0], "M"), pd.Period(LOCKDOWN[1], "M")
    overlaps = np.array([(p + 12 >= lo) and (p <= hi) for p in starts])
    for f in DEMAND_FACTORS & set(factors):
        F.loc[overlaps, f] = np.nan
    # trailing 12-month volatility of monthly changes, as knowable at each month (for volatility scaling)
    vol = {}
    for f in factors:
        s, kind = FACTORS[f]
        ch = r[s].diff() if kind == "pp" else r[s].pct_change()
        if f in DEMAND_FACTORS:
            ch = ch.mask(in_lockdown)                    # lockdown swings must not inflate later volatility
        vol[f] = ch.shift(LAG.get(s, 0)).rolling(12, min_periods=9).std()
    VOL = pd.DataFrame(vol)
    base_cols = ["rate", "fx", "energy", "food", "oil", "cpi"]
    S = pd.concat([reg.rename("reg"), hstate.rename("hmm"), F], axis=1).dropna(subset=["reg", "hmm"] + base_cols)
    samples = []
    for p, row in S.iterrows():
        e0 = round(float(r["EURIBOR_3M"][p]), 3)                       # rate level at the start of the window
        infl0 = yoy(r["HICP_DE"]).get(p)                                  # trailing inflation at the start
        infl0 = None if pd.isna(infl0) else round(float(infl0) * 100, 2)
        vs = [None if pd.isna(VOL[f].get(p)) else round(float(VOL[f][p]), 6) for f in factors]
        samples.append([p, row["reg"], row["hmm"], e0, infl0] + [None if pd.isna(row[f]) else round(float(row[f]), 5) for f in factors] + vs)

    scen = {}
    last12 = (r.index[r.index.get_loc(asof) - 12], asof)
    # Other scenarios that overlap the Covid rules would mix closures and reopenings into their sales move
    # (2022: restaurant sales rose as rules ended). Their sales move is measured over the first 12 months
    # after the rules ended instead; prices, rates and output keep the scenario's own window.
    first_free = pd.Period(LOCKDOWN[1], "M") + 1
    post = (str(first_free), str(first_free + 12))
    for name, (a, b) in SCENARIOS.items():
        latest = a is None
        a, b = (last12 if latest else (a, b))
        touches = name != LOCKDOWN_SCENARIO and a <= LOCKDOWN[1] and b >= LOCKDOWN[0]
        mv = {}
        for f in factors:
            x = move(r, f, *(post if touches and f in DEMAND_FACTORS else (a, b)))
            if x is None and latest:                  # series that publish later: use their own latest 12 months
                s = FACTORS[f][0]
                bb = r[s].dropna().index.max()
                aa = r.index[r.index.get_loc(bb) - 12]
                x = move(r, f, aa, bb)
            mv[f] = None if x is None else round(x, 5)
        scen[name] = {"from": a, "to": b, "moves": mv}
        if touches:
            scen[name]["salesFrom"], scen[name]["salesTo"] = post

    rr = reg.dropna()
    i = len(rr) - 1
    while i > 0 and rr.iloc[i - 1] == rr.iloc[-1]:
        i -= 1
    yo = lambda s, p: round(float(yoy(r[s]).get(p, np.nan)) * 100, 1) if s in r and not pd.isna(yoy(r[s]).get(p, np.nan)) else None
    last_valid = lambda s: r[s].dropna().index.max() if s in r else None
    cur = {
        "asOf": asof, "regime": rr.iloc[-1], "since": rr.index[i],
        "euribor": round(float(r["EURIBOR_3M"][asof]), 3), "eurusd": round(float(r["EURUSD"][asof]), 4),
        "hicp": yo("HICP_DE", asof), "energy": yo("HICP_ENERGY_DE", asof), "food": yo("HICP_FOOD_DE", asof),
        "ip": yo("IP_DE", last_valid("IP_DE")), "brentYoy": yo("BRENT", asof),
        "hmm": {c: round(float(probs[c].iloc[-1]), 3) for c in probs.columns},
        "vol": {f: (None if pd.isna(VOL[f].dropna().iloc[-1]) else round(float(VOL[f].dropna().iloc[-1]), 6)) for f in factors},
        "hmmState": hstate.iloc[-1], "hmmMeans": stmeans, "hmmTrans": trans,
    }
    for key, s in [("yc1", "YC_1Y"), ("yc3", "YC_3Y"), ("us1", "US_1Y")]:
        if s in r and r[s].notna().any():
            cur[key] = round(float(r[s].dropna().iloc[-1]), 3)
    for key, s in [("retail", "RETAIL_DE"), ("foodserv", "FOODSERV_DE"), ("elec", "HICP_ELEC_DE"), ("gas", "HICP_GAS_DE")]:
        if s in r and r[s].notna().sum() > 12:
            cur[key] = yo(s, last_valid(s))

    h = pd.DataFrame({"reg": reg, "hicp": yoy(k["HICP_DE"]), "ip": yoy(k["IP_DE"]), "euribor": k["EURIBOR_3M"],
                      "g": gd, "i": idf, "hmm": hstate}).loc["2005-01":]
    hist = {"period": list(h.index), "reg": [x if isinstance(x, str) else None for x in h["reg"]],
            "hmm": [x if isinstance(x, str) else None for x in h["hmm"]]}
    for c, mult in [("hicp", 100), ("ip", 100), ("g", 100), ("i", 100), ("euribor", 1)]:
        hist[c] = [None if pd.isna(x) else round(float(x) * mult, 3) for x in h[c]]

    D = {"current": cur, "factors": factors, "samples": samples, "scenarios": scen, "hist": hist,
         "minWage": dict(MIN_WAGE),
         "lockdown": {"from": LOCKDOWN[0], "to": LOCKDOWN[1]},
         # provenance: when and from what this file was built, so a figure on the page can be traced back
         "meta": {"schema": 2, "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "code": git_sha(), "samples": len(samples), "series": series_status(db)}}
    D["meta"] = unchanged_meta(out, D) or D["meta"]
    with open(out, "w") as fh:
        json.dump(D, fh, separators=(",", ":"))
    print(f"as of {asof}: regime {cur['regime']} since {cur['since']}; HMM {cur['hmm']}; "
          f"{len(samples)} samples; factors {factors}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db"); ap.add_argument("--csv"); ap.add_argument("--out", default="data.json")
    a = ap.parse_args()
    main(a.db, a.csv, a.out)
