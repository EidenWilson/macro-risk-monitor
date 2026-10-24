"""Render docs/og.png, the 1200x630 picture shown when the site's link is shared (LinkedIn, WhatsApp, Slack).

    pip install playwright && python -m playwright install chromium
    python assets/og_image.py
"""
import base64
import pathlib
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
font = base64.b64encode((ROOT / "assets/archivo-latin-wdth-normal.woff2").read_bytes()).decode("ascii")
html = f"""<!doctype html><meta charset="utf-8"><style>
@font-face{{font-family:Archivo;font-weight:100 900;font-stretch:62% 125%;src:url(data:font/woff2;base64,{font}) format("woff2")}}
*{{box-sizing:border-box}} body{{margin:0;width:1200px;height:630px;font-family:Archivo,sans-serif;color:#1c2541;
  background:linear-gradient(135deg,#e9efff 0%,#eafaf4 55%,#fff6e3 100%);display:flex;flex-direction:column;justify-content:space-between;padding:64px 72px}}
.brand{{font-weight:700;font-stretch:115%;font-size:30px;letter-spacing:-.01em}}
.eyebrow{{font-size:22px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#5d6a87;margin:0 0 18px}}
h1{{margin:0;font-size:66px;line-height:1.08;font-weight:500;font-stretch:88%;letter-spacing:-.015em;max-width:980px}}
h1 b{{font-weight:800;font-stretch:112%;color:#c42a20}}
.bar{{position:relative;height:44px;border-radius:99px;background:#ddf7ee;overflow:hidden;border:1px solid #e2e8f5;margin-top:34px;width:980px}}
.keep{{position:absolute;inset:0 auto 0 0;width:38%;background:#10a37f}}
.lose{{position:absolute;top:0;bottom:0;left:38%;width:62%;background:repeating-linear-gradient(135deg,#f0483e 0 10px,rgba(240,72,62,.75) 10px 20px)}}
.foot{{display:flex;justify-content:space-between;align-items:flex-end;font-size:22px;color:#5d6a87}}
.foot span{{white-space:nowrap}} .url{{font-weight:700;color:#3358f0}}
</style>
<div class="brand">Macro Risk Monitor</div>
<div><p class="eyebrow">Macro risk check for German small businesses</p>
<h1>In a bad year, the economy could take <b>€44,300</b> out of a €72,000 profit.</h1>
<div class="bar"><div class="keep"></div><div class="lose"></div></div></div>
<div class="foot"><span>Live ECB, Eurostat and FRED data</span><span class="url">eidenwilson.github.io/macro-risk-monitor</span></div>"""

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1200, "height": 630})
    pg.set_content(html)
    pg.wait_for_timeout(300)
    pg.screenshot(path=str(ROOT / "docs/og.png"))
    b.close()
print("wrote docs/og.png")
