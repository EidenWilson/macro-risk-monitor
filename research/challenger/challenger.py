"""Step 2: can a LightGBM quantile model beat the page's filtered historical simulation (FHS) at the 1-in-20 bad year?

Same 213 forecast months (2008-01 to 2025-09), same six example businesses, same realized outcomes (from the page's
own business model, see extract.py), and no look-ahead for any model. Writes results.csv; prints the summary.
Run from the repo root:  python research/challenger/challenger.py
"""
import json, pathlib, sys, warnings
import numpy as np, pandas as pd
import lightgbm as lgb
import statsmodels.api as sm
from scipy import stats

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
from build_data import FACTORS          # factor -> (series id, kind)
from macro_config import LAG            # publication lag per series

warnings.filterwarnings("ignore")
TAU, GAP = 0.05, 14                     # quantile; a window's outcome counts as known 12 + 2 months after it starts
D = json.load(open(REPO / "docs" / "data.json"))
PM = json.load(open(HERE / "page_model.json"))
fac, S = D["factors"], D["samples"]
NF, SOFF = len(fac), 5
VOFF = SOFF + NF
per = [s[0] for s in S]
mi = np.array([int(p[:4]) * 12 + int(p[5:7]) - 1 for p in per])
assert np.all(np.diff(mi) == 1), "samples must be consecutive months"

# ---------------- features, each knowable at the start of its window ----------------
num = lambda x: np.nan if x is None else float(x)
Fw = np.array([[num(s[SOFF + k]) for k in range(NF)] for s in S])          # forward 12-month moves (outcome side)
Vt = np.array([[num(s[VOFF + k]) for k in range(NF)] for s in S])          # trailing volatility, already lagged
X = pd.DataFrame(index=per)
for r in ["Recovery", "Overheating", "Stagflation", "Contraction"]:
    X["phase_" + r] = [1.0 if s[1] == r else 0.0 for s in S]               # business-cycle phase, from lagged data
X["euribor"] = [num(s[3]) for s in S]                                       # lag 0
X["inflation_yoy"] = pd.Series([num(s[4]) for s in S], index=per).shift(LAG["HICP_DE"]).values
for k, f in enumerate(fac):
    lag = LAG.get(FACTORS[f][0], 0)
    X["vol_" + f] = Vt[:, k]
    X["past12_" + f] = pd.Series(Fw[:, k], index=per).shift(12 + lag).values   # last published 12-month move
FULL = list(X.columns)
COMPACT = ["phase_Stagflation", "phase_Contraction", "euribor", "inflation_yoy",
           "vol_energy", "vol_rate", "past12_energy", "past12_oil"]


def lgb_q(Xtr, ytr, Xte):
    m = lgb.LGBMRegressor(objective="quantile", alpha=TAU, n_estimators=150, learning_rate=0.05, num_leaves=4,
                          max_depth=2, min_child_samples=15, subsample=0.8, subsample_freq=1, colsample_bytree=0.7,
                          reg_lambda=1.0, random_state=7, n_jobs=1, verbose=-1, deterministic=True, force_row_wise=True)
    m.fit(Xtr, ytr)
    return float(m.predict(Xte)[0])


def linear_q(Xtr, ytr, Xte):
    ok = Xtr.notna().all(axis=1)
    Xtr, ytr = Xtr[ok], ytr[ok]
    mu, sd = Xtr.mean(), Xtr.std().replace(0, 1)
    Z, z = (Xtr - mu) / sd, ((Xte - mu) / sd).fillna(0)
    res = sm.QuantReg(ytr, sm.add_constant(Z, has_constant="add")).fit(q=TAU, max_iter=5000)
    return float(np.asarray(res.predict(sm.add_constant(z, has_constant="add")))[0])


def kupiec(n, x, p=TAU):
    ph = x / n
    l0 = (n - x) * np.log(1 - p) + x * np.log(p)
    l1 = (x * np.log(ph) if x else 0) + ((n - x) * np.log(1 - ph) if x < n else 0)
    return float(stats.chi2.sf(max(0.0, -2 * (l0 - l1)), 1))


pinball = lambda y, q: (TAU - (y < q)) * (y - q)


def dm_test(la, lb, lags=12):
    """Diebold-Mariano on the loss difference, HAC (Newey-West) because 12-month outcomes overlap."""
    d = np.asarray(la) - np.asarray(lb)
    fit = sm.OLS(d, np.ones_like(d)).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
    return float(fit.params[0]), float(fit.pvalues[0])


def fit_all():
    """Every model's forecast for every backtest month, per business (% of profit)."""
    rows, series = [], {}
    for biz, b in PM["biz"].items():
        y_all = np.array(b["real"]) / b["ebit"] * 100                       # realized profit change, % of profit
        idx = b["idx"]
        fc = {m: [] for m in ["naive", "fhs", "lgb_full", "lgb_compact", "linear"]}
        for i in idx:
            tr = np.where(mi <= mi[i] - GAP)[0]                             # outcomes fully published by month i
            ytr = pd.Series(y_all[tr], index=X.index[tr])
            fc["naive"].append(float(np.quantile(ytr, TAU)))
            fc["lgb_full"].append(lgb_q(X.iloc[tr][FULL], ytr, X.iloc[[i]][FULL]))
            fc["lgb_compact"].append(lgb_q(X.iloc[tr][COMPACT], ytr, X.iloc[[i]][COMPACT]))
            fc["linear"].append(linear_q(X.iloc[tr][COMPACT], ytr, X.iloc[[i]][COMPACT]))
        fc["fhs"] = list(np.array(b["strict"]) / b["ebit"] * 100)            # the page model, without look-ahead
        y = y_all[idx]
        L = {m: pinball(y, np.array(q)) for m, q in fc.items()}
        crisis = [k for k, i in enumerate(idx) if "2021-03" <= per[i] <= "2023-06"]
        for m, q in fc.items():
            q = np.array(q); br = y < q; x = int(br.sum())
            d, p = dm_test(L[m], L["fhs"]) if m != "fhs" else (0.0, 1.0)
            rows.append(dict(business=biz, model=m, n=len(y), breaches=x, rate=x / len(y), kupiec_p=kupiec(len(y), x),
                             pinball=float(L[m].mean()), rel_to_fhs=float(L[m].mean() / L["fhs"].mean()), dm_diff=d, dm_p=p,
                             mean_forecast=float(q.mean()), breaches_2021_23=int(br[crisis].sum())))
        series[biz] = {"real": y, **{m: np.array(q) for m, q in fc.items()}}
        print(biz, "done", flush=True)
    return pd.DataFrame(rows), series


def report(R, series):
    pd.set_option("display.width", 200)
    models = ["fhs", "naive", "lgb_full", "lgb_compact", "linear"]
    P = R.groupby("model").agg(breaches=("breaches", "sum"), n=("n", "sum"), quantile_score=("rel_to_fhs", "mean"),
                               mean_forecast=("mean_forecast", "mean"), breaches_2021_23=("breaches_2021_23", "sum"))
    P["rate"] = P.breaches / P.n
    P["kupiec_passes"] = R.assign(ok=R.kupiec_p > .05).groupby("model").ok.sum()
    print("\nAll six businesses pooled (quantile score: mean pinball loss, page model = 1)")
    print(P.loc[models].round(3).to_string())
    print("\nShare of months the bad year was beaten, per business")
    print((R.pivot(index="business", columns="model", values="rate")[models] * 100).round(1).to_string())
    # pooled Diebold-Mariano: the loss difference averaged over the six businesses each month, HAC with 12 lags
    L = {m: np.mean([pinball(s["real"], s[m]) for s in series.values()], axis=0) for m in models}
    print()
    for m in models[1:]:
        d = L[m] - L["fhs"]
        f = sm.OLS(d, np.ones_like(d)).fit(cov_type="HAC", cov_kwds={"maxlags": 12})
        print(f"Diebold-Mariano, {m} vs page model: {f.params[0] / L['fhs'].mean():+.0%} quantile loss, p = {f.pvalues[0]:.3f}")
    # why: the last restaurant fit, in sample against out of sample, and what it leaned on
    b = PM["biz"]["restaurant"]; y_all = np.array(b["real"]) / b["ebit"] * 100; i = b["idx"][-1]
    tr = np.where(mi <= mi[i] - GAP)[0]
    m = lgb.LGBMRegressor(objective="quantile", alpha=TAU, n_estimators=150, learning_rate=0.05, num_leaves=4, max_depth=2,
                          min_child_samples=15, subsample=0.8, subsample_freq=1, colsample_bytree=0.7, reg_lambda=1.0,
                          random_state=7, n_jobs=1, verbose=-1, deterministic=True, force_row_wise=True)
    m.fit(X.iloc[tr][FULL], y_all[tr])
    ins = float((y_all[tr] < m.predict(X.iloc[tr][FULL])).mean())
    oos = float(R.query("business == 'restaurant' and model == 'lgb_full'").rate.iloc[0])
    gain = pd.Series(m.booster_.feature_importance("gain"), index=FULL).sort_values(ascending=False)
    print(f"\nRestaurant, last LightGBM fit: {len(tr)} windows, beaten {ins:.1%} in sample, {oos:.1%} out of sample")
    print("Its strongest inputs (share of gain):", (gain / gain.sum()).head(5).round(3).to_dict())


if __name__ == "__main__":
    R, series = fit_all()
    R.to_csv(HERE / "results.csv", index=False)
    report(R, series)
