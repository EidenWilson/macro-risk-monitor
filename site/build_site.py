"""Build the pages around the tool into docs/: the page for tax advisors and banks (/advisors/), the portfolio stress
test (/portfolio/), each in English and German, plus 404.html and sitemap.xml. Run it after build_page.py:

    python build_page.py && python site/build_site.py

The advisors page shows figures from the tool itself (the example restaurant, the six examples ranked, the economy
now and the backtest). They are read from the built tool in a headless browser, the same way alerts/digest.py reads
the alert email's figures, so the page can never disagree with the tool. Its screenshots of the tool are taken in the
same way (docs/assets/shots/). The data refresh rebuilds both.
"""
import datetime as dt
import hashlib
import json
import pathlib
import shutil
import sys
from decimal import ROUND_HALF_UP, Decimal

from jinja2 import Environment, FileSystemLoader, select_autoescape

HERE = pathlib.Path(__file__).resolve().parent
ROOT, DOCS = HERE.parent, HERE.parent / "docs"
sys.path.insert(0, str(HERE))
from text import ADVISORS, COMMON, NOT_FOUND, PORTFOLIO, PORTFOLIO_JS  # noqa: E402

SITE = "https://eidenwilson.github.io/macro-risk-monitor/"
BASE = "/macro-risk-monitor/"
CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8")) if (HERE / "config.json").exists() else {}
LANGS = ["en", "de"]
TOOL_LANGS = ["en", "ar", "de", "es", "fr", "hi", "it", "pl", "pt", "ru", "tr", "uk", "zh"]
ICON = ("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><circle cx='16' cy='16' r='14' fill='%23ffffff' stroke='%231f2a3a' stroke-width='2.5'/>"
        "<path d='M16 2.5v27M2.5 16h27' stroke='%231f2a3a' stroke-width='1.6'/><circle cx='22.6' cy='9.4' r='4.2' fill='%23b3261e'/></svg>")

SNAPSHOT = r"""() => {
  const c = D.current, v = {...PRESETS.restaurant}, bt = backtest(v), n = bt.length, x = bt.filter(b => b.breach).length;
  const ex = Object.keys(PRESETS).map(key => { const p = {...PRESETS[key]}, p5 = outcomes(p, 'regime').q(.05); return {key, ebit: p.ebit, loss: Math.max(0, -p5)}; });
  const words = l => withLang(l, () => ({regime: t(c.regime), presets: t('presetNames'), month: monthLong(ym(c.asOf)),
    since: monthLong(ym(c.since)), wageMonth: monthLong(ym(D.minWage.effective))}));
  return {asOf: c.asOf, euribor: c.euribor, energy: c.energy, minWage: D.minWage, bt: {n, x, p: kupiec(n, x).pval}, ex, en: words('en'), de: words('de')};
}"""


def snapshot():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto((DOCS / "index.html").resolve().as_uri(), wait_until="load")
        snap = page.evaluate(SNAPSHOT)
        browser.close()
    return snap


# Screenshots of the tool for the advisors page, taken from the built tool on every build so they always show the
# same data as the page around them. WebP straight from Chromium (no image library needed), at twice the CSS size.
SHOTS = {"result": "section.hero", "clients": "section.clients", "backtest": "section[aria-labelledby=h-bt]"}
SHOT_END = {"backtest": "#btLegend"}  # stop below the chart's legend; the page states the backtest figures itself
SHOT_PAD = {"clients": 18, "backtest": 18}  # white margin around a card, so its text doesn't touch the frame


def screenshots():
    import base64
    from playwright.sync_api import sync_playwright
    out = DOCS / "assets" / "shots"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.webp"):
        old.unlink()
    sizes = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for lang in LANGS:
            ctx = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=2, locale="de-DE" if lang == "de" else "en-GB")
            page = ctx.new_page()
            page.goto((DOCS / "index.html").resolve().as_uri() + f"?plan=business&lang={lang}", wait_until="load")
            page.evaluate("document.fonts.ready")
            page.add_style_tag(content="#toast,.chat-fab,#askBtn{visibility:hidden!important} *{transition:none!important;animation:none!important}")
            cdp = ctx.new_cdp_session(page)

            def shoot(name):
                el = page.locator(SHOTS[name]).first
                el.scroll_into_view_if_needed()
                page.wait_for_timeout(150)
                r = page.evaluate("""([el, end, pad]) => { const b = el.getBoundingClientRect(), e = end ? document.querySelector(end).getBoundingClientRect() : b;
                    return {x: b.left + scrollX - pad, y: b.top + scrollY - pad, width: b.width + 2 * pad, height: (end ? e.bottom + 12 : b.bottom) - b.top + 2 * pad}; }""",
                                  [el.element_handle(), SHOT_END.get(name), SHOT_PAD.get(name, 0)])
                img = cdp.send("Page.captureScreenshot", {"format": "webp", "quality": 80, "captureBeyondViewport": True,
                                                          "clip": {**{k: round(v) for k, v in r.items()}, "scale": 2}})
                (out / f"{name}-{lang}.webp").write_bytes(base64.b64decode(img["data"]))
                sizes.setdefault(lang, {})[name] = {"src": f"assets/shots/{name}-{lang}.webp", "w": round(r["width"]), "h": round(r["height"])}

            shoot("result")
            # the six examples saved as clients, so the Business ranking has something to rank
            names = page.eval_on_selector_all("#presets button", "bs => bs.map(b => b.textContent.trim())")
            for i, name in enumerate(names):
                page.locator("#presets button").nth(i).click()
                page.fill("#clientName", name)
                page.click("#saveClient")
            page.locator("#presets button").first.click()
            shoot("clients")
            page.click("#relBtn")
            page.wait_for_function("document.querySelector('#bt') && document.querySelector('#bt').childElementCount > 3")
            shoot("backtest")
            ctx.close()
        browser.close()
    return sizes


# numbers as each language writes them (half up, like the page's Intl formatting)
def num(x, lang, d=0):
    q = Decimal(str(x)).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP)
    s = f"{abs(q):,.{d}f}"
    if lang == "de":
        s = s.replace(",", "_").replace(".", ",").replace("_", ".")
    return ("−" if q < 0 else "") + s


def pct(x, lang, d=0, sign=False):
    s = num(x, lang, d)
    if sign and Decimal(str(x)).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP) > 0:
        s = "+" + s
    return s + (chr(160) + "%" if lang == "de" else "%")


def eur(x, lang, d=0):
    s = num(abs(x), lang, d)
    sign = "−" if x < 0 else ""
    return f"{sign}{s}{chr(160)}€" if lang == "de" else f"{sign}€{s}"


def figures(snap, lang):
    T, w = ADVISORS[lang], snap[lang]
    r = next(e for e in snap["ex"] if e["key"] == "restaurant")
    lost = round(r["loss"] / 100) * 100
    left, lost_pct = r["ebit"] - lost, round(lost / r["ebit"] * 100)
    ranked = sorted(({"name": w["presets"][e["key"]], "share": e["loss"] / e["ebit"] * 100} for e in snap["ex"]), key=lambda e: -e["share"])
    top = ranked[0]["share"] or 1
    for e in ranked:
        e.update(w=max(2, round(e["share"] / top * 100, 1)), over=e["share"] > 40, share=pct(e["share"], lang))
    bt = snap["bt"]
    mw = snap["minWage"]
    bt_rate = pct(bt["x"] / bt["n"] * 100, lang, 1) if bt["n"] else "–"
    return {
        "shot_result_alt": T["shot_result_alt"].format(lost=eur(lost, lang), ebit=eur(r["ebit"], lang)),
        "shot_result_cap": T["shot_result_cap"].format(month=w["month"]),
        "shot_clients_alt": T["shot_clients_alt"].format(list=", ".join(f"{e['name']} {e['share']}" for e in ranked)),
        "shot_bt_alt": T["shot_bt_alt"].format(rate=bt_rate),
        "ex1_label": T["ex1_label"].format(month=w["month"]),
        "ebit": num(r["ebit"], lang), "loss": num(-lost, lang), "left": num(left, lang), "hundred": num(100, lang),
        "loss_pct": num(-lost_pct, lang), "left_pct": num(100 - lost_pct, lang), "left_w": max(0, 100 - lost_pct), "ranked": ranked,
        "regime": w["regime"], "since": T["now_econ_sub"].format(since=w["since"]),
        "euribor": pct(snap["euribor"], lang, 2), "energy": pct(snap["energy"], lang, 1, sign=True),
        "wage_to": eur(mw["to"], lang, 2), "wage_sub": T["now_wage_sub"].format(wage_month=w["wageMonth"], wage_from=eur(mw["from"], lang, 2)),
        "now_note": T["now_note"].format(month=w["month"]),
        "bt_rate": bt_rate,
        "bt_verdict": T["t_test_pass"] if bt["p"] > 0.05 else T["t_test_fail"], "bt_p": T["t_test_sub"].format(p=num(bt["p"], lang, 2)),
        "plans": [[n, who, eur(float(price), lang, 0 if float(price).is_integer() else 2), feats, cls] for n, who, price, feats, cls in T["plans"]],
    }


def jsonld_advisors(lang, T):
    app = {"@context": "https://schema.org", "@type": "WebApplication", "name": "Macro Risk Monitor", "url": SITE,
           "applicationCategory": "FinanceApplication", "operatingSystem": "Any (runs in the browser)", "inLanguage": TOOL_LANGS,
           "description": T["desc"], "offers": [{"@type": "Offer", "name": n, "price": p, "priceCurrency": "EUR"} for n, _, p, _, _ in T["plans"]]}
    faq = {"@context": "https://schema.org", "@type": "FAQPage", "inLanguage": lang,
           "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in T["faq"]]}
    return json.dumps([app, faq], ensure_ascii=False).replace("</", "<\\/")


def jsonld_portfolio(lang, T):
    return json.dumps({"@context": "https://schema.org", "@type": "WebApplication", "name": T["app_name"], "url": SITE + ("portfolio/de/" if lang == "de" else "portfolio/"),
                       "applicationCategory": "FinanceApplication", "operatingSystem": "Any (runs in the browser)", "inLanguage": lang,
                       "isPartOf": {"@type": "WebSite", "name": "Macro Risk Monitor", "url": SITE}, "description": T["desc"],
                       "offers": {"@type": "Offer", "price": "0", "priceCurrency": "EUR"}}, ensure_ascii=False).replace("</", "<\\/")


def main():
    snap = snapshot()
    shots = screenshots()
    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)
    assets = DOCS / "assets"
    (assets / "fonts").mkdir(parents=True, exist_ok=True)
    for f in ("site.css", "portfolio.js"):
        shutil.copyfile(HERE / "static" / f, assets / f)
    for f in ("sofia-sans-semi-condensed-latin.woff2", "sofia-sans-semi-condensed-latin-ext.woff2", "OFL.txt"):
        shutil.copyfile(ROOT / "assets" / "fonts" / f, assets / "fonts" / f)
    (DOCS / "portfolio").mkdir(exist_ok=True)
    for f in ("example-fund.csv", "template.csv"):
        shutil.copyfile(HERE / "data" / f, DOCS / "portfolio" / f)
    v = {k: hashlib.sha1((HERE / "static" / f).read_bytes()).hexdigest()[:8] for k, f in (("css", "site.css"), ("js", "portfolio.js"))}
    legal = all((DOCS / f).exists() and "[[" not in (DOCS / f).read_text(encoding="utf-8") for f in ("impressum.html", "datenschutz.html"))
    example = (HERE / "data" / "example-fund.csv").read_text(encoding="utf-8")
    written = []
    for key in ("advisors", "portfolio"):
        for lang in LANGS:
            path = f"{key}/" + ("de/" if lang == "de" else "")
            root = "../" * path.count("/")
            ctx = dict(lang=lang, key=key, path=path, root=root, site=SITE, v=v, icon=ICON, legal=legal, other=True,
                       c=COMMON[lang], alternates=[("en", f"{key}/"), ("de", f"{key}/de/")],
                       tool=root + ("?lang=de" if lang == "de" else ""), tool_business=root + ("?plan=business&lang=de" if lang == "de" else "?plan=business"),
                       here_en=root + f"{key}/", here_de=root + f"{key}/de/", contact=CONFIG.get("contact_email", ""))
            if key == "advisors":
                T = ADVISORS[lang]
                ctx.update(t=T, s=figures(snap, lang), shots=shots[lang], title=T["title"], desc=T["desc"], jsonld=jsonld_advisors(lang, T))
            else:
                T = PORTFOLIO[lang]
                pt = {"lang": lang, "locale": "de-DE" if lang == "de" else "en-GB", "text": PORTFOLIO_JS[lang], "example": example}
                ctx.update(t=T, title=T["title"], desc=T["desc"], jsonld=jsonld_portfolio(lang, T),
                           pt=json.dumps(pt, ensure_ascii=False).replace("</", "<\\/"))
            out = DOCS / path / "index.html"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(env.get_template(f"{key}.html").render(**ctx), encoding="utf-8")
            written.append(path)
    # 404: GitHub Pages serves it at any missing address, so its links are absolute
    T = NOT_FOUND["en"]
    page = env.get_template("404.html").render(lang="en", key="", path="404.html", root=BASE, site=SITE, v=v, icon=ICON, legal=legal, other=False,
                                               c=COMMON["en"], alternates=[("en", "")], tool=BASE, t=T, de=NOT_FOUND["de"], title=T["title"],
                                               desc=T["p"], jsonld="{}")
    (DOCS / "404.html").write_text(page.replace('<link rel="canonical" href="https://eidenwilson.github.io/macro-risk-monitor/404.html">', ""), encoding="utf-8")
    # sitemap: the tool in each of its languages, and the pages above
    today = dt.date.today().isoformat()
    alt = "".join(f'<xhtml:link rel="alternate" hreflang="{l}" href="{SITE}{"" if l == "en" else "?lang=" + l}"/>' for l in TOOL_LANGS)
    urls = [f'<url><loc>{SITE}</loc><lastmod>{today}</lastmod>{alt}</url>']
    for key in ("advisors", "portfolio"):
        pair = "".join(f'<xhtml:link rel="alternate" hreflang="{l}" href="{SITE}{key}/{"de/" if l == "de" else ""}"/>' for l in LANGS)
        urls += [f'<url><loc>{SITE}{key}/{"de/" if l == "de" else ""}</loc><lastmod>{today}</lastmod>{pair}</url>' for l in LANGS]
    (DOCS / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
                                      'xmlns:xhtml="http://www.w3.org/1999/xhtml">\n' + "\n".join(urls) + "\n</urlset>\n", encoding="utf-8")
    print(f"wrote {', '.join(written)}, 404.html, sitemap.xml (data through {snap['asOf']}; restaurant bad year {round(snap['ex'][0]['loss'])})")


if __name__ == "__main__":
    main()
