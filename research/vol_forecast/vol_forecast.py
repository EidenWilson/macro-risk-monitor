"""Can a volatility forecast make the bad year better? The scaling step, tested with three forecasts.

The page scales each past 12-month move by today's volatility over the volatility back then (filtered historical
simulation). "Today's volatility" is the trailing 12-month volatility: it assumes the next year is as volatile as
the last one. Volatility does not behave like that: it reverts to its long-run level, and it tends to rise after
large moves. Volatility is also where machine learning has enough data: every month is a training example, not only
the bad years (the challenger study showed that predicting the 5% tail directly fails for lack of bad years).

Forecasts of each factor's volatility over the next 12 months, made every month on data known at the time:
  trailing  the page today (no forecast: the last 12 months carry on)
  linear    log forward volatility on log trailing and log 36-month volatility (a HAR-style regression)
  lightgbm  the same target from 38 inputs: every factor's trailing volatility, the phase, Euribor, inflation and
            the last published 12-month moves

Then the page's own model, in the built page, recomputes the 1-in-20 bad year for the six examples with each forecast
as "today's volatility" ("now": past moves keep their trailing volatility back then), or as both today's and back
then ("both": every past move scaled by the forecast made at its own start). Same 213 months, the page model in its
strict form (no look-ahead), the same outcomes.

Run from the repo root (needs the data log data/vintages.csv, lightgbm and Playwright):
    python research/vol_forecast/vol_forecast.py
"""
import functools
import http.server
import json
import pathlib
import sys
import threading
import warnings

import lightgbm as lgb
import numpy as np
import pandas as pd
import statsmodels.api as sm
from playwright.sync_api import sync_playwright
from scipy import stats

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
import vintage  # noqa: E402
from build_data import DEMAND_FACTORS, FACTORS, LOCKDOWN  # noqa: E402
from macro_config import LAG  # noqa: E402

warnings.filterwarnings("ignore")
TAU, GAP, MIN_TRAIN = 0.05, 14, 36
D = json.loads((REPO / "docs" / "data.json").read_text())
fac, S = D["factors"], D["samples"]
NF, SOFF = len(fac), 5
VOFF = SOFF + NF
per = [s[0] for s in S]
mi = np.array([int(p[:4]) * 12 + int(p[5:7]) - 1 for p in per])

# ---------------- volatility, as the page computes it, and what came after ----------------
raw = vintage.known_on(vintage.read_log(REPO / "data" / "vintages.csv")).pivot(index="period", columns="series_id", values="value").sort_index()
lock = (raw.index >= LOCKDOWN[0]) & (raw.index <= LOCKDOWN[1])
trail, long_, fwd, past = {}, {}, {}, {}
for f in fac:
    s, kind = FACTORS[f]
    ch = raw[s].diff() if kind == "pp" else raw[s].pct_change()
    if f in DEMAND_FACTORS:
        ch = ch.mask(lock)
    lag = LAG.get(s, 0)
    t12 = ch.rolling(12, min_periods=9).std()
    trail[f] = t12.shift(lag)                                   # the page's volatility: last 12 months, as published
    long_[f] = ch.rolling(36, min_periods=24).std().shift(lag)  # the long-run level it reverts to
    fwd[f] = t12.shift(-12)                                     # what came: volatility over the next 12 months
    lvl = raw[s]
    past[f] = ((lvl - lvl.shift(12)) if kind == "pp" else (lvl / lvl.shift(12) - 1)).shift(lag)
trail, long_, fwd, past = (pd.DataFrame(x).reindex(per) for x in (trail, long_, fwd, past))
page_vol = pd.DataFrame([[np.nan if x is None else x for x in s[VOFF:VOFF + NF]] for s in S], index=per, columns=fac)
assert np.allclose(trail.values, page_vol.values, rtol=1e-4, atol=1e-6, equal_nan=True), "volatility does not match the page"

X = pd.DataFrame(index=per)
for r_ in ["Recovery", "Overheating", "Stagflation", "Contraction"]:
    X["phase_" + r_] = [1.0 if s[1] == r_ else 0.0 for s in S]
X["euribor"] = [s[3] for s in S]
X["inflation_yoy"] = pd.Series([np.nan if s[4] is None else s[4] for s in S], index=per).shift(LAG["HICP_DE"]).values
for f in fac:
    X["logvol_" + f] = np.log(trail[f])
    X["past12_" + f] = past[f]


def forecasts(f):
    """Forecast of log forward volatility of factor f at every sample month, each from data known at that month."""
    y = np.log(fwd[f])
    own = pd.DataFrame({"t": np.log(trail[f]), "l": np.log(long_[f])}, index=per)
    out = {"trailing": np.log(trail[f]).values.copy(), "linear": np.full(len(per), np.nan), "lightgbm": np.full(len(per), np.nan)}
    for i in range(len(per)):
        tr = np.where((mi <= mi[i] - GAP) & y.notna().values & own.notna().all(axis=1).values)[0]
        if len(tr) < MIN_TRAIN or own.iloc[i].isna().any():
            out["linear"][i] = out["lightgbm"][i] = out["trailing"][i]
            continue
        A = sm.add_constant(own.iloc[tr], has_constant="add")
        b = np.linalg.lstsq(A.values, y.iloc[tr].values, rcond=None)[0]
        out["linear"][i] = float(np.r_[1.0, own.iloc[i].values] @ b)
        m = lgb.LGBMRegressor(n_estimators=200, learning_rate=0.05, num_leaves=8, max_depth=3, min_child_samples=20,
                              subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0, random_state=7,
                              n_jobs=1, verbose=-1, deterministic=True, force_row_wise=True)
        m.fit(X.iloc[tr], y.iloc[tr])
        out["lightgbm"][i] = float(m.predict(X.iloc[[i]])[0])
    return out, y.values


JS = r"""([nowT, thenT]) => {
  const S = D.samples, out = {real: {}, pred: {}};
  for (const k of Object.keys(PRESETS)) {
    const v = {...PRESETS[k]}, vh = hedged(v), pred = [], real = [], VM0 = {...VMED};
    for (const [i] of BT_POOLS) {
      for (const f of D.factors) { const a = S.slice(0, i + 1).map(s => s[VOFF + F[f]]).filter(x => x).sort((x, y) => x - y); VMED[f] = a.length ? a[Math.floor(a.length / 2)] : null; }
      const at = S[i], ctx = {e0: at[3], infl0: at[4]}, target = nowT ? nowT[i] : volAt(at);
      const elig = []; for (let j = 0; j < S.length && MI[j] <= MI[i] - 14; j++) elig.push(j);
      let pool = elig.filter(j => S[j][1] === S[i][1]); if (pool.length < 12) pool = elig;
      const moves = j => Object.fromEntries(D.factors.map(f => {
        const x = fval(S[j], f), vt = target[f];
        let vs = thenT ? thenT[j][f] : S[j][VOFF + F[f]];
        if (vs && VMED[f]) vs = Math.max(vs, VMED[f]);
        return [f, (!vt || !vs) ? x : x * Math.min(VCLIP[1], Math.max(VCLIP[0], vt / vs))];
      }));
      pred.push(quant(pool.map(j => -projTotal(vh, moves(j), ctx)), .05));
      real.push(-projTotal(vh, rawMoves(at), ctx));
    }
    Object.assign(VMED, VM0);
    out.pred[k] = pred; out.real[k] = real; out.ebit = out.ebit || {}; out.ebit[k] = v.ebit;
  }
  out.idx = BT_POOLS.map(([i]) => i);
  return out;
}"""


def kupiec(n, x, p=TAU):
    ph = x / n
    l0 = (n - x) * np.log(1 - p) + x * np.log(p)
    l1 = (x * np.log(ph) if x else 0) + ((n - x) * np.log(1 - ph) if x < n else 0)
    return float(stats.chi2.sf(max(0.0, -2 * (l0 - l1)), 1))


pinball = lambda y, q: (TAU - (y < q)) * (y - q)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def main():
    fc, ylog = {}, {}
    for f in fac:
        fc[f], ylog[f] = forecasts(f)
        print("forecast", f, flush=True)
    # (a) how good are the forecasts? log volatility over the next 12 months, the 213 backtest months
    bt_months = [i for i in range(len(per)) if "2008-01" <= per[i] <= "2025-09"]
    acc = []
    for f in fac:
        y = ylog[f][bt_months]
        ok = ~np.isnan(y) & ~np.isnan(fc[f]["trailing"][bt_months])
        for m in ("trailing", "linear", "lightgbm"):
            e = fc[f][m][bt_months][ok] - y[ok]
            v_ratio = np.exp(2 * (y[ok] - fc[f][m][bt_months][ok]))
            acc.append(dict(factor=f, model=m, rmse=float(np.sqrt(np.mean(e ** 2))), bias=float(np.mean(e)),
                            qlike=float(np.mean(v_ratio - np.log(v_ratio) - 1))))
    A = pd.DataFrame(acc)
    A.to_csv(HERE / "accuracy.csv", index=False)
    rel = A.pivot(index="factor", columns="model", values="rmse")
    print("\nRMSE of log volatility over the next 12 months (213 months):")
    print(rel.round(3).to_string())
    print("mean RMSE relative to trailing:", (rel[["linear", "lightgbm"]].div(rel.trailing, axis=0)).mean().round(3).to_dict())

    # (b) the bad year with each forecast, in the page's own model
    vol = {m: {str(i): {f: float(np.exp(fc[f][m][i])) if not np.isnan(fc[f][m][i]) else None for f in fac} for i in range(len(per))}
           for m in ("linear", "lightgbm")}
    variants = {"page": (None, None)}
    for m in ("linear", "lightgbm"):
        variants[f"{m} now"] = (vol[m], None)
        variants[f"{m} both"] = (vol[m], vol[m])
    # added after the first results (exploratory): the forecast may only add caution, never assume calmer markets
    # than the last 12 months, the same rule as the page's lower bound of 1 on the scale factor
    tr = {str(i): {f: (None if np.isnan(trail[f].iloc[i]) else float(trail[f].iloc[i])) for f in fac} for i in range(len(per))}
    for m in ("linear", "lightgbm"):
        mx = {i: {f: (max(v, tr[i][f]) if v is not None and tr[i][f] is not None else tr[i][f]) for f, v in d.items()} for i, d in vol[m].items()}
        variants[f"{m} max"] = (mx, None)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(REPO / "docs")))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    res = {}
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page(bypass_csp=True)
        pg.goto(f"http://127.0.0.1:{httpd.server_address[1]}/?lang=en", wait_until="networkidle")
        for name, (now, then) in variants.items():
            res[name] = pg.evaluate(JS, [now, then])
            print("bad year with", name, flush=True)
        br.close()
    rows = []
    base = res["page"]
    for name, r in res.items():
        for biz in r["pred"]:
            y = np.array(r["real"][biz]) / r["ebit"][biz] * 100
            q = np.array(r["pred"][biz]) / r["ebit"][biz] * 100
            q0 = np.array(base["pred"][biz]) / r["ebit"][biz] * 100
            x = int((y < q).sum())
            rows.append(dict(variant=name, business=biz, breaches=x, rate=x / len(y), kupiec_p=kupiec(len(y), x),
                             quantile_score=float(pinball(y, q).mean() / pinball(y, q0).mean()), mean_bad_year=float(q.mean())))
    R = pd.DataFrame(rows)
    R.to_csv(HERE / "results.csv", index=False)
    P = R.groupby("variant", sort=False).agg(breaches=("breaches", "sum"), quantile_score=("quantile_score", "mean"),
                                             mean_bad_year=("mean_bad_year", "mean"))
    P["rate"] = P.breaches / (213 * 6)
    P["kupiec_passes"] = R.assign(ok=R.kupiec_p > .05).groupby("variant", sort=False).ok.sum()
    print("\nAll six examples pooled (quantile score: page model = 1)")
    print(P.round(3).to_string())
    print("\nShare of months the bad year was beaten, per example")
    print((R.pivot(index="business", columns="variant", values="rate")[list(res)] * 100).round(1).to_string())


if __name__ == "__main__":
    main()
