"""Browser checks for the built page: plans, layout and the Business assistant (with its server mocked).

    pip install playwright && python -m playwright install chromium
    python build_page.py && python tests/test_page.py

No network access is needed: the assistant server and Claude are replaced by fakes, so these checks cover the
page's side of the conversation (tool loop, privacy, apply and undo, errors), not the quality of a model's answers.
"""
import functools, http.server, json, pathlib, sys, threading
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent / "docs"
SERVER = "https://mrm-assistant.test.workers.dev"
CLIENTS = [  # names that must never leave the page
    {"id": "c1", "name": "Bäckerei Müller", "preset": "bakery"},
    {"id": "c2", "name": "Druckerei Weiß", "preset": "print"},
    {"id": "c3", "name": "Gasthaus zur Post", "preset": "restaurant"},
]
failures = []


def check(cond, msg):
    print(("  ok  " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(ROOT)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}/"


def page_with(browser, url, width=1280, init=None):
    pg = browser.new_page(viewport={"width": width, "height": 900})
    pg.errors = []
    pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    if init:
        pg.add_init_script(init)
    pg.goto(url, wait_until="networkidle")
    return pg


def visible(pg, sel):
    return pg.evaluate(f"(() => {{ const el = document.querySelector({json.dumps(sel)}); return !!el && el.getClientRects().length > 0; }})()")


SEED = """(() => {
  const P = {bakery:{rev:620000, ebit:46000, beta:0.6, demandIdx:'retail', loan:180000, energy:64000, food:190000, cash:22000},
             print:{rev:1500000, ebit:95000, beta:0.6, demandIdx:'ip', loan:400000, energy:70000, food:420000, inputIdx:'ppi_paper', cash:70000},
             restaurant:{rev:900000, ebit:72000}};
  const clients = %s.map(c => ({id:c.id, name:c.name, values:P[c.preset], updated:'2026-10-01'}));
  try { localStorage.setItem('mrm3-clients', JSON.stringify(clients)); } catch (e) {}
})();""" % json.dumps(CLIENTS)


def test_plans(browser, base):
    print("plans and layout")
    for plan, shown, hidden in [
        ("free", ["#tiles", "#scen", ".upsell[data-only=free]"], ["#hedgeTable", "#cashChart", "#pdfBtn", "#askBtn", ".clients", ".rel"]),
        ("pro", ["#hedgeTable", "#cashChart", "#pdfBtn", ".rel", ".upsell.biz"], ["#askBtn", ".clients", ".upsell[data-only=free]"]),
        ("business", ["#hedgeTable", "#askBtn", ".clients", "#alert"], [".upsell.biz", ".upsell[data-only=free]"]),
    ]:
        for width in (1280, 390):
            pg = page_with(browser, f"{base}?plan={plan}", width)
            check(not pg.errors, f"{plan} at {width}px: no console errors {pg.errors[:2]}")
            check(pg.evaluate("document.documentElement.scrollWidth") <= width, f"{plan} at {width}px: no sideways scroll")
            if width == 1280:
                for sel in shown:
                    check(visible(pg, sel), f"{plan}: {sel} shown")
                for sel in hidden:
                    check(not visible(pg, sel), f"{plan}: {sel} hidden")
            pg.close()
    pg = page_with(browser, base)
    tabs = pg.evaluate("[...document.querySelectorAll('#scen button')].map(b => b.dataset.s || 'more')")
    check(tabs == ["2022 energy shock", "more"], f"free shows one scenario and a Pro link: {tabs}")
    pg.click("#planBtn")
    price = "document.querySelector('[aria-labelledby=pl-business] .price').firstChild.textContent"
    check(pg.evaluate(price) == "€89.99", f"business price keeps its cents: {pg.evaluate(price)}")
    pg.click("[data-setplan=pro]")
    check(pg.evaluate("document.documentElement.dataset.plan") == "pro" and pg.evaluate("document.getElementById('planModal').hidden"), "switching plan in the dialog")
    check(pg.evaluate("document.querySelectorAll('#scen button[data-s]').length") == 5, "pro shows all five scenarios")
    pg.close()
    pg = page_with(browser, f"{base}?lang=de"); pg.click("#planBtn")
    de = pg.evaluate(price).replace(chr(160), " ")
    check(de == "89,99 €", f"business price in German format: {de}")
    pg.close()


def test_tools(browser, base):
    print("assistant tools against the page's own numbers")
    pg = page_with(browser, f"{base}?plan=business")
    r = pg.evaluate("""() => {
      const act = () => {};
      const same = TOOLS.what_if({energy_bill: V.energy}, act);
      const loan = TOOLS.what_if({fix_loan_rate: true}, act);
      const row = LAST.hedges.find(x => x.h.k === 'loan');
      const sh = TOOLS.shock({energy_pct: 30}, act), none = TOOLS.shock({energy_pct: 0}, act);
      let err = ''; try { TOOLS.shock({}, act); } catch (e) { err = e.message; }
      const ctx = assistantContext();
      return {before: same.before, after: same.after, hero: Math.round(LAST.hero.p5 / 100) * 100,
              loanAfter: loan.after.bad_year_profit_change, loanExpected: Math.round((LAST.hero.p5 + row.save - row.cost) / 100) * 100,
              loanEffect: loan.effect.on_profit_in_bad_year, loanGap: loan.after.profit_in_bad_year - loan.before.profit_in_bad_year,
              energyEffect: sh.effect_by_line.energy, total: sh.effect_of_shock, noneTotal: none.effect_of_shock,
              shockGap: sh.next_year_profit_with_shock - sh.next_year_profit_without_shock, notes: sh.why_the_effect_is_smaller_than_the_move, err,
              ctxKeys: Object.keys(ctx), ctxSize: JSON.stringify(ctx).length, drivers: ctx.risk.where_a_bad_year_hits_pct,
              repaired: repairCalls(parseRoute('Sure. {"calls":[{"tool":"what_if","arguments":{"sales_index_pct":-5,"energy_pct":20,"fix_loan_rate":true}},{"tool":"compare_clients","arguments":{}}]}')),
              garbage: parseRoute('no json here'),
              unchecked: uncheckedFigures('A 30% rise adds €14,400 to the €48,000 bill, so profit next year is €' + sh.next_year_profit_with_shock.toLocaleString('en-GB') + '. You could lose €7,777,700, or 7.777.700 € in German, or €7.8m.', [ctx, sh], 'What if energy prices rise 30%?')};
    }""")
    check(r["before"] == r["after"], "what_if with no real change leaves every number as it was")
    check(r["before"]["bad_year_profit_change"] == r["hero"], f"what_if starts from the headline figure ({r['hero']})")
    check(r["loanEffect"] == r["loanGap"] and r["loanEffect"] > 0, f"what_if states its effect explicitly ({r['loanEffect']})")
    check(r["shockGap"] == r["total"], "shock: the two next-year profits differ by exactly the stated effect")
    check(any("fixed price" in n for n in r["notes"]), f"shock explains why the effect is smaller than the move: {r['notes']}")
    check(r["repaired"] == [{"name": "shock", "args": {"sales_index_pct": -5, "energy_pct": 20}}, {"name": "what_if", "args": {"fix_loan_rate": True}}, {"name": "compare_clients", "args": {}}],
          f"router mistakes are repaired: market moves go to shock, own figures to what_if ({r['repaired']})")
    check(r["garbage"] == [], "a router reply without JSON means no tools")
    check(r["unchecked"] == ["€7,777,700", "7.777.700 €", "€7.8m"], f"invented figures are flagged in both number styles, derived and known ones are not ({r['unchecked']})")
    check(abs(r["loanAfter"] - r["loanExpected"]) <= 100, f"what_if fix_loan_rate matches the protection table ({r['loanAfter']} vs {r['loanExpected']})")
    check(r["energyEffect"] < 0 and r["total"] < 0 and r["noneTotal"] == 0, f"shock: energy +30% costs money ({r['energyEffect']}), a zero shock costs nothing")
    check("No shock given" in r["err"], "shock without parameters explains what to pass")
    check(r["ctxSize"] < 12000, f"page summary is small enough to send ({r['ctxSize']} chars)")
    check(abs(sum(r["drivers"].values()) - 100) <= 2, f"bad-year drivers add up to 100% ({r['drivers']})")
    pg.close()


def test_server_transport(browser, base):
    print("assistant over the server (mocked)")
    sent, check_final = [], []

    cors = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "content-type, x-access-code", "Access-Control-Allow-Methods": "POST, OPTIONS"}

    def fake(route):
        if route.request.method == "OPTIONS":
            return route.fulfill(status=204, headers=cors)
        reply = route.fulfill
        route.fulfill = lambda **kw: reply(headers=cors, **kw)
        body = json.loads(route.request.post_data or "{}")
        sent.append(route.request.post_data)
        msgs = body["messages"]; last = msgs[-1]
        if route.request.headers.get("x-access-code") != "letmein" and "quota" not in json.dumps(msgs):
            return route.fulfill(status=401, content_type="application/json", body='{"error":"access_code"}')
        check_final.append(body.get("final") is True)
        if last["role"] == "user" and last["content"].startswith("ROUTING STEP"):
            q = last["content"].split("Question: ", 1)[1]
            if "quota" in q:
                return route.fulfill(status=429, content_type="application/json", body='{"error":"quota"}')
            calls = [{"tool": "what_if", "arguments": {"energy_pct": 30}}] if "energy" in q else \
                    [{"tool": "what_if", "arguments": {"fix_loan_rate": True}}] if "loan" in q else \
                    [] if "invent" in q else [{"tool": "compare_clients", "arguments": {}}]
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({"text": "Sure. " + json.dumps({"calls": calls}), "calls": []}))
        if last["role"] == "user":
            text = "In a bad year you would lose €7,777,700."
        else:
            res = last["result"]
            if last["name"] == "compare_clients":
                text = f"**{res['clients'][0]['code']}** is most at risk, then {res['clients'][1]['code']}."
            elif last["name"] == "shock":
                text = f"Profit next year would be €{res['next_year_profit_with_shock']:,} instead of €{res['next_year_profit_without_shock']:,}.\n- Energy: €{-res['effect_by_line'].get('energy'):,} more"
            else:
                text = f"Fixing the loan changes the profit in a bad year to €{res['after']['profit_in_bad_year']:,}."
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"text": text, "calls": []}))

    init = SEED + f"window.MRM_ASSISTANT_URL = {json.dumps(SERVER)};"
    pg = page_with(browser, f"{base}?plan=business", init=init)
    pg.route("**/chat", fake)   # the real server URL from assistant/config.json is baked into the build; answer it here
    pg.click("#askBtn")
    check("assistant server" in pg.inner_text("#chatLog"), "welcome text names where questions go")
    pg.fill("#chatInput", "What if energy prices rise 30%?"); pg.keyboard.press("Enter")
    pg.wait_for_selector(".codeform", timeout=5000)
    check(True, "a 401 from the server asks for the access code")
    pg.fill(".codeform input", "letmein"); pg.click(".codeform button")
    pg.wait_for_function("document.querySelectorAll('.msg.a .body p, .msg.a .body li').length > 0 && !chat.busy", timeout=5000)
    log = pg.inner_text("#chatLog")
    check("Applied a shock" in log and "Energy +30%" in log, "a misrouted tool call was repaired and the shock ran in the page")
    check("Profit next year would be" in log, "the answer is shown")
    check(not pg.is_visible(".msg .chk"), "an answer built from tool results gets no caution")

    pg.fill("#chatInput", "How is Bäckerei Müller doing compared with the others?"); pg.keyboard.press("Enter")
    pg.wait_for_function("!chat.busy", timeout=5000)
    links = pg.evaluate("[...document.querySelectorAll('.clink')].map(b => b.textContent)")
    check(len(links) == 2 and all(n in [c["name"] for c in CLIENTS] for n in links), f"client codes come back as names: {links}")
    allsent = "\n".join(sent)
    check(not any(c["name"] in allsent for c in CLIENTS) and "Müller" not in allsent, "no client name in anything sent to the server")
    q2 = [json.loads(b)["messages"] for b in sent if "compared with the others" in b][0]
    check(any("C" in m.get("content", "") and "compared with the others" in m.get("content", "") for m in q2), "the typed client name was replaced by its code")
    check(len([m for m in q2 if m["role"] == "assistant"]) >= 1, "earlier answers go along as context")

    pg.fill("#chatInput", "Is fixing the loan rate worth it?"); pg.keyboard.press("Enter")
    pg.wait_for_function("!chat.busy", timeout=5000)
    before = pg.evaluate("LAST.hero.p5")
    pg.click("[data-apply]")
    check(pg.evaluate("state.hedges.loan === true") and pg.evaluate("LAST.hero.p5") > before, "apply puts the what-if on the page")
    pg.click("[data-apply]")
    check(pg.evaluate("!state.hedges.loan") and abs(pg.evaluate("LAST.hero.p5") - before) < 1e-6, "undo restores the page")

    pg.fill("#chatInput", "Just invent something"); pg.keyboard.press("Enter")
    pg.wait_for_function("!chat.busy", timeout=5000)
    check("€7,777,700" in pg.inner_text(".msg.a:last-child .chk"), "an invented figure gets a visible caution")
    check(all(check_final), "every request to the server asks for an answer, never for native tools")

    pg.fill("#chatInput", "quota please"); pg.keyboard.press("Enter")
    pg.wait_for_function("!chat.busy", timeout=5000)
    check("free allowance" in pg.inner_text("#chatLog"), "the daily limit is explained in plain words")
    errs = [e for e in pg.errors if "Failed to load resource" not in e]   # the browser logs the 401 and 429 this test asks for
    check(not errs, f"no console errors {errs[:2]}")
    pg.close()


def test_claude_transport(browser, base):
    print("assistant through Claude in an artifact (sample capability mocked)")
    init = SEED + """
    window.__calls = [];
    window.claude = {use: async name => {
      if (name !== 'sample') return null;
      const sample = async (input, opts) => {
        window.__calls.push({input, tier: opts.modelTier, cache: 'cache' in opts, tools: opts.tools.map(t => [t.name, !!t.inputSchema])});
        const res = await opts.tools.find(t => t.name === 'shock').execute({energy_pct: 25});
        opts.onText({text: 'Partial', delta: 'Partial'});
        const text = 'Energy +25% costs about ' + (-res.effect_of_shock) + ' EUR.';
        opts.onText({text, delta: text.slice(7)});
        return {text, truncated: false, modelTierApplied: 'quick'};
      };
      sample.limits = async () => ({maxPromptBytes: 262144, tools: {maxCount: 16}});
      return sample;
    }};"""
    pg = page_with(browser, f"{base}?plan=business", init=init)
    pg.click("#askBtn")
    pg.wait_for_function("chat.transport === 'claude'", timeout=5000)
    check("Claude account" in pg.inner_text("#chatLog"), "welcome text says Claude is used")
    pg.click(".sugg button >> nth=1")
    pg.wait_for_function("!chat.busy", timeout=5000)
    c = pg.evaluate("window.__calls[0]")
    first = c["input"][0]["content"]
    check(c["tier"] == "quick" and not c["cache"], "quick tier, no caching with tools")
    check(sorted(t[0] for t in c["tools"]) == ["compare_clients", "shock", "what_if"] and all(t[1] for t in c["tools"]), "all three tools offered with schemas")
    check(first.startswith("You are the risk assistant") and "DATA (the page right now" in first, "instructions and page data lead the conversation")
    check(c["input"][-1]["role"] == "user" and "energy" in c["input"][-1]["content"].lower(), "the question is the last turn")
    log = pg.inner_text("#chatLog")
    check("Energy +25%" in log and "costs about" in log, "streamed answer and tool activity shown")
    check(not any(cl["name"] in json.dumps(c["input"]) for cl in CLIENTS), "no client name sent to Claude")
    check(not pg.errors, f"no console errors {pg.errors[:2]}")
    pg.close()


def test_languages(browser, base):
    print("languages")
    pg = page_with(browser, f"{base}?plan=business")
    r = pg.evaluate("""() => {
      const kind = v => Array.isArray(v) ? 'list of ' + v.length : v && typeof v === 'object' ? 'object' : typeof v;
      const diff = (a, b, path, out) => {                         // keys a translation lacks, adds or shapes differently
        for (const k of Object.keys(a)) {
          if (k === '_') continue;
          if (!(k in b)) out.push(path + k + ' missing');
          else if (kind(a[k]) !== kind(b[k])) out.push(`${path}${k}: ${kind(b[k])}, English has ${kind(a[k])}`);
          else if (kind(a[k]) === 'object') diff(a[k], b[k], path + k + '.', out);
        }
        for (const k of Object.keys(b)) if (!(k in a)) out.push(path + k + ' is not in English');
        return out;
      };
      const calls = (o, path, out) => { for (const [k, v] of Object.entries(o)) {   // every text function, with sample figures
        if (typeof v === 'function') for (const n of [0, 1, 2, 5, 12, 49]) {
          let x; try { x = String(v(n, '€44,300', '€72,000', '62%', '40%', '1.5%')); } catch (e) { out.push(path + k + ' throws ' + e.message); break; }
          if (/undefined|NaN|\\[object|null/.test(x)) { out.push(path + k + ': ' + x.slice(0, 80)); break; }
        } else if (v && typeof v === 'object' && !Array.isArray(v)) calls(v, path + k + '.', out); } return out; };
      const lostInPdf = l => {                                    // report text the PDF fonts could not write
        const {ops} = withLang(T[l]._.pdf ? l : 'en', () => reportContent({...V}));
        return ops.flatMap(o => o.slice(1)).filter(x => typeof x === 'string' && pdfText(x).includes('?') && !x.includes('?')).slice(0, 2);
      };
      return {langs: LANGS, packs: Object.fromEntries(LANGS.map(l => [l, {diff: diff(T.en, T[l], '', []), bad: withLang(l, () => calls(T[l], '', [])), pdf: lostInPdf(l)}]))};
    }""")
    check(len(r["langs"]) == 13, f"13 languages in the menu: {r['langs']}")
    for l, x in r["packs"].items():
        check(not x["diff"], f"{l}: same keys as English {x['diff'][:4]}")
        check(not x["bad"], f"{l}: every text function reads well with sample figures {x['bad'][:3]}")
        check(not x["pdf"], f"{l}: the PDF report has no letters its fonts lack {x['pdf']}")
    pg.close()
    for lang in r["langs"]:
        for width in (1280, 390):
            pg = page_with(browser, f"{base}?plan=business&lang={lang}", width)
            pg.click("#relBtn")
            if width == 1280:
                pg.click("#askBtn")
            v = pg.evaluate("""() => ({lang: document.documentElement.lang, dir: document.documentElement.dir, code: document.getElementById('langCode').textContent,
              sel: document.getElementById('langSel').value, chartDir: getComputedStyle(document.getElementById('cashChart')).direction,
              wide: document.documentElement.scrollWidth, text: document.body.innerText})""")
            bad = [w for w in ("undefined", "NaN", "[object") if w in v["text"]]
            check(not pg.errors and not bad, f"{lang} at {width}px: no errors or broken text {pg.errors[:1]} {bad}")
            check(v["wide"] <= width, f"{lang} at {width}px: no sideways scroll ({v['wide']}px)")
            if width == 1280:
                check(v["sel"] == lang and v["code"] == lang.upper(), f"{lang}: the menu shows the language")
                check(v["dir"] == ("rtl" if lang == "ar" else "ltr") and v["chartDir"] == "ltr", f"{lang}: page direction {v['dir']}, charts left to right")
            pg.close()
    pg = page_with(browser, f"{base}?lang=fr")
    pg.select_option("#langSel", "ar")
    v = pg.evaluate("({dir: document.documentElement.dir, url: location.search, saved: JSON.parse(localStorage.getItem('mrm3') || '{}').lang})")
    check(v["dir"] == "rtl" and "lang=ar" in v["url"] and v["saved"] == "ar", f"choosing a language switches the page, the link and the saved choice {v}")
    pg.close()
    pg = page_with(browser, base, init="Object.defineProperty(navigator, 'languages', {get: () => ['pt-BR', 'en']});")
    check(pg.evaluate("LANG") == "pt" and pg.evaluate("document.documentElement.translate") is False, "a first visit follows the browser's language, and the browser does not translate it again")
    pg.close()
    pg = page_with(browser, base, init="Object.defineProperty(navigator, 'languages', {get: () => ['ja-JP']});")
    check(pg.evaluate("LANG") == "en" and pg.evaluate("document.documentElement.translate") is True, "a language the page lacks gets English, which the browser may translate")
    pg.close()


def test_formats(browser, base):
    print("numbers and money in each language")
    pg = page_with(browser, base)
    r = pg.evaluate("""() => {
      const fmt = l => withLang(l, () => ({money: eur(-44312), signed: seur(14380), pct: pct(3.3), share: pc(62), axis: short(-20000),
        local: localMoney('Der Gewinn sinkt um €14,400 auf €58,600, nicht €1.2m.'), page: inPageLanguage('Der Gewinn sinkt, wenn die Energiepreise steigen und die Nachfrage nachlässt.'),
        english: inPageLanguage('Profit falls when energy prices rise and the demand for the product softens.'),
        styles: localMoney('Profit falls by 44.300 € to €27,700, or by 1 400 € and 12,50 €; not €1.2m.')}));
      return {de: fmt('de'), fr: fmt('fr'), en: fmt('en'), ar: fmt('ar'), hi: fmt('hi'),
        parse: [parseNum('\u0664\u0664\u066c\u0663\u0660\u0660'), parseNum('9,00,000'), parseNum('44\u202f300 €'), parseNum('\u200f\u200e-72,000'), parseNum('1.200.000,50'), parseNum('12,5')]};
    }""")
    de, fr, en = r["de"], r["fr"], r["en"]
    check(en["money"] == "€44,300" and en["signed"] == "+€14,400" and en["pct"] == "+3.3%" and en["axis"] == "−€20k", f"English formats unchanged {en}")
    check(de["money"] == "44.300\u00a0€" and de["share"] == "62\u00a0%" and de["axis"] == "−20k\u00a0€", f"German formats {de}")
    check(fr["money"] == "44\u202f300\u00a0€", f"French groups thousands with a narrow space {fr['money']!r}")
    check(r["hi"]["money"] == "€44,300" and r["ar"]["money"].endswith("€"), f"Hindi and Arabic money {r['hi']['money']!r} {r['ar']['money']!r}")
    check(de["local"] == "Der Gewinn sinkt um 14.400\u00a0€ auf 58.600\u00a0€, nicht €1.2m.", f"English-style amounts in a German answer are rewritten: {de['local']!r}")
    check(de["page"] and not de["english"], "an answer in the page's language is recognised, an English one is not")
    check(r["ar"]["styles"] == "Profit falls by \u200f44,300\u00a0€ to \u200f27,700\u00a0€, or by \u200f1,400\u00a0€ and \u200f12.50\u00a0€; not €1.2m."
          and fr["styles"] == "Profit falls by 44\u202f300\u00a0€ to 27\u202f700\u00a0€, or by 1\u202f400\u00a0€ and 12,50\u00a0€; not €1.2m.",
          f"amounts in any style are rewritten the language's way {r['ar']['styles']!r} {fr['styles']!r}")
    check(r["parse"] == [44300, 900000, 44300, -72000, 1200000.5, 12.5], f"typed numbers in any style and digit script {r['parse']}")
    pg.close()


def test_pdf_language(browser, base):
    print("PDF report language")
    init = "window.__saved = []; window.claude = {use: async n => n === 'downloads' ? {save: async f => { window.__saved.push(f.filename); }} : null};"
    for lang, english in (("fr", False), ("ru", True)):
        pg = page_with(browser, f"{base}?plan=pro&lang={lang}", init=init)
        pg.click("#pdfBtn")
        pg.wait_for_function("window.__saved.length > 0 || !document.getElementById('toast').hidden", timeout=15000)
        pg.wait_for_function("!document.getElementById('toast').hidden", timeout=15000)
        toast, want = pg.evaluate("[document.getElementById('toast').textContent, t(" + ("'pdfInEnglish'" if english else "'pdfSaved'") + ")]")
        check(toast == want and pg.evaluate("window.__saved.length") == 1, f"{lang}: report saved{' in English, and the toast says so' if english else ' in French'} ({toast})")
        pg.close()


def test_alerts(browser, base):
    print("since the last visit, and the alert email sign-up")
    seen = """(() => { try { localStorage.setItem('mrm3-seen', JSON.stringify({asOf: '2000-01', regime: 'Recovery', clients: {c1: 0, c2: 99, c3: 41}})); } catch (e) {} })();"""
    pg = page_with(browser, f"{base}?plan=business&lang=de#clients", init=SEED + seen)
    pg.wait_for_function("!document.getElementById('changes').hidden", timeout=5000)
    v = pg.evaluate("""() => ({text: document.getElementById('changes').innerText, title: T.de.chgTitle, regime: T.de[D.current.regime],
      top: Math.round(document.getElementById('changes').getBoundingClientRect().top), shares: Object.fromEntries(clients.map(c => [c.name, Math.round(clientShare(c))]))})""")
    check(v["title"] in v["text"] and v["regime"] in v["text"], f"the panel says what changed, in the page's language ({v['text'][:80]!r})")
    above = v["text"].split("\n")
    check(any("Bäckerei Müller" in l and "Warnschwelle" in l for l in above), f"a business that crossed the alert level is named {v['shares']}")
    check(any("Gasthaus zur Post" in l and "5 Punkte" in l for l in above), "a business whose risk rose by 5 points or more is named")
    check(not any("Druckerei Weiß" in l for l in above), "a business still above the level but lower than before is not named")
    check(50 <= v["top"] <= 120, f"the email link lands on the panel, below the sticky header ({v['top']}px from the top)")
    pg.click("#chgOk")
    check(pg.evaluate("document.getElementById('changes').hidden && JSON.parse(localStorage.getItem('mrm3-seen')).asOf === D.current.asOf"), "Got it hides it until new data arrives")
    check(not pg.is_visible("#subscribe"), "no sign-up form until an email service is set")
    check(not pg.errors, f"no console errors {pg.errors[:2]}")
    pg.close()
    form = "window.MRM_ALERTS_FORM = 'https://buttondown.com/api/emails/embed-subscribe/example';"
    pg = page_with(browser, f"{base}?plan=business&lang=fr", init=SEED + form)
    v = pg.evaluate("""() => { const f = document.getElementById('subscribe'); return {shown: !f.hidden && f.getClientRects().length > 0, action: f.action, method: f.method,
      embed: f.querySelector('[name=embed]').value, lang: f.querySelector('[name=metadata__language]').value, email: f.querySelector('[name=email]').type, changes: document.getElementById('changes').hidden}; }""")
    check(v["shown"] and v["action"].endswith("/embed-subscribe/example") and v["method"] == "post" and v["embed"] == "1" and v["email"] == "email",
          f"the sign-up form posts straight to the email service {v}")
    check(v["lang"] == "fr", "subscribers are tagged with the page's language")
    check(v["changes"], "a first visit shows no changes and only remembers this one")
    pg.close()


if __name__ == "__main__":
    base = serve()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for t in (test_plans, test_tools, test_server_transport, test_claude_transport, test_languages, test_formats, test_pdf_language, test_alerts):
            t(browser, base)
        browser.close()
    print(f"\n{len(failures)} failed" if failures else "\nall checks passed")
    sys.exit(1 if failures else 0)
