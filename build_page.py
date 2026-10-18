"""Inject data.json into template.html and write the static site to docs/index.html (GitHub Pages)."""
import argparse
import pathlib

ap = argparse.ArgumentParser()
ap.add_argument("--data", default="docs/data.json")
ap.add_argument("--template", default="template.html")
ap.add_argument("--out", default="docs/index.html")
a = ap.parse_args()

page = pathlib.Path(a.template).read_text(encoding="utf-8")
data = pathlib.Path(a.data).read_text(encoding="utf-8")
html = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">"
        "<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}"
        "body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style></head><body>"
        + page.replace("__DATA__", data) + "</body></html>")
pathlib.Path(a.out).write_text(html, encoding="utf-8")
print(f"wrote {a.out} ({len(html) // 1024} KB)")
