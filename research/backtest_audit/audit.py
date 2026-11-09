"""Why do the importer and the print shop fail the backtest, and is the test itself right?

1. The test. The page backtests 213 monthly forecasts of the next 12 months. Neighbouring windows share 11 of their
   12 months, so one bad year shows up as a run of up to 12 misses, and the 213 forecasts hold only about 18
   independent years. Kupiec's test assumes independent forecasts, so on overlapping windows it rejects far too
   often. The standard correction: test the miss rate with Newey-West (HAC) errors over the 11 lags the overlap
   creates. Both are reported; the overlap-adjusted one is the right one for this backtest.
2. The misses. For the two failing examples: when they happened, and whether any past window in the forecast's
   pool held a move as large as the one that came (if not, no reweighting of history can catch it).
3. One principled alternative, set before looking at results: the full pool of past windows instead of only those
   from the same business-cycle phase (more windows, more extremes), scored on all six examples.

Run from the repo root (Playwright): python research/backtest_audit/audit.py
"""
import functools
import http.server
import json
import pathlib
import threading

import numpy as np
import pandas as pd
import statsmodels.api as sm
from playwright.sync_api import sync_playwright
from scipy import stats

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
TAU = 0.05
KEY = {"importer": "fx", "print": "ppi_paper"}

JS = r"""(key) => {
  const S = D.samples, out = {};
  for (const k of Object.keys(PRESETS)) {
    const v = {...PRESETS[k]}, vh = hedged(v), bt = backtest(v), full = [], ext = [];
    for (const [i] of BT_POOLS) {
      const at = S[i], target = volAt(at), ctx = {e0: at[3], infl0: at[4]};
      const elig = []; for (let j = 0; j < S.length && MI[j] <= MI[i] - 12; j++) elig.push(j);
      full.push(quant(elig.map(j => -projTotal(vh, movesOf(S[j], target), ctx)), .05));
      const f = key[k];
      if (f) {
        const pool = BT_POOLS.find(([ii]) => ii === i)[1];
        const xs = pool.map(j => movesOf(S[j], target)[f]);
        ext.push({real: rawMoves(at)[f], lo: Math.min(...xs), hi: Math.max(...xs), n: xs.length});
      }
    }
    out[k] = {ebit: v.ebit, p: bt.map(b => b.p), pred: bt.map(b => b.pred), real: bt.map(b => b.real), full, ext};
  }
  out.episodes = EPISODES;
  return out;
}"""


def kupiec(n, x, p=TAU):
    ph = x / n
    l0 = (n - x) * np.log(1 - p) + x * np.log(p)
    l1 = (x * np.log(ph) if x else 0) + ((n - x) * np.log(1 - ph) if x < n else 0)
    return float(stats.chi2.sf(max(0.0, -2 * (l0 - l1)), 1))


def hac(breach, p=TAU, lags=11):
    """Miss rate against p with Newey-West errors: the test for overlapping 12-month windows."""
    b = np.asarray(breach, float) - p
    fit = sm.OLS(b, np.ones_like(b)).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
    return float(fit.pvalues[0]), float(fit.bse[0])


def episode(period, eps):
    a = int(period[:4]) * 12 + int(period[5:7]) - 1
    best, bo = None, 0
    for key, s0, s1, _ in eps:
        m0, m1 = int(s0[:4]) * 12 + int(s0[5:7]) - 1, int(s1[:4]) * 12 + int(s1[5:7]) - 1
        o = min(a + 12, m1) - max(a, m0)
        if o > bo:
            best, bo = key, o
    return best or period[:4]


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def main():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(REPO / "docs")))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page(bypass_csp=True)
        pg.goto(f"http://127.0.0.1:{httpd.server_address[1]}/?lang=en", wait_until="networkidle")
        out = pg.evaluate(JS, KEY)
        br.close()
    eps = out.pop("episodes")
    rows, misses, dl = [], {}, []
    for biz, r in out.items():
        y, q, qf = (np.array(r[k]) / r["ebit"] * 100 for k in ("real", "pred", "full"))
        b, bf = y < q, y < qf
        n, x, xf = len(y), int(b.sum()), int(bf.sum())
        loss, loss_f = (TAU - b) * (y - q), (TAU - bf) * (y - qf)
        dl.append(loss_f - loss)
        dm = sm.OLS(loss_f - loss, np.ones(n)).fit(cov_type="HAC", cov_kwds={"maxlags": 12})
        ph, se = hac(b)
        rows.append(dict(business=biz, n=n, misses=x, rate=round(100 * x / n, 1), kupiec_p=round(kupiec(n, x), 3),
                         overlap_adjusted_p=round(ph, 3), se_pp=round(100 * se, 1),
                         full_pool_misses=xf, full_pool_rate=round(100 * xf / n, 1),
                         full_pool_kupiec_p=round(kupiec(n, xf), 3), full_pool_adjusted_p=round(hac(bf)[0], 3),
                         full_pool_quantile_score=round(float(np.mean((TAU - (y < qf)) * (y - qf)) / np.mean((TAU - (y < q)) * (y - q))), 3),
                         full_pool_dm_p=round(float(dm.pvalues[0]), 3),
                         full_pool_mean_bad_year=round(float(qf.mean()), 1), mean_bad_year=round(float(q.mean()), 1)))
        misses[biz] = pd.Series([episode(p_, eps) for p_, hit in zip(r["p"], b) if hit]).value_counts().to_dict()
        if biz in KEY:
            e = pd.DataFrame(r["ext"], index=r["p"])
            e["miss"] = b
            m = e[e.miss]
            f = KEY[biz]
            adverse = (m.real < m.lo) if f == "fx" else (m.real > m.hi)      # a weaker euro hurts the importer; dearer paper the print shop
            print(f"\n{biz}: {x} misses; key factor {f}; months whose move lay beyond every scaled move in the pool: "
                  f"{int(adverse.sum())} of {x}")
            print(m.assign(real=m.real.round(3), lo=m.lo.round(3), hi=m.hi.round(3)).groupby(m.index.str[:4]).agg(
                misses=("miss", "sum"), real_min=("real", "min"), real_max=("real", "max"), pool_lo=("lo", "min"), pool_hi=("hi", "max")).to_string())
    T = pd.DataFrame(rows)
    T.to_csv(HERE / "results.csv", index=False)
    pd.set_option("display.width", 220)
    print("\n" + T.to_string(index=False))
    print("\nwhere the misses came from:", json.dumps(misses))
    d = np.mean(dl, axis=0)                      # the six examples together, month by month
    dm = sm.OLS(d, np.ones_like(d)).fit(cov_type="HAC", cov_kwds={"maxlags": 12})
    print(f"full pool against the phase pool, all six: misses {T.full_pool_misses.sum()} vs {T.misses.sum()} of {T.n.sum()}; "
          f"quantile score {T.full_pool_quantile_score.mean():.3f}; Diebold-Mariano p = {dm.pvalues[0]:.3f}")


if __name__ == "__main__":
    main()
