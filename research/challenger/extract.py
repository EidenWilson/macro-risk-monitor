"""Step 1: take the page's own model outputs for the six example businesses, from the built page in a headless browser.

For every 12-month window since 2001: the realized profit change. For every backtest month since 2008: the page's
bad-year forecast (filtered historical simulation), as on the page and with no look-ahead at all (strict publication
lags for past windows, volatility floor from data known at the time). Writes page_model.json next to this file.
Run from the repo root after `python build_page.py`:  python research/challenger/extract.py
"""
import functools, http.server, json, pathlib, threading
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
DOCS = HERE.parents[1] / "docs"
JS = r"""() => {
  const S = D.samples, out = {factors: D.factors, periods: S.map(s => s[0]), biz: {}};
  for (const k of Object.keys(PRESETS)) {
    const v = {...PRESETS[k]}, vh = hedged(v);
    const real = S.map(s => -projTotal(vh, rawMoves(s), {e0: s[3], infl0: s[4]}));
    const bt = backtest(v), strict = [], idx = [], VM0 = {...VMED};
    for (const [i] of BT_POOLS) {
      for (const f of D.factors) { const a = S.slice(0, i + 1).map(s => s[VOFF + F[f]]).filter(x => x).sort((x, y) => x - y); VMED[f] = a.length ? a[Math.floor(a.length / 2)] : null; }
      const at = S[i], target = volAt(at), ctx = {e0: at[3], infl0: at[4]};
      const elig = []; for (let j = 0; j < S.length && MI[j] <= MI[i] - 14; j++) elig.push(j);
      let pool = elig.filter(j => S[j][1] === S[i][1]); if (pool.length < 12) pool = elig;
      strict.push(quant(pool.map(j => -projTotal(vh, movesOf(S[j], target), ctx)), .05));
      idx.push(i);
    }
    Object.assign(VMED, VM0);
    out.biz[k] = {ebit: v.ebit, real, idx, page: bt.map(b => b.pred), strict};
  }
  return out;
}"""


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


if __name__ == "__main__":
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(DOCS)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(bypass_csp=True)
        pg.goto(f"http://127.0.0.1:{httpd.server_address[1]}/?lang=en", wait_until="networkidle")
        out = pg.evaluate(JS)
        browser.close()
    (HERE / "page_model.json").write_text(json.dumps(out))
    for k, b in out["biz"].items():
        y = [b["real"][i] for i in b["idx"]]
        print(f"{k:10s} page model beaten {sum(r < q for r, q in zip(y, b['page']))} of {len(y)}, "
              f"without look-ahead {sum(r < q for r, q in zip(y, b['strict']))}")
