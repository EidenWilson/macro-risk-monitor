"""Build the static site: inject data.json, the embedded font and the assistant spec into template.html.

Default (GitHub Pages): writes docs/index.html with a full document head. The PDF library is loaded from
docs/vendor/ when that file exists, so visitors' browsers only ever talk to this site, and the footer links to
the legal notice and privacy page once docs/impressum.html and docs/datenschutz.html exist.
--bare: writes only the page itself, for hosts that add their own document skeleton (PDF library from cdnjs).

The Business assistant's instructions and tool definitions live in assistant/ and are shared with the assistant
server (worker/), so the page and the server always agree. When assistant/config.json names the server's URL, the
website build talks to it; a --bare build (Claude artifact) uses Claude through the viewer's account instead.
"""
import argparse
import base64
import json
import pathlib
import re

ap = argparse.ArgumentParser()
ap.add_argument("--data", default="docs/data.json")
ap.add_argument("--template", default="template.html")
ap.add_argument("--font", default="assets/archivo-latin-wdth-normal.woff2")
ap.add_argument("--out", default="docs/index.html")
ap.add_argument("--assistant", default="assistant", help="folder with prompt.txt, tools.json and config.json")
ap.add_argument("--bare", action="store_true", help="page only, without the <html>/<head> wrapper")
ap.add_argument("--site-url", default="https://eidenwilson.github.io/macro-risk-monitor/", help="public address, for link previews")
a = ap.parse_args()

out = pathlib.Path(a.out)
page = pathlib.Path(a.template).read_text(encoding="utf-8")
for key in ("__DATA__", "__FONT_WOFF2__", "__ASSISTANT_PROMPT__", "__ASSISTANT_TOOLS__"):
    if page.count(key) != 1:
        raise SystemExit(f"{a.template} must contain {key} exactly once")
spec = pathlib.Path(a.assistant)
safe = lambda js: js.replace("</", "<\\/")                             # can never close the script tag
prompt = (spec / "prompt.txt").read_text(encoding="utf-8").strip()
tools = json.loads((spec / "tools.json").read_text(encoding="utf-8"))
cfg = json.loads((spec / "config.json").read_text(encoding="utf-8")) if (spec / "config.json").exists() else {}
page = page.replace("__ASSISTANT_PROMPT__", safe(json.dumps(prompt))).replace("__ASSISTANT_TOOLS__", safe(json.dumps(tools)))
data = safe(pathlib.Path(a.data).read_text(encoding="utf-8"))
font = base64.b64encode(pathlib.Path(a.font).read_bytes()).decode("ascii")
page = page.replace("__FONT_WOFF2__", font).replace("__DATA__", data)

if a.bare:
    html = page
else:
    flags = {}
    if (out.parent / "vendor" / "jspdf.umd.min.js").exists():
        flags["MRM_JSPDF_SRC"] = "vendor/jspdf.umd.min.js"
    legal = [out.parent / f for f in ("impressum.html", "datenschutz.html")]
    if all(f.exists() and "[[" not in f.read_text(encoding="utf-8") for f in legal):   # never link unfinished drafts
        flags["MRM_LEGAL"] = True
    if str(cfg.get("url", "")).startswith("https://"):                  # the Business assistant's server (worker/)
        flags["MRM_ASSISTANT_URL"] = cfg["url"].rstrip("/")
        if cfg.get("model"):                                             # one of the server's MODELS (worker/wrangler.toml)
            flags["MRM_ASSISTANT_MODEL"] = str(cfg["model"])
    # the template starts with its <title> and <meta> tags; they belong in the document head
    m = re.match(r"\s*(?:(?:<title>.*?</title>|<meta\b[^>]*>)\s*)+", page, re.S)
    head_tags, body = (m.group(0).strip(), page[m.end():]) if m else ("", page)
    # link previews (LinkedIn, WhatsApp, Slack) and a small icon for the browser tab
    desc = "How much of next year's profit could the economy take? A free macro risk check for German small businesses, built on live ECB, Eurostat and FRED data."
    site = a.site_url.rstrip("/") + "/"
    share = (f'<meta property="og:type" content="website"><meta property="og:title" content="Macro Risk Monitor">'
             f'<meta property="og:description" content="{desc}"><meta property="og:url" content="{site}">'
             f'<meta name="twitter:card" content="summary_large_image"><meta name="theme-color" content="#ffffff">')
    if (out.parent / "og.png").exists():                                 # made by assets/og_image.py
        share += (f'<meta property="og:image" content="{site}og.png"><meta property="og:image:width" content="1200">'
                  f'<meta property="og:image:height" content="630"><meta property="og:image:alt" content="Macro Risk Monitor: in a bad year, the economy could take €44,300 out of a €72,000 profit.">')
    icon = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='8' fill='%233358f0'/>"
            "<rect x='5' y='12' width='22' height='8' rx='4' fill='%23ffffff'/><rect x='5' y='12' width='9' height='8' rx='4' fill='%2310a37f'/>"
            "<rect x='14' y='12' width='13' height='8' rx='4' fill='%23f0483e'/></svg>")
    share += f'<link rel="icon" href="data:image/svg+xml,{icon}">'
    html = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">"
            + head_tags +
            f"<meta name=\"description\" content=\"{desc}\">" + share +
            "<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}"
            "body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>"
            + (f"<script>{''.join(f'window.{k}={json.dumps(v)};' for k, v in flags.items())}</script>" if flags else "")
            + "</head><body>" + body + "</body></html>")

out.write_text(html, encoding="utf-8")
print(f"wrote {out} ({len(html) // 1024} KB){' flags: ' + ', '.join(flags) if not a.bare and flags else ''}")
