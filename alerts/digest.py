"""Monthly alert email for advisors (Business plan).

Runs in the data refresh after build_page.py. It opens the built page in a headless browser and reads every figure
from the page's own model (there is no second copy of the model in Python), compares them with the figures of the
last email (alerts/last.json), and when new data has arrived writes a short German and English digest and sends it
to the subscribers through Buttondown. Client data is never involved: the email links to the page, which re-ranks
the clients saved in each advisor's own browser.

Without BUTTONDOWN_API_KEY the digest is only printed (the workflow puts it in the run summary), so it can be pasted
into Buttondown by hand.

    python alerts/digest.py              # compare, send when the data month is new, then update alerts/last.json
    python alerts/digest.py --dry-run    # print the digest only; nothing is sent or saved
"""
import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request
from decimal import ROUND_HALF_UP, Decimal

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "alerts" / "config.json").read_text(encoding="utf-8"))
LAST = ROOT / "alerts" / "last.json"

# what the page knows now, in its own words for German and English
SNAPSHOT = r"""() => {
  const c = D.current, regimes = ['Recovery', 'Overheating', 'Stagflation', 'Contraction'];
  const words = l => withLang(l, () => ({regimes: Object.fromEntries(regimes.map(r => [r, t(r)])), desc: Object.fromEntries(regimes.map(r => [r, t('d' + r)])),
    presets: t('presetNames'), month: monthLong(ym(c.asOf)), since: monthLong(ym(c.since)), wageMonth: monthLong(ym(D.minWage.effective))}));
  const examples = Object.fromEntries(Object.keys(PRESETS).map(k => { const v = {...PRESETS[k]}, p5 = outcomes(v, POOL).q(.05);
    return [k, {share: v.ebit > 0 ? Math.round(Math.max(0, -p5) / v.ebit * 100) : null, loss: Math.round(Math.max(0, -p5) / 100) * 100}]; }));
  return {asOf: c.asOf, since: c.since, regime: c.regime, euribor: c.euribor, hicp: c.hicp, energy: c.energy, ip: c.ip,
    minWage: D.minWage, examples, pool: POOL, recent: (D.hist.reg || []).filter(Boolean).slice(-3), de: words('de'), en: words('en')};
}"""


def snapshot(page):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(bypass_csp=True)  # the build drives the page with string expressions, which its CSP forbids
        pg.goto(pathlib.Path(page).resolve().as_uri(), wait_until="load")
        snap = pg.evaluate(SNAPSHOT)
        browser.close()
    return snap


def num(x, d, lang):
    x = float(Decimal(str(x)).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP))   # 2.635 -> 2.64, as the page rounds
    s = f"{abs(x):,.{d}f}"
    if lang == "de":
        s = s.replace(",", "_").replace(".", ",").replace("_", ".")
    return ("\u2212" if x < 0 and round(abs(x), d) else "") + s


def pct(x, lang, d=1, sign=False):
    s = num(x, d, lang)
    if sign and x > 0 and round(x, d):
        s = "+" + s
    return s + ("\u00a0%" if lang == "de" else "%")


TEXT = {
    "de": {
        "title": "Makro-Risiko: Daten bis {month}",
        "phase": "**Konjunktur: {regime}**{was} · seit {since}. {desc}",
        "was": " (vorher {x})",
        "numbers": "**Wichtige Zahlen**",
        "euribor": "Euribor 3M", "hicp": "Inflation zum Vorjahr", "energy": "Energiepreise zum Vorjahr", "ip": "Industrieproduktion zum Vorjahr",
        "examples": "**Typische Betriebe: Anteil des Gewinns, den ein schlechtes Jahr (1 von 20) kosten könnte**",
        "wage": "**Schon bekannt:** Der Mindestlohn steigt im {month} von {a} auf {b} pro Stunde.",
        "method": "**Neue Methode:** Seit November 2026 rechnet das schlechte Jahr mit allen früheren Monaten seit 2000 statt nur mit denen der aktuellen Konjunkturphase. Die Zahlen oben sind schon so gerechnet; die Änderung ist kein Konjunktursignal.",
        "cta": "[Mandanten-Ranking öffnen]({url}): Die Seite rechnet Ihre gespeicherten Betriebe mit den neuen Daten neu und zeigt, wer Ihre Warnschwelle überschritten hat. Ihre Mandantendaten bleiben in Ihrem Browser.",
        "subject": "Makro-Risiko {month}", "subjectPhase": "Konjunktur jetzt {regime}", "subjectPhaseNew": "Konjunktur vorläufig {regime}",
        "provisional": " Vorläufig: Das ist der erste Monat in dieser Phase. Erst die nächsten Daten zeigen, ob sie sich hält.",
        "reversal": " Das kehrt den Wechsel aus der letzten E-Mail um. Beide Wechsel sind wahrscheinlich Rauschen, bis eine Phase zwei Monate hält.",
    },
    "en": {
        "title": "Macro risk: data through {month}",
        "phase": "**Economy: {regime}**{was} · since {since}. {desc}",
        "was": " (was {x})",
        "numbers": "**Key numbers**",
        "euribor": "Euribor 3M", "hicp": "Inflation, year on year", "energy": "Energy prices, year on year", "ip": "Industrial output, year on year",
        "examples": "**Typical businesses: share of profit a bad year (1 in 20) could take**",
        "wage": "**Already known:** the minimum wage rises in {month} from {a} to {b} an hour.",
        "method": "**Method change:** since November 2026 the bad year draws on every past month since 2000, not only those in the current phase. The figures above already use it; the change is not a signal about the economy.",
        "cta": "[Open your client ranking]({url}): the page re-ranks the businesses you saved with the new data and shows who crossed your alert level. Your client data stays in your browser.",
        "subject": "macro risk update {month}", "subjectPhase": "economy now {regime}", "subjectPhaseNew": "economy provisionally {regime}",
        "provisional": " Provisional: this is the phase's first month. The next data will show whether it holds.",
        "reversal": " This reverses the change in the last email. Treat both changes as noise until a phase holds for two months.",
    },
}


def months_held(cur):
    """Months the current phase has held, counting the data month itself (1 = it changed with this data)."""
    (y1, m1), (y2, m2) = (map(int, cur[k].split("-")[:2]) for k in ("since", "asOf"))
    return (y2 - y1) * 12 + (m2 - m1) + 1


def phase_note(cur, prev):
    """'provisional', 'reversal' or None. The compass reads the sign of a 6-month change, so it can flip for a single
    month and flip back; advisors should not re-plan on one month's reading (same rule as check_alerts.py)."""
    if not prev or prev["regime"] == cur["regime"] or months_held(cur) != 1:
        return None
    r = cur.get("recent") or []
    return "reversal" if len(r) == 3 and r[0] == cur["regime"] and r[1] == prev["regime"] else "provisional"


def money(x, lang):
    return f"{num(x, 2, lang)}\u00a0€" if lang == "de" else f"€{num(x, 2, lang)}"


def section(lang, cur, prev):
    T, w = TEXT[lang], cur[lang]
    site = CONFIG["site"].rstrip("/") + "/"
    was = lambda k, f: (T["was"].format(x=f(prev[k])) if prev and prev.get(k) is not None and prev[k] != cur[k] else "")
    note = phase_note(cur, prev)
    lines = [f"# {T['title'].format(month=w['month'])}", "",
             T["phase"].format(regime=w["regimes"][cur["regime"]], was=(T["was"].format(x=w["regimes"][prev["regime"]]) if prev and prev["regime"] != cur["regime"] else ""),
                               since=w["since"], desc=w["desc"][cur["regime"]]) + (T[note] if note else ""), "", T["numbers"]]
    for k, d, sign in (("euribor", 2, False), ("hicp", 1, True), ("energy", 1, True), ("ip", 1, True)):
        if cur.get(k) is not None:
            f = lambda x, d=d, sign=sign: pct(x, lang, d, sign)
            lines.append(f"- {T[k]}: {f(cur[k])}{was(k, f)}")
    lines += ["", T["examples"]]
    for key, name in w["presets"].items():
        e, p = cur["examples"].get(key), (prev or {}).get("examples", {}).get(key)
        if not e or e["share"] is None:
            continue
        before = T["was"].format(x=pct(p["share"], lang, 0)) if p and p.get("share") is not None and p["share"] != e["share"] else ""
        lines.append(f"- {name}: {pct(e['share'], lang, 0)}{before}")
    if prev and cur.get("pool") == "all" and prev.get("pool") != "all":   # once, in the first email after the switch
        lines += ["", T["method"]]
    mw = cur["minWage"]
    if mw["effective"] > cur["asOf"]:
        lines += ["", T["wage"].format(month=w["wageMonth"], a=money(mw["from"], lang), b=money(mw["to"], lang))]
    lines += ["", T["cta"].format(url=f"{site}?plan=business&lang={lang}#clients")]
    return "\n".join(lines)


def digest(cur, prev):
    phase = prev is not None and prev["regime"] != cur["regime"]
    key = ("subjectPhaseNew" if phase_note(cur, prev) else "subjectPhase") if phase else "subject"
    de, en = (TEXT[l][key].format(month=cur[l]["month"], regime=cur[l]["regimes"][cur["regime"]]) for l in ("de", "en"))
    subject = f"{de} | {en[0].upper() + en[1:]}"
    body = "\n\n".join([
        "**Deutsch** · English below",
        section("de", cur, prev), "---", "**English**", section("en", cur, prev), "---",
        "Prototyp zur Diskussion, keine Finanzberatung. Prototype for discussion, not financial advice.",
    ])
    return subject, body


def send(subject, body, key):
    req = urllib.request.Request("https://api.buttondown.com/v1/emails", method="POST",
                                 data=json.dumps({"subject": subject, "body": body, "status": "about_to_send"}).encode(),
                                 headers={"Authorization": f"Token {key}", "Content-Type": "application/json",
                                          "X-API-Version": "2026-04-01", "X-Buttondown-Live-Dangerously": "true"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", default=str(ROOT / "docs" / "index.html"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cur = snapshot(a.page)
    prev = json.loads(LAST.read_text(encoding="utf-8")) if LAST.exists() else None
    if prev and prev["asOf"] == cur["asOf"] and prev["regime"] == cur["regime"] and not a.dry_run:
        print(f"No new data since the last email ({cur['asOf']}): nothing to send.")
        return
    subject, body = digest(cur, prev)
    print(f"Subject: {subject}\n\n{body}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"## Alert email\n\n**{subject}**\n\n{body}\n")
    if a.dry_run:
        return
    key = os.environ.get("BUTTONDOWN_API_KEY", "").strip()
    if key:
        try:
            print("Buttondown:", send(subject, body, key))
        except urllib.error.HTTPError as e:
            print(f"::warning::The alert email was not sent ({e.code}): {e.read().decode(errors='replace')[:400]}")
            return                                                   # keep last.json, so the next refresh tries again
        except OSError as e:
            print(f"::warning::The alert email was not sent: {e}")
            return
    else:
        print("BUTTONDOWN_API_KEY is not set: the digest above was not sent.")
    LAST.write_text(json.dumps(cur, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
