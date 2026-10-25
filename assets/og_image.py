"""Render docs/og.png, the 1200x630 picture shown when the site's link is shared (LinkedIn, WhatsApp, Slack),
and assets/screenshot.png for the README, both in the page's own design.

    pip install playwright && python -m playwright install chromium
    python build_page.py && python assets/og_image.py
"""
import base64
import pathlib
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
font = base64.b64encode((ROOT / "assets/fonts/sofia-sans-semi-condensed-latin.woff2").read_bytes()).decode("ascii")
html = f"""<!doctype html><meta charset="utf-8"><style>
@font-face{{font-family:"Sofia Sans SC";font-weight:1 1000;src:url(data:font/woff2;base64,{font}) format("woff2")}}
*{{box-sizing:border-box}}
body{{margin:0;width:1200px;height:630px;font-family:"Sofia Sans SC",sans-serif;color:#1f2a3a;background:#fff;padding:52px 64px 48px;
  display:grid;grid-template-rows:auto 1fr auto;font-variant-numeric:tabular-nums}}
.brand{{display:flex;align-items:center;gap:14px;font-weight:700;font-size:30px;padding-bottom:22px;border-bottom:2px solid #1f2a3a}}
.brand svg{{width:38px;height:38px}}
.main{{display:grid;grid-template-columns:1fr 390px;gap:56px;align-items:center}}
h1{{margin:0;font-size:60px;line-height:1.04;font-weight:650;max-width:15ch}}
.bar{{position:relative;height:20px;margin-top:30px;box-shadow:inset 0 0 0 1.5px #1f2a3a;width:560px}}
.keep{{position:absolute;inset:0 auto 0 0;width:38%;background:#1f2a3a}}
.lose{{position:absolute;top:0;bottom:0;left:38%;right:0;background:repeating-linear-gradient(135deg,#b3261e 0 3px,transparent 3px 8px);box-shadow:inset 0 0 0 1.5px #b3261e}}
table{{border-collapse:collapse;width:100%;font-size:25px}}
th,td{{padding:9px 0;white-space:nowrap}}
thead th{{font-size:19px;font-weight:600;color:#566170;text-align:right;padding-bottom:6px;border-bottom:2.5px solid #1f2a3a}}
tbody th{{text-align:left;font-weight:400}}
td{{text-align:right;font-weight:600;padding-left:24px}}
.neg td{{color:#a4231c}}
.sum th,.sum td{{font-weight:700}}
.sum td{{border-top:1.5px solid #1f2a3a;border-bottom:4.5px double #1f2a3a}}
.foot{{display:flex;justify-content:space-between;align-items:flex-end;font-size:22px;color:#566170}}
.foot span{{white-space:nowrap}} .url{{font-weight:700;color:#1f2a3a}}
</style>
<div class="brand"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10.4" fill="none" stroke="#1f2a3a" stroke-width="1.8"/><path d="M12 1.6v20.8M1.6 12h20.8" stroke="#1f2a3a" stroke-width="1.15"/><circle cx="16.8" cy="7.2" r="3.1" fill="#b3261e"/></svg>Macro Risk Monitor</div>
<div class="main"><div><h1>In a bad year, the economy could take €44,300 out of a €72,000 profit.</h1>
<div class="bar"><div class="keep"></div><div class="lose"></div></div></div>
<table><thead><tr><td></td><th>€</th><th>%</th></tr></thead><tbody>
<tr><th>Annual profit</th><td>72,000</td><td>100</td></tr>
<tr class="neg"><th>Bad year (1 in 20)</th><td>−44,300</td><td>−62</td></tr>
<tr class="sum"><th>Left in a bad year</th><td>27,700</td><td>38</td></tr></tbody></table></div>
<div class="foot"><span>Risk check for small businesses in Germany</span><span class="url">eidenwilson.github.io/macro-risk-monitor</span></div>"""

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1200, "height": 630})
    pg.set_content(html)
    pg.wait_for_timeout(300)
    pg.screenshot(path=str(ROOT / "docs/og.png"))
    pg.close()
    pg = b.new_page(viewport={"width": 1440, "height": 860})                # the README picture: the built page itself
    pg.goto((ROOT / "docs/index.html").as_uri() + "?lang=en&plan=pro", wait_until="load")
    pg.wait_for_timeout(500)
    pg.screenshot(path=str(ROOT / "assets/screenshot.png"))
    b.close()
print("wrote docs/og.png and assets/screenshot.png")
