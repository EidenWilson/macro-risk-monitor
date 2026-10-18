"""
Build data.json for the Macro Risk Monitor page from macro.db (or a raw CSV export).

    python build_data.py --db macro.db --out data.json

Outputs: current snapshot, rule-based regimes, HMM state probabilities,
historical 12-month forward moves per month (the sample used for the risk
distribution), named stress scenarios, and the regime history for charts.
"""
import argparse
import json
import sqlite3
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# publication lag in months: a value for month t is knowable at the end of t+lag
LAG = {"EURIBOR_3M": 0, "EURUSD": 0, "HICP_DE": 1, "HICP_FOOD_DE": 1, "HICP_ENERGY_DE": 1,
       "IP_DE": 2, "EU_NATGAS": 1, "BRENT": 0,
       "HICP_ELEC_DE": 1, "HICP_GAS_DE": 1, "RETAIL_DE": 2, "FOODSERV_DE": 2, "CONF_DE": 0,
       "PPI_C10_DE": 1, "PPI_C24_DE": 1, "PPI_C17_DE": 1, "PPI_C22_DE": 1, "PPI_C16_DE": 1,
       "YC_1Y": 0, "YC_3Y": 0, "US_1Y": 0}

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
}


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
    post = np.vstack([m.predict_proba(Z[: t + 1])[-1] for t in range(len(Z))])
    probs = pd.DataFrame(post, index=X.index, columns=names)
    trans = {names[a]: {names[b]: round(float(m.transmat_[a, b]), 3) for b in range(4)} for a in range(4)}
    stmeans = {names[a]: {"growth": round(float(means[a, 0]), 2), "inflation": round(float(means[a, 1]), 2)} for a in range(4)}
    return probs, stmeans, trans


def move(r, f, a, b):
    s, kind = FACTORS[f]
    if s not in r or pd.isna(r[s].get(a)) or pd.isna(r[s].get(b)):
        return None
    return float(r[s][b] - r[s][a]) if kind == "pp" else float(r[s][b] / r[s][a] - 1)


def main(db=None, csv=None, out="data.json"):
    r = load_raw(db, csv)
    # last complete month = latest month where both Euribor and headline HICP exist
    asof = r.dropna(subset=["EURIBOR_3M", "HICP_DE"]).index.max()
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
    # trailing 12-month volatility of monthly changes, as knowable at each month (for volatility scaling)
    vol = {}
    for f in factors:
        s, kind = FACTORS[f]
        x = r[s].shift(LAG.get(s, 0))
        ch = x.diff() if kind == "pp" else x.pct_change()
        vol[f] = ch.rolling(12, min_periods=9).std()
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
    for name, (a, b) in SCENARIOS.items():
        latest = a is None
        a, b = (last12 if latest else (a, b))
        mv = {}
        for f in factors:
            x = move(r, f, a, b)
            if x is None and latest:                  # series that publish later: use their own latest 12 months
                s = FACTORS[f][0]
                bb = r[s].dropna().index.max()
                aa = r.index[r.index.get_loc(bb) - 12]
                x = move(r, f, aa, bb)
            mv[f] = None if x is None else round(x, 5)
        scen[name] = {"from": a, "to": b, "moves": mv}

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
         "minWage": {"from": 13.90, "to": 14.60, "effective": "2027-01"}}
    with open(out, "w") as fh:
        json.dump(D, fh, separators=(",", ":"))
    print(f"as of {asof}: regime {cur['regime']} since {cur['since']}; HMM {cur['hmm']}; "
          f"{len(samples)} samples; factors {factors}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db"); ap.add_argument("--csv"); ap.add_argument("--out", default="data.json")
    a = ap.parse_args()
    main(a.db, a.csv, a.out)
