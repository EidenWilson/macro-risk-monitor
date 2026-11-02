"""Step 3: sensitivity of the LightGBM challenger to its settings (38 inputs). If no setting is calibrated, the
conclusion does not hinge on the settings chosen before the first run. Run from the repo root:
python research/challenger/grid.py
"""
import itertools, pathlib, sys
from multiprocessing import Pool
import numpy as np, pandas as pd, lightgbm as lgb
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from challenger import X, FULL, PM, mi, GAP, TAU, pinball

GRID = list(itertools.product([4, 8], [5, 20], [150, 500]))   # num_leaves, min_child_samples, n_estimators


def run(cfg):
    leaves, mcs, trees = cfg
    out = []
    for biz, b in PM["biz"].items():
        y_all = np.array(b["real"]) / b["ebit"] * 100
        q = []
        for i in b["idx"]:
            tr = np.where(mi <= mi[i] - GAP)[0]
            m = lgb.LGBMRegressor(objective="quantile", alpha=TAU, n_estimators=trees, learning_rate=0.05, num_leaves=leaves,
                                  min_child_samples=mcs, subsample=0.8, subsample_freq=1, colsample_bytree=0.7, reg_lambda=1.0,
                                  random_state=7, n_jobs=1, verbose=-1, deterministic=True, force_row_wise=True)
            m.fit(X.iloc[tr][FULL], y_all[tr])
            q.append(float(m.predict(X.iloc[[i]][FULL])[0]))
        y, q = y_all[b["idx"]], np.array(q)
        fhs = np.array(b["strict"]) / b["ebit"] * 100
        out.append(dict(leaves=leaves, min_child=mcs, trees=trees, business=biz, breaches=int((y < q).sum()),
                        rel=float(pinball(y, q).mean() / pinball(y, fhs).mean())))
    return out


if __name__ == "__main__":
    with Pool(2) as p:
        res = [r for rs in p.map(run, GRID) for r in rs]
    G = pd.DataFrame(res)
    G.to_csv(pathlib.Path(__file__).resolve().parent / "grid.csv", index=False)
    summ = G.groupby(["leaves", "min_child", "trees"]).agg(breaches=("breaches", "sum"), rel_pinball=("rel", "mean"))
    summ["rate"] = summ.breaches / 1278
    print(summ.round(3))
