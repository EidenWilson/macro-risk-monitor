# Macro Risk Monitor

[![Tests](https://github.com/EidenWilson/macro-risk-monitor/actions/workflows/tests.yml/badge.svg)](https://github.com/EidenWilson/macro-risk-monitor/actions/workflows/tests.yml)

**Live:** [the risk check](https://eidenwilson.github.io/macro-risk-monitor/), [the Business plan with the AI assistant](https://eidenwilson.github.io/macro-risk-monitor/?plan=business), [the page for tax advisors and banks](https://eidenwilson.github.io/macro-risk-monitor/advisors/) and [the portfolio stress test](https://eidenwilson.github.io/macro-risk-monitor/portfolio/)

![The page for the example restaurant: in a bad year the economy could take €44,200 of its €72,000 profit, with the biggest risks, the cash outlook and the economy below](assets/screenshot.png)

**60-second demo** (silent, with subtitles): [English](https://eidenwilson.github.io/macro-risk-monitor/assets/video/demo-en.mp4) · [Deutsch](https://eidenwilson.github.io/macro-risk-monitor/assets/video/demo-de.mp4), also on [the advisors page](https://eidenwilson.github.io/macro-risk-monitor/advisors/#demo) with the subtitles as text.

A risk tool for German small and mid-sized businesses (Mittelstand). It turns live macro data into one question an owner can act on: **how much of next year's profit could the economy take, and what is protection worth?**

The page answers first and explains after: a one-sentence headline, four tiles (biggest risks, whether cash lasts, the economy now, the best protection), eight basic inputs with the rest under "More detail", and the technical charts folded into one "How reliable is this?" section. It comes in three plans: **Free** for a quick check, **Pro** for the full analysis and **Business** for tax advisors and banks, with an AI risk assistant.

Built by Eiden Wilson Neralaily (data and modelling) and Channappa (finance and risk), ISM Munich.

## Highlights

- **End-to-end data pipeline.** 21 series from the ECB, Eurostat and FRED, each shifted by its publication lag, stored in SQLite and checked for new releases every day by GitHub Actions, which keeps every value as first published and every later revision (`data/vintages.csv`) and opens an issue when the economic regime changes.
- **Risk model.** Filtered historical simulation over 280+ past 12-month windows, with the current regime and a 4-state hidden Markov model as alternative views, and an out-of-sample backtest since 2008 (Kupiec test adjusted for overlapping windows).
- **Product.** Answer first, Free, Pro and Business plans, DATEV import (SKR03 and SKR04), a PDF report with figures or with charts, 13 languages (Arabic right to left), a phone layout, no cookies, and no third-party requests outside the Business assistant.
- **AI assistant (Business).** An open model (Gemma 4) on Cloudflare Workers AI, on the free plan. It never computes numbers itself: it calls tools that run the page's own risk model. Client names never leave the browser, and a routing step plus a check of every euro amount keep it from inventing figures.
- **Portfolio stress test.** Load a fund report's or a bank's holdings list (CSV, German or English formats): yields solved from prices and coupon dates, duration, convexity and the value of a basis point, then five stated scenarios and one of your own, split into rates, spreads, inflation, equities and gold, holding by holding. In the browser, nothing uploaded.
- **Tested.** Server tests and browser checks (plans, phone layout, the assistant's tools and conversation loop, bond arithmetic against closed forms, the pages around the tool) run on every push.

**Stack:** Python (pandas, NumPy, SciPy, hmmlearn, SQLite) · JavaScript without a framework · Cloudflare Workers and Workers AI · GitHub Actions and Pages · Playwright

<img src="assets/assistant.jpg" width="560" alt="The risk assistant on the live site: asked what happens if energy prices rise 30%, it applies the shock with the page's model and answers that next year's profit would be €55,400, €7,000 less than without the shock">

## What it does

| Feature | What the owner sees | Plan |
|---|---|---|
| Profit at risk | A 1-in-20 bad-year loss for the business, from 280+ historical 12-month windows | Free |
| Biggest risks | Where a bad year hits: each cost line's share of the loss, averaged over the worst 1-in-20 outcomes | Free |
| Cash check | Whether cash and the credit line last a bad year, and the lowest balance | Free |
| Regime compass | Whether Germany is in Recovery, Overheating, Stagflation or Contraction, and the path over the last 24 months | Free |
| Stress scenario | Profit bridge if the 2022 energy shock repeated | Free |
| More stress scenarios | 2008, 2011, the 2020 lockdown and the last 12 months as well | Pro |
| Cash runway | Month-by-month bank balance in a flat, typical and bad year, against the credit line | Pro |
| Contract calendar | When each exposure becomes live: loan resets, energy contract end, Indexmiete adjustment, minimum wage rise | Pro |
| Protection value | Net value of fixing the loan rate, fixing energy, passing on costs, hedging USD, after the cost of each | Pro |
| Backtest | Out-of-sample check of the bad-year forecast since 2008 with a Kupiec test adjusted for overlapping windows, and which shocks the misses came from | Pro |
| Statistical second opinion | A 4-state hidden Markov model on growth and inflation levels with filtered probabilities | Pro |
| DATEV import | Reads a Summen- und Saldenliste (SKR03 or SKR04) in the browser and fills the exposures | Pro |
| Report | PDF for a bank or tax advisor meeting: the figures, or the same answers as charts (risks, range of outcomes, cash, past shocks, protection, backtest, 20 years of phases) | Pro |
| Advisor mode | Save many businesses and rank them by risk, with an alert threshold | Business |
| Risk assistant | Chat that explains the results, runs what-ifs on the model, compares clients and drafts client notes | Business |
| Since your last visit | When new data arrives: which saved businesses crossed the alert level or rose by 5 points or more, and a change of economic phase (remembered in the browser only) | Business |
| Monthly alert email | A German and English digest when new data arrives: the phase, key rates, how risk moved for typical businesses, and a link that re-ranks the advisor's own clients | Business |
| 13 languages | English, German, Spanish, French, Italian, Portuguese, Turkish, Polish, Ukrainian, Russian, Arabic (right to left), Hindi and Chinese. Opens in the browser's language; money, percentages and months follow each language (44.300 €, 44 300 €, €4,43,000, 2026年9月); reads 900.000, 900,000 or Arabic-Indic digits either way | All |
| Phone layout | Results first; the inputs open as a sheet from the bottom | All |

Prices on the plans dialog (Free, Pro €19 a month, Business €70 a month per office for up to 50 clients) are placeholders. This is a prototype, so anyone can switch plans to try them; `?plan=business` (or `#business` in the Claude preview) opens a plan directly.

## How it works

```
ECB Data Portal ─┐
Eurostat ────────┼─> pipeline.py ──> macro.db (SQLite, publication-lag aware)
FRED ────────────┘                        │
                                          ├─> vintage.py ──> data/vintages.csv (every value as first seen, every revision)
                                          v
                               build_data.py ──> data.json
                               (regimes, HMM, 12-month forward moves,
                                trailing volatility, scenarios)
                                          │
                                          v
                               build_page.py ──> docs/index.html (GitHub Pages)
                                          │
                               check_alerts.py ──> GitHub issue on regime change
```

**Risk assistant (Business).** The language model never computes a number. Each question goes out with a short summary of the page (about 3 KB of figures); anything new comes from three tools that run the same in-browser model as the charts:

```
 question ──> page ──> model (Claude in the preview, Workers AI on the website)
               ^          │ asks for a tool: what_if, shock or compare_clients
               │          v
               └── page runs the tool on the in-browser model and sends back the result
                          │
                          v
                answer, with an "Apply these changes to the page" button after a what-if
```

- `what_if` recalculates the business with changed figures and protection actions, `shock` applies a custom 12-month move (energy, Euribor, purchase prices, inflation, sales, the dollar, oil), `compare_clients` ranks the saved clients.
- Client names never leave the browser: the model sees codes (C1, C2, ...), a name typed into a question is swapped for its code before sending, and the page puts the names back into the answer.
- The instructions and tool definitions live in `assistant/` and are shared by the page and the server, so the two always agree.
- In the Claude preview the page asks Claude through the viewer's own account (it asks for permission once). On the website it talks to a small Cloudflare Worker in `worker/`, which adds the fixed instructions, calls a Workers AI model with function calling and returns text or tool calls in one shape whatever format the model answers in. The Worker only accepts requests from the site, limits each visitor to 12 requests a minute, can require an access code and stores nothing.

**Data.** Euribor 3M, EUR/USD, the AAA yield curve and German HICP (headline, food, energy, electricity, gas) from the ECB; industrial production, retail and food-service sales, consumer confidence and producer prices for food, metals, paper, plastics and wood from Eurostat; Brent, EU gas and the US 1-year rate from FRED. 21 series in total. Each series is shifted by its publication lag so every month only uses what was public at the time. The ECB replaced its ICP dataset with HICP (ECOICOP 2) in February 2026; the pipeline uses the new codes.

**Regimes.** The compass uses the sign of the 6-month change in 3-month-average inflation and industrial output. The HMM is a 4-state Gaussian model on their levels, fitted with 10 restarts; states are labelled from their means (Expansion, Sluggish growth, Inflation shock, Recession).

**Covid.** From March 2020 to March 2022, Covid rules closed or restricted restaurants and shops. Those sales swings came from public-health orders, not the economy, so retail and food-service windows that touch this period are left out of the risk range and the backtest, and their volatility excludes it. The 2020 lockdown is a separate stress scenario instead (before government support such as Kurzarbeit). Other scenarios that overlap the rules, such as the 2022 energy shock, measure sales over the first 12 months after the rules ended (April 2022 to April 2023).

**Risk model.** For each past month, the actual moves of rates, prices, FX and demand over the following 12 months are applied to the business, building up linearly through the year and respecting contract timing. Moves are scaled by today's volatility relative to volatility at the time (filtered historical simulation), with the past volatility floored at its median so calm periods such as zero rates do not inflate the scale. The bad year is the 5th percentile of that distribution over every past month since 2000. Until 9 November 2026 the page drew only on months from today's phase of the economy; that view is still a tab, but an audit found the full pool more robust (fewer misses for three of the six examples, no more for the others, and a 12% better quantile score, though the gain is not statistically significant): [`research/backtest_audit/`](research/backtest_audit/).

**Validation.** Since 2008, for every month the model forecast a 1-in-20 loss using only data available then and compared it with what happened (213 checks per business, target 5% of months worse than forecast). Neighbouring checks share 11 of their 12 months, so the 213 checks hold only about 18 independent years; the Kupiec test is scaled for that (its statistic divided by 12), which gives about the same p-values as a Newey-West test over the overlap.

| Example business | Worse than forecast | Kupiec test, adjusted for overlap | Unadjusted | Where the misses came from |
|---|---|---|---|---|
| Restaurant | 5.6% | passes (p = 0.90) | 0.68 | 10 of 12 in the 2021–23 energy and inflation shock |
| Bakery | 7.5% | passes (p = 0.65) | 0.12 | 15 of 16 in the 2021–23 shock |
| Metal workshop | 4.7% | passes (p = 0.95) | 0.84 | 2021–23 shock (7), 2020 Covid slump (3) |
| Food truck | 1.4% | passes (p = 0.42) | 0.005 | the bad year is cautious |
| Online importer | 8.5% | passes (p = 0.54) | 0.035 | 2021–23 shock (9), 2014–15 dollar surge (6), 2008–09 (2) |
| Print shop | 8.9% | passes (p = 0.49) | 0.018 | 2008–09 (8), 2021–23 paper prices (7), 2020 (3) |

With about 18 independent years the test can catch a badly wrong model but cannot tell 5% from 9%, so the miss rates matter more than the pass. The model learns from history, so it misses shocks bigger than anything it had seen; the page says which shock each business's misses came from and, above 7.5%, how to read the bad year instead (for the print shop, roughly 1 in 11 rather than 1 in 20). Volatility scaling cut the restaurant's miss rate from about 21%, and taking Covid closures out of the history from 8.5%.

**Challenger.** A LightGBM quantile model, refitted every month on what was known at the time (38 inputs: phase, Euribor, inflation, and the trailing volatility and last published move of all 16 factors), did worse: across the six examples its bad year was beaten in 20.6% of months against 7.9% for this model (same-phase pool, as the page was then; 6.1% with all months), it fails the Kupiec test for all six examples (three of six once the test allows for overlapping windows), and its quantile score is about 30% worse. With about 18 independent years and three or four real shocks there are too few bad years to learn a tail from. Code, setup and results: [`research/challenger/`](research/challenger/).

**Privacy and speed.** The page sets no cookies, loads nothing from third parties (the font is embedded and jsPDF is served from `docs/vendor/`), processes DATEV files and builds the PDF in the browser, and only writes to the browser's local storage once someone uses it (with a button to delete it). The exceptions are on the Business plan: when someone asks the assistant a question, the question and the page's computed figures (never client names or ledger lines) go to the assistant server and the model behind it; and when someone subscribes to the alert email, their email address (and nothing else) goes to Buttondown. Usage is counted anonymously: on the live site only, and not when the browser sends Do Not Track or Global Privacy Control, the page sends a beacon to the same assistant server for a page view and when a feature is used (own figures, PDF, DATEV import, client saved, question). The server keeps only daily totals per page, event, plan and language, in a Durable Object (`worker/src/counter.js`): no cookies, IDs, IP addresses or figures. The totals are at [`/stats`](https://mrm-assistant.eidenwilson.workers.dev/stats) (add `?days=30` for a shorter window). The privacy page needs a paragraph on all of this before the assistant goes live. The backtest runs only when its section is opened.

**Design.** The page is set like a bookkeeping sheet, the paper its users already read: blue-black ink for text and profit, red for losses (the German *schwarze* and *rote Zahlen*), the headline sum written out as a statement with a rule over the total and a double rule under the result, the inputs as white boxes on the tint of a printed form, and a highlighter for the one line that needs attention. The logo is the business-cycle clock from the economy section. One typeface, Sofia Sans Semi Condensed, in the spirit of German DIN lettering, with tabular figures wherever numbers line up.

## The pages around the tool

`site/` builds two more pages, each in English and German, in the tool's design: **/advisors/**, the page for tax advisors and banks (what it does, how it works, prices, a pilot request, questions), and **/portfolio/**, the portfolio stress test. `site/build_site.py` also writes `404.html` and `sitemap.xml`. The advisors page's figures (the example restaurant, the six examples ranked, the economy now, the backtest) are read from the built tool in a headless browser, and its three screenshots of the tool (a result, the client ranking, the backtest chart, in each page's language) are taken from it in the same run (`docs/assets/shots/`), so they always match it; the data refresh rebuilds both. Text lives in `site/text.py`; a contact address for pilot requests goes in `site/config.json` (`"contact_email"`), and the button appears once it is set.

The portfolio test reads any CSV with a name or ISIN and a market value; holding, price (in % for bonds), maturity and coupon make the bond arithmetic exact, and `type`, `duration` and `beta` columns override its guesses. `docs/portfolio/template.csv` shows the columns; the example is the first page of holdings from a published fund report (`site/data/example-fund.csv`, maturities checked against exchange listings). The scenarios are stated round numbers, not forecasts or replays of history.

## Run it locally

`make help` lists shortcuts (`make install`, `make data`, `make page`, `make test`, `make serve`). By hand:

```bash
pip install -r requirements-dev.txt -c constraints.txt   # the versions the model was tested with
echo "FRED_API_KEY=your_key" > .env          # free key from fred.stlouisfed.org
python pipeline.py --db macro.db --start 2000-01   # add --strict to stop when a critical series failed or is stale
python vintage.py --db macro.db --log data/vintages.csv   # append what is new or revised since the last run
python build_data.py --db macro.db --out docs/data.json
python validate_data.py docs/data.json          # the gate the scheduled refresh runs before publishing
python build_page.py                           # writes docs/index.html (text from i18n/, fonts from assets/, jsPDF from docs/vendor/)
python site/build_site.py                      # the advisors page, the portfolio stress test, 404 and sitemap (needs jinja2 and Playwright)
python assets/og_image.py                      # optional: the link-preview picture docs/og.png and the README screenshot
```

## Deploy (free)

1. Push this folder to a GitHub repository.
2. Settings → Secrets and variables → Actions → add `FRED_API_KEY`.
3. Settings → Pages → deploy from branch `main`, folder `/docs`.
4. Actions → "Refresh data and site" → Run workflow. It then runs every day by itself and opens an issue (which GitHub emails you) when the regime changes.

### The assistant server (Business plan, free)

The Workers Free plan covers it: 100,000 requests a day and 10,000 Workers AI neurons a day. The website uses Google's Gemma 4 (26B), chosen after comparing answers on the live site: about 150 questions a day, two to nine seconds each. When the allowance is used up, the assistant says so until 00:00 UTC; nothing is billed. No API keys are involved.

```bash
cd worker
npm install
npx wrangler login        # opens the browser; a free Cloudflare account is enough
npx wrangler deploy       # prints the URL, e.g. https://mrm-assistant.<your-subdomain>.workers.dev
curl https://mrm-assistant.<your-subdomain>.workers.dev/health
```

Then put that URL in `assistant/config.json` (`{"url": "https://mrm-assistant..."}`), run `python build_page.py` and push. Optional: `npx wrangler secret put ACCESS_CODE` so only people you give the code to can use it. The default model, the other models a page may ask for (`MODELS`), the allowed origins and how the model calls tools (`TOOL_MODE`) are set in `worker/wrangler.toml`. To switch the website to another model from `MODELS`, set `"model"` in `assistant/config.json` and rebuild; no redeploy is needed. If a question fails, `npx wrangler tail` in `worker/` shows the model's error while you ask again.

After the first deploy, GitHub keeps the server current: `.github/workflows/deploy-worker.yml` redeploys it whenever `worker/`, `assistant/prompt.txt` or `assistant/tools.json` change on `main` (server tests first, then a check that `/health` answers with the new code), and it can be run by hand from the Actions tab. It needs two repository secrets: `CLOUDFLARE_API_TOKEN` (Cloudflare dashboard → API Tokens → Create Token → template "Edit Cloudflare Workers", scoped to this account) and `CLOUDFLARE_ACCOUNT_ID`. Without them it skips with a warning.

## Alert email (Business, free up to 100 subscribers)

`alerts/digest.py` runs in the data refresh. It opens the built page in a headless browser, reads the economy's phase, the key rates and the six example businesses' bad-year risk from the page's own model, compares them with the last email (`alerts/last.json`) and, when new data has arrived, sends a short German and English digest through [Buttondown](https://buttondown.com). The email links to `?plan=business#clients`, where the page re-ranks the businesses the advisor saved in their own browser and shows what changed since their last visit; no client data is ever sent anywhere.

1. Create a free Buttondown account and make sure new subscribers confirm by email (double opt-in, which German law expects).
2. Put the username in `alerts/config.json` (`"buttondown": "your-username"`), run `python build_page.py` and push: the sign-up form appears on the Business plan.
3. Copy the API key from Buttondown's API page and add it to the repository as the Actions secret `BUTTONDOWN_API_KEY`.

`python alerts/digest.py --dry-run` prints the next email. Without the secret, the refresh only shows the digest in its run summary, ready to paste into Buttondown. Before inviting real subscribers, the legal notice and the privacy page need to be live, and the privacy page should name Buttondown.

## Languages

All text lives in `i18n/`, one file per language; `en.js` is the source and explains the format. To add a language, copy `en.js`, translate the values, set its `_` line (name in the menu, locale for numbers and dates, `dir: 'rtl'` for right-to-left scripts, `pdf: true` only for Western European languages) and rebuild. `build_page.py` stops on a key written twice and warns about missing ones (the page shows English for those); `tests/test_page.py` checks every language for matching keys, broken text, sideways scrolling and the right-to-left layout. The embedded font covers Latin (including Polish and Turkish letters) and Cyrillic; Arabic, Hindi and Chinese use the visitor's system fonts. The PDF's built-in fonts write Western European languages only, so other languages get an English report and are told so. The assistant answers in the language the question is written in.

## Tests

```bash
pytest                                                  # Python unit tests: lags, panel, HMM filter, data gate, alerts (no network)
cd worker && npm test                                   # assistant server: CORS, limits, access code, all model output formats
python build_page.py && python tests/test_page.py       # browser: plans, phone layout, tools, assistant loop, all 13 languages
python site/build_site.py && python tests/test_site.py # browser: bond arithmetic, file reading, scenarios, export, the pages around the tool
```

The browser checks need Playwright (`pip install playwright && python -m playwright install chromium`). They check that the assistant's tools reproduce the page's own numbers and that no client name appears in anything sent to the server or to Claude.

## Security

`build_page.py` adds a Content-Security-Policy to the website build: only this build's own inline scripts (by SHA-256
hash) and files from the site itself may run, and the page may only talk to the assistant server named in
`assistant/config.json` and post the alert sign-up to Buttondown. An injected script cannot run or send data
anywhere. `tests/test_page.py` checks that the policy breaks nothing (PDF, assistant, plans) and records no
violations. `--no-csp` turns it off for debugging.

The series list, publication lags and the minimum wage live in `macro_config.py`, shared by the pipeline and the
model build. See `IMPROVEMENTS.md` for everything changed in 1.1 and the recommended next steps.

## Legal pages

Germany requires a legal notice (Impressum) and a privacy policy. Put `impressum.html` and `datenschutz.html` in `docs/`; `build_page.py` links them in the footer once neither contains a `[[placeholder]]`.

## Licences

Sofia Sans Semi Condensed font: SIL Open Font License 1.1 (`assets/fonts/OFL.txt`). jsPDF: MIT (`docs/vendor/jspdf.LICENSE.txt`).

## Limits

- History starts in 2000, so the model has seen only a handful of big shocks; it underestimates anything larger than 2022.
- Overlapping 12-month windows leave the backtest only about 18 independent years; the overlap adjustment to the Kupiec test is approximate.
- Hedge costs are editable assumptions; the loan fixing cost uses the ECB 1-year rate when available.
- The assistant explains the model's figures; how well it phrases them depends on the model behind it, so advisors should check an answer before passing it to a client.
- Plans are not enforced: there are no accounts or payments yet. Only the assistant server is protected (origin check, rate limit, optional access code).
- Not financial advice.
