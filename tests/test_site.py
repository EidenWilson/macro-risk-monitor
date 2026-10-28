"""Browser checks for the pages around the tool: the portfolio stress test (bond arithmetic, file reading, scenarios,
export) and the page for tax advisors and banks, in English and German, on a desktop and a phone.

    python build_page.py && python site/build_site.py && python tests/test_site.py
"""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from playwright.sync_api import sync_playwright  # noqa: E402
from test_page import check, failures, page_with, serve  # noqa: E402

GERMAN_EXPORT = """Gattungsbezeichnung;ISIN;Bestand;Währung;Kurs;Kurswert in EUR;Fälligkeit
"2,6000 % Belgien, Königreich EO-Obl. Lin. 2025(30)";BE0000365743;140;%;98,270;137.578,00;22.10.2030
"6,5000 % Bundesrep.Deutschland Anl.v.1997 (2027)";DE0001135044;260;%;104,790;272.454,00;
"1,8500 % Österreich, Republik EO-Medium-Term Notes 2022(49)";AT0000A2Y8G4;120;%;70,740;84.888,00;
Invesco Physical Markets PLC O.E. ETC Gold;XS2183935274;1.800;EUR;104,600;188.280,00;
"""


def num(s):
    """'−172,416' or '3,027,299' (English page) -> float"""
    return float(s.replace("−", "-").replace(",", "").replace("€", "").replace("+", "").strip())


def test_bond_math(browser, base):
    print("portfolio: bond arithmetic and reading files")
    pg = page_with(browser, base + "portfolio/")
    r = pg.evaluate("""() => { const T = PT_TEST, s = new Date(Date.UTC(2026, 2, 31));
      const z = T.bondMath(0, new Date(Date.UTC(2052, 7, 15)), s, 40.89, 1), par = T.bondMath(3, new Date(Date.UTC(2036, 2, 31)), s, 100, 1),
            bund = T.bondMath(6.5, new Date(Date.UTC(2027, 6, 4)), s, 104.79, 1), btp = T.bondMath(4, new Date(Date.UTC(2031, 9, 30)), s, 103.22, 2);
      return {z, par, bund, btp, n: [['1.234,56', ';'], ['1,234.56', ','], ['98,270', ';'], ['1.800', ';'], ['104.600', ','], ['−4.705', ';'], ['−4,705', ';']].map(([v, d]) => T.toNum(v, d)),
        m: ['6,5000 % Bundesrep.Deutschland Anl.v.1997 (2027)', '1,8500 % Österreich, Republik EO-Medium-Term Notes 2022(49)', '5,5000 % Frankreich EO-OAT 1997(29)'].map(n => T.maturityFromName(n).d.getUTCFullYear()),
        c: T.couponFromName('3,4500 % Spanien EO-Obligaciones 2024(34)')}; }""")
    years = r["z"]["years"]
    check(abs(r["z"]["y"] - ((100 / 40.89) ** (1 / years) - 1)) < 1e-6, f"zero-coupon yield matches the closed form ({r['z']['y']:.5f})")
    check(abs(r["z"]["dur"] - years / (1 + r["z"]["y"])) < 0.01, f"zero-coupon duration is maturity / (1 + yield) ({r['z']['dur']:.2f})")
    check(abs(r["par"]["y"] - 0.03) < 2e-4 and 8.4 < r["par"]["dur"] < 8.7, f"a 3% bond priced at 100 yields 3% with duration about 8.5 ({r['par']})")
    check(abs(r["bund"]["y"] - 0.0258) < 5e-4, f"6.5% Bund 2027 at 104.79: yield about 2.58% after accrued interest ({r['bund']['y']:.4%})")
    check(r["btp"]["y"] > 0.03 and r["btp"]["dur"] > 4.5, f"half-yearly BTP coupons are handled ({r['btp']})")
    check(r["n"] == [1234.56, 1234.56, 98.27, 1800, 104.6, -4705, -4.705], f"numbers in German and English formats {r['n']}")
    check(r["m"] == [2027, 2049, 2029], f"maturity years read from report names {r['m']}")
    check(r["c"] == 3.45, "coupon read from the name")
    h = pg.evaluate("(csv) => { const r = PT_TEST.load(csv); return r.holdings.map(h => ({kind: h.kind, country: h.country, mv: h.mv, flags: h.flags.length, exact: !!h.mat})); }", GERMAN_EXPORT)
    check([x["kind"] for x in h] == ["gov", "gov", "gov", "gold"], f"a German report export is read (semicolons, decimal commas) {h}")
    check([x["mv"] for x in h] == [137578, 272454, 84888, 188280], "market values in German number format")
    check(h[0]["country"] == "BE" and h[0]["flags"] == 0 and h[1]["flags"] == 1, "exact maturities are used; a year from the name is flagged as mid-year")
    check(not pg.errors, f"no console errors {pg.errors[:2]}")
    pg.close()


def test_example(browser, base):
    print("portfolio: the example fund, scenarios and export")
    pg = page_with(browser, base + "portfolio/")
    pg.click("#example")
    pg.wait_for_selector("#out:not([hidden])")
    figs = pg.inner_text("#figs")
    check("€3,027,299" in figs, "the example's market value matches page 6 of the report (€3,027,298.73)")
    check(pg.inner_text("#countries").count("%") >= 9 and "No country" not in pg.inner_text("#countries"), "every bond has a country, Portugal included")
    totals = {}
    for s in ("up", "down", "infl", "crisis", "crash", "custom"):
        pg.click(f'[data-s="{s}"]')
        rows = [r.split("\t") for r in pg.inner_text("#impact").split("\n") if "\t" in r][1:]
        vals = [num(r[1]) for r in rows]
        totals[s] = vals
        check(abs(vals[0] + sum(vals[1:6]) - vals[6]) <= 3, f"{s}: the parts add up to the value after the shock ({vals[0]:,.0f} + {sum(vals[1:6]):,.0f} = {vals[6]:,.0f})")
        foot = num(pg.inner_text("#holdFoot").split("\t")[-1])
        check(abs(foot - sum(vals[1:6])) <= 3, f"{s}: the holdings table adds up to the same result")
    up, down = totals["up"], totals["down"]
    check(-0.07 < up[1] / up[0] < -0.05 and up[2] == 0, f"+100 bp costs 5 to 7% (duration about 6.7) and moves no spreads ({up[1] / up[0]:.2%})")
    check(down[1] > -up[1], "convexity: a fall in yields gains more than a rise loses")
    check(totals["crisis"][2] < 0 and totals["crisis"][1] > 0, "euro crisis: spreads lose, German yields falling helps")
    check(totals["infl"][3] > 0, "inflation shock: higher expected inflation softens the inflation-linked bond's loss")
    pg.click('[data-s="custom"]')
    pg.evaluate("() => { const i = document.querySelector('input[data-f=core]'); i.value = '0'; i.dispatchEvent(new Event('input', {bubbles: true})); }")
    v = [num(r.split("\t")[1]) for r in [r for r in pg.inner_text("#impact").split("\n") if "\t" in r][1:]]
    check(v[1] == 0, "your own scenario: setting German yields to 0 removes the rate effect")
    with pg.expect_download() as d:
        pg.click("#exportBtn")
    path = d.value.path()
    lines = pathlib.Path(path).read_text(encoding="utf-8").strip().split("\n")
    check(len(lines) == 22 and lines[0].startswith("name,isin,type") and "pnl_crisis_eur" in lines[0], f"the export has a header and 21 holdings ({len(lines)} lines)")
    check(not pg.errors, f"no console errors {pg.errors[:2]}")
    pg.close()


def test_pages(browser, base):
    print("pages: advisors and portfolio in English and German, desktop and phone, 404")
    for path in ("advisors/", "advisors/de/", "portfolio/", "portfolio/de/"):
        for width in (1440, 390):
            pg = page_with(browser, base + path, width)
            html = pg.content()
            check(not re.search(r"undefined|NaN|\{\{|\{0\}", pg.inner_text("body")), f"{path} at {width}px: no broken text")
            check(pg.evaluate("document.documentElement.scrollWidth") <= width, f"{path} at {width}px: no sideways scroll")
            if width == 1440:
                ld = pg.evaluate("[...document.querySelectorAll('script[type=\"application/ld+json\"]')].map(s => JSON.parse(s.textContent))")
                check(bool(ld), f"{path}: structured data parses")
                check(html.count('<link rel="alternate" hreflang="en"') == 1 and html.count('<link rel="alternate" hreflang="de"') == 1 and html.count('rel="canonical"') == 1, f"{path}: canonical and language alternates")
                check(pg.evaluate("document.querySelectorAll('h1').length") == 1, f"{path}: one h1")
                lang = "de" if path.endswith("de/") else "en"
                check(pg.evaluate("document.documentElement.lang") == lang, f"{path}: lang={lang}")
            check(not pg.errors, f"{path} at {width}px: no console errors {pg.errors[:2]}")
            pg.close()
    pg = page_with(browser, base + "advisors/")
    s = pg.inner_text("#why")
    check("Stagflation" in s and "%" in s and "€14.60" in s, "advisors: the economy now comes from the tool's data")
    rows = pg.inner_text(".hero .ledger").split("\n")
    check(any("72,000" in r for r in rows) and any("−44,300" in r for r in rows), "advisors: the example restaurant matches the tool")
    check(pg.evaluate("[...document.querySelectorAll('a[href]')].every(a => a.getAttribute('href') && !a.getAttribute('href').includes('undefined'))"), "advisors: every link has a target")
    pg.close()
    pg = page_with(browser, base + "404.html")
    check("not in the books" in pg.inner_text("h1"), "404 page renders")
    pg.close()


if __name__ == "__main__":
    base = serve()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for t in (test_bond_math, test_example, test_pages):
            t(browser, base)
        browser.close()
    print(f"\n{len(failures)} failed" if failures else "\nall checks passed")
    sys.exit(1 if failures else 0)
