# Macro Risk Monitor — Improvement Guide (v1.0 → v1.1)

This file covers two things:

- **Part A**: the changes already made in this folder. Each one says what it is, why it matters, which files it touches and how it was tested.
- **Part B**: the next improvements, in priority order. They are recommended but not yet built, because each one needs a product decision or more work.

The live project was already strong: a lag-aware data pipeline, a backtested risk model, 13 languages, browser tests and no third-party requests. So these changes don't rebuild it. They target the places where it could **break without anyone noticing**, show **wrong numbers**, or be **attacked**.

> **Merged on 1 Nov 2026, with these changes to the package**
>
> - The refresh now runs the browser checks on each month's new data, and some of them assumed this month's figures ("Stagflation", a −44,300 bad year for the restaurant, fixing its loan always helping, which client is above the alert level). A simulated next month failed them, which would have stopped the first refresh with new data. They now compare the advisors page with the built tool, check the loan what-if by consistency (its sign is the data's business) and build the "since your last visit" baseline from the day's shares.
> - The stale-data unit test counts from the data's own month instead of a fixed date (it would have failed from the February 2027 data on).
> - The monthly email, not only the GitHub issue, now marks a phase in its first month as provisional and calls a flip straight back noise.
> - `data.json` keeps its provenance when nothing else changed, so a refresh without new data commits nothing (the build time alone used to make a new 690 KB page every time).
> - Python 3.12 in `pyproject.toml` (numpy 2.5 and scipy 1.18 from `constraints.txt` need it).
> - The Worker changes (Step 10) go live only after `npx wrangler deploy` from the `worker/` folder.
>
> Tests after the merge: 26 Python unit tests, 24 assistant server tests, all browser checks (including the policy check) and all site checks, on the real data and on simulated next months.

---

## How to use this folder

```bash
make install          # Python packages at the tested versions + Chromium for the browser tests
make test             # lint + unit tests + assistant-server tests + browser checks (everything CI runs)
make page && make serve   # build docs/ and open http://localhost:8000
make data             # optional: download fresh data (needs FRED_API_KEY in .env)
```

To deploy, push the folder to GitHub, the same way as before (README → "Deploy").

---

## Part A — Improvements implemented

### Step 1: One shared place for the series list and publication lags
**Files:** `macro_config.py` (new), `pipeline.py`, `build_data.py`

**What:** The 21 series and their publication lags were defined twice: in `pipeline.py` (`SERIES`) and again by hand in `build_data.py` (`LAG`). Both scripts now import one list from `macro_config.py`. The list also marks the 7 series the model can't do without as `critical`.

**Why:** The lag is what stops the model from using data before it was published. That's the main protection against look-ahead bias in the backtest. If someone changed a lag in one file and not the other, the model and the pipeline would quietly disagree, and the backtest would look better than it really is. With only one copy, that mismatch can't happen. A unit test also pins the old values so they can't drift by accident.

**Also moved:** the German minimum wage (13.90 → 14.60 € from 2027-01). It was hard-coded inside `build_data.py`. It's now in the config with a date check, so the build prints a warning once the rise has taken effect and the figure needs updating.

### Step 2: A data quality gate, so a broken download can't reach the live page
**Files:** `pipeline.py` (`--strict`, `quality_gate`), `validate_data.py` (new), `.github/workflows/refresh.yml`

**What:**
1. `pipeline.py --strict` exits with code 2 if a **critical** series failed to download or is more than 3 months stale.
2. `validate_data.py` checks the built `data.json` before it's published:
   - all required keys are present
   - the regime is one of the four valid names
   - the values are in plausible ranges (e.g. Euribor −2..15 %, which catches a basis-points/percent mix-up)
   - HMM probabilities add up to 1
   - there are at least 150 historical windows, every row has the right length and contains no NaN/∞
   - today's regime has history behind it
   - all five scenarios are present
   - the data is not more than 4 months old
3. The scheduled refresh runs both checks **before** committing. If either fails, the refresh stops, the **last good page stays online**, and a GitHub issue is opened.

**Why:** Previously the refresh committed whatever came out of the build. ECB, Eurostat and FRED change dataset codes and formats; the ECB already replaced ICP with HICP in February 2026. Then a half-empty or wrongly parsed series would go straight to the live page and into the monthly email to advisors. For a tool that tells a business "the economy could take €44,300 of your profit", a silently wrong number is the worst kind of failure.

### Step 3: Run the tests in the scheduled refresh too
**File:** `.github/workflows/refresh.yml`

**What:**
- The refresh now runs `pytest`, `tests/test_page.py` and `tests/test_site.py` on the freshly built page before committing.
- `concurrency: refresh` prevents two refreshes from pushing over each other.
- `timeout-minutes` stops a hung run.
- `git pull --rebase` before push avoids a rejected push if someone committed meanwhile.
- A **"Data refresh failed"** issue is opened whenever any step fails.

**Why:** The tests ran on every push, but **not** on the twice-monthly refresh. The refresh is the one job that deploys new numbers with nobody watching. Before, a failed refresh was silent: the page just got older. Now you get an email (GitHub issues notify watchers).

### Step 4: Python unit tests for the model's building blocks
**Files:** `tests/unit/conftest.py`, `tests/unit/test_model.py` (new), `pyproject.toml`

**What:** 22 fast tests that need no network and no browser (about 1.5 s). They cover:
- lags have a single source and match the old values
- every factor reads a series the pipeline actually downloads
- daily → monthly averaging, and ECB month codes
- the panel only uses published values (industrial production lags 2 months)
- revisions are counted
- the quality gate (failed critical series, stale series)
- a failing fetch never crashes the run
- the regime quadrants
- the new HMM filter gives exactly the same probabilities as the old loop
- **a full end-to-end `build_data` run on a synthetic 2000–2026 panel**, whose output passes the data gate and keeps the Covid months out of the sales history
- the data gate catches stale data, unit errors, broken rows, a missing scenario and an unknown regime
- the alert rules (provisional regime, flip-back, thresholds)

**Why:** The existing tests were all browser tests of the finished page. They catch a broken page, but not a quietly wrong number from the Python model, such as a lag error, a lost Covid exclusion or a shifted HMM label. These tests catch those errors in seconds, on every push.

### Step 5: HMM filter in one pass (O(T) instead of O(T²))
**File:** `build_data.py` (`filtered_probs`)

**What:** The statistical second opinion needs "P(state | data up to month t)" for every month. The old code called `predict_proba` on every prefix of the history, which re-runs forward–backward over the whole prefix about 320 times. The new function does one forward pass.

**Why:** Measured on 300 months: **205 ms → 2 ms (~90× faster)**, with a maximum difference of 1e-13, so the results are identical (a unit test checks this). Today the time saved is small. But the cost grows with the square of the history length, so it starts to matter once history goes back further (Part B, step 6) or the backtest refits the HMM each month (Part B, step 7).

### Step 6: Provenance in data.json
**File:** `build_data.py` (`meta` block)

**What:** `data.json` now records:
- `schema` version
- `generatedAt` (UTC)
- `code` (the git commit)
- the number of windows
- each series' last period and fetch status

**Why:** If an advisor asks "where does this number come from?", or a bank questions a report, you can trace it to the exact build and data vintage. It also makes debugging a bad refresh much faster. Older pages ignore the extra key, so this change is backwards compatible.

### Step 7: Smaller pipeline fixes
**File:** `pipeline.py`, `build_data.py`

- **Parallel downloads:** the 21 independent downloads run 6 at a time instead of one after another (several times faster on a normal connection). The database is still written from one thread in a fixed order, so the result is the same.
- **One HTTP session with a User-Agent:** connections are reused, and the statistics offices can identify the client. Anonymous scripts are the first to be rate-limited.
- **Warnings are no longer all switched off:** `warnings.filterwarnings("ignore")` hid *every* warning, including real ones like division by zero in a price move. Now only hmmlearn's convergence chatter and pandas' FutureWarnings are silenced.
- `zip(..., strict=True)` in the Eurostat parser: a malformed JSON-stat answer now fails loudly instead of being read wrongly.

### Step 8: Fewer false alarms in the regime alerts
**File:** `check_alerts.py`

**What:**
- A regime change in its **first month** is labelled *"provisional: first month, confirm next update"*.
- A flip straight back (A → B → A) is called out as noise.
- The thresholds are in one table, and the logic is a function, so it can be tested.

**Why:** The compass reads the *sign of a 6-month change*, so it can flip for a single month and flip back. Each flip opened a GitHub issue and could trigger an email to advisors. Labelling the first month as provisional keeps the information without asking advisors to re-plan on one month's reading.

### Step 9: Content-Security-Policy on the website
**Files:** `build_page.py` (`with_csp`), `tests/test_page.py` (`test_csp`), plus `bypass_csp` in `site/build_site.py`, `alerts/digest.py` and `assets/og_image.py` (build automation only)

**What:** The build adds a strict CSP `<meta>` tag:
- `default-src 'none'`
- scripts only from the site itself, or the page's own inline scripts allowed **by SHA-256 hash** (no `'unsafe-inline'`, no `eval`)
- network access only to the assistant server named in `assistant/config.json`
- forms may post only to the site itself and Buttondown

**Why:** The page handles client names, DATEV ledgers and financial figures. It promised "no third-party requests"; the CSP makes the **browser enforce** that promise. Tested:
- an injected `<script>` does **not** run
- `fetch('https://evil.example')` is **blocked**
- the PDF report, the assistant and all plans still work, with **zero policy violations** (new browser test)

GitHub Pages can't send HTTP headers, so the policy is in a meta tag (which can't set `frame-ancestors`; see Part B).

The non-standard `<meta name="claude:viewport-fit-sides">` (a Claude preview hint) is now kept out of the website build.

### Step 10: Assistant server (Cloudflare Worker) fixes
**Files:** `worker/src/handler.js`, `worker/test/handler.test.mjs` (5 new tests), `template.html` (2-word mapping)

1. **Bug fix: tool-mode fallback per model.** `nativeRefused` was one flag for the whole Worker. If *one* model refused native tool calling, *every* model, including Gemma 4 which the site uses, was switched to the weaker prompt-based tool calling until the isolate restarted. It's now tracked **per model**, and `/health` lists which models are in prompt mode.
2. **Timeout:** a model call that hangs now ends after 25 s (set by `TIMEOUT_MS`) with HTTP 504 `timeout`. The page shows "try again" with a retry button instead of a spinner that never stops. A timeout no longer triggers the prompt-mode retry, which would only have doubled the wait.
3. **Security headers** on every response: `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, alongside the existing `no-store`.

**Why:** These are reliability and quality bugs that users would notice ("the assistant got worse" or "it froze") but that are hard to trace back to the cause.

### Step 11: Reproducible builds and dependency hygiene
**Files:** `constraints.txt`, `requirements-dev.txt`, `.github/dependabot.yml`, `.github/workflows/tests.yml`, `pyproject.toml`, `Makefile`, `.gitignore`

**What:**
- CI installs **exact tested versions** (`-c constraints.txt`).
- Dependabot opens weekly pull requests for Actions, pip and npm updates.
- A new `unit` CI job runs ruff lint, pytest and the data gate on the committed data.
- `concurrency` cancels outdated test runs.
- A `Makefile` covers the common commands.

**Why:** `requirements.txt` only had `>=` ranges. A new pandas, NumPy or hmmlearn release on a refresh day could have changed the HMM states or the bad-year figures with no code change and no review. Now updates arrive as pull requests, and the tests run before anything reaches the live page.

### Test results for this folder

| Suite | Result |
|---|---|
| Python unit tests (`pytest`) | **26 passed** (22 from the package, 4 added on merge) |
| Ruff lint on the Python modules | **clean** |
| Assistant server (`node --test`) | **24 passed** (19 existing + 5 new) |
| Browser checks (`tests/test_page.py`) incl. new CSP check | **all checks passed** |
| Pages around the tool (`tests/test_site.py`) | **all checks passed** |
| Alert email dry run | **ok** |
| Published `docs/data.json` against the new gate | **ok** (as of 2026-09, 287 windows) |

Not tested here: a live download from ECB, Eurostat and FRED, because this workspace has no access to those sites. The pipeline changes are covered by the unit tests with mocked fetchers. The first scheduled refresh after you push will be the live check, and with the new gate, a problem stops it safely.

---

## Part B — Recommended next improvements (not yet built)

These are ordered by value for effort. Each one says **why** and **how**.

### 1. Legal pages before any real user (must-do)
**Why:** German law requires an Impressum and a Datenschutzerklärung for a business website. The privacy page must name Cloudflare (for the assistant) and Buttondown (for email), and the Buttondown double opt-in is required under UWG/GDPR. Without these, inviting advisors creates legal exposure.
**How:**
1. Write `docs/impressum.html` and `docs/datenschutz.html`.
2. Remove every `[[placeholder]]`.
3. Run `python build_page.py`. The footer links appear automatically.

### 2. Encrypt saved client data in the browser
**Why:** The Business plan stores clients' revenue, profit, loans and cash in `localStorage` as plain text. Tax-advisor offices often use shared PCs, and that data is confidential under §57 StBerG. Any script, extension or person with access to the browser profile can read it.
**How:**
1. Add an optional passphrase.
2. Derive a key with WebCrypto PBKDF2 (≥ 200k iterations), then encrypt the client list with AES-GCM.
3. Keep the key in memory only.
4. Add an "auto-lock after 15 minutes" option.
5. Add a test that the stored value is not readable JSON.

### 3. Load only the visitor's language
**Why:** `docs/index.html` is about 690 KB (290 KB gzipped). **334 KB of it is the 13 language packs**, although each visitor reads one. Cutting this roughly halves load time on mobile networks, which matters for owners checking on a phone.
**How:**
1. In `build_page.py`, embed English plus the language from the URL/browser, and write the other packs to `docs/i18n/<code>.js`.
2. In the template, make `setLang()` async and `import()` the pack when it's missing (CSP already allows `'self'`).
3. Keep `--bare` single-file for the Claude preview.

### 4. Split the 1,470-line inline script into modules
**Why:** `template.html` is 182 KB with all logic in one `<script>`: the risk model, the charts, DATEV import, PDF, the assistant and client storage. That makes reviews, merge conflicts and unit-testing the risk maths in isolation hard.
**How:**
1. Move the code into `src/js/model.js`, `charts.js`, `datev.js`, `assistant.js` and so on.
2. Have `build_page.py` concatenate them in order (no bundler needed). The output stays identical.
3. Then test `model.js` directly with `node --test`, the same way the worker is tested.

### 5. Enforce plans before charging money
**Why:** The README says so itself: anyone can switch to Business with `?plan=business`. That's fine for a prototype, but not for €19 or €70 a month.
**How:** Add a sign-in, such as Cloudflare Access or a magic-link login on the existing Worker, and Stripe Checkout. The Worker issues a signed plan token, and the page unlocks Pro/Business features only with a valid token. The assistant server should also check the token instead of a shared access code.

### 6. Longer history to cover more shocks
**Why:** History starts in 2000, so the model has seen only a few big shocks. The backtest shows it: the online importer (10.3 % misses) and the print shop (16 %) **fail** the Kupiec test. Both missed shocks larger than anything in their history.
**How:**
1. Extend the core series back to 1991 (German reunification) from Bundesbank time series (Euribor → FIBOR 3M, HICP → German CPI, DM/USD) and OECD MEI.
2. Mark the splice dates.
3. Rerun the backtest. Expect more windows and better coverage of the 1992–93 ERM crisis and the 2000 dot-com bust.

### 7. Remove look-ahead from the HMM's history
**Why:** The HMM is fitted once on **all** data, including the future, and then its probabilities are read for past months. So the historical HMM states shown in the "Twenty years of regimes" chart, and any backtest that uses them, use parameters fitted with information that wasn't available at the time. The compass regime has no such problem.
**How:** In the backtest, refit the HMM on an expanding window (data up to t only). The new O(T) filter keeps that affordable. Alternatively, say clearly on the page that the HMM history is "fitted in hindsight".

### 8. Deploy Pages from a build artifact, not committed files
**Why:** Every refresh commits a new 690 KB `index.html`, screenshots and data: about 24 large commits a year. Fonts, `site.css` and `portfolio.js` also exist twice (source and `docs/`). The repository grows without limit, and diffs are unreadable.
**How:**
1. Switch Settings → Pages to "GitHub Actions".
2. Upload `docs/` with `actions/upload-pages-artifact` and publish with `actions/deploy-pages`.
3. Commit only `data.json` and `alerts/last.json` (small, and worth keeping as history).

### 9. Pin GitHub Actions to commit SHAs
**Why:** `actions/checkout@v5` is a moving tag. A compromised action (as with `tj-actions/changed-files` in 2025) would run with `contents: write`, which is enough to change the live page.
**How:** Replace each `@v5`/`@v6` with its full commit SHA plus a version comment. Dependabot (already added) keeps them current.

### 10. Real HTTP security headers
**Why:** A meta-tag CSP can't set `frame-ancestors`, so the page can still be framed (clickjacking), and GitHub Pages can't send `Permissions-Policy` or `X-Frame-Options`.
**How:** Serve `docs/` through Cloudflare Pages (free, and the Worker already runs there). A `_headers` file then sets the CSP as a header, `frame-ancestors 'none'`, `Permissions-Policy: camera=(), microphone=(), geolocation=()` and HSTS.

### 11. Accessibility and no-JavaScript fallback
**Why:** Without JavaScript the page is empty. This also affects search engines and some reader tools. The page already has good contrast and labels, but there's no automated accessibility check.
**How:**
1. Add a `<noscript>` block with the headline example (€44,300 of €72,000) and a link to the advisors page.
2. Add `axe-core` (vendored, same-origin) to `tests/test_page.py` and fail on serious violations in all 13 languages and the phone layout.

### 12. Watch the assistant server
**Why:** If the Worker is down or the free daily allowance runs out, nobody notices until an advisor complains.
**How:** Add a scheduled GitHub Action (hourly) that calls `/health` and opens an issue after 3 failures in a row. Add a daily budget per access code in the Worker (Workers KV or Durable Objects) so one office can't use up everyone's allowance.

### 13. Integrity check for the CDN copy of jsPDF
**Why:** In the `--bare` build (Claude preview), jsPDF is loaded from cdnjs **without** Subresource Integrity, so a compromised CDN file would run in the page.
**How:** Set `s.integrity = 'sha384-…'` and `s.crossOrigin = 'anonymous'` in the loader, using the hash of `docs/vendor/jspdf.umd.min.js` (the same 2.5.1 file, but verify it matches the cdnjs copy byte for byte first).

---

## Folder structure (what changed)

```
macro-risk-monitor/
├── IMPROVEMENTS.md              NEW  this guide
├── macro_config.py              NEW  series, lags, critical flags, minimum wage (one copy)
├── validate_data.py             NEW  data gate before publishing
├── pyproject.toml               NEW  pytest + ruff configuration
├── constraints.txt              NEW  exact tested package versions
├── requirements-dev.txt         NEW  test/lint tools
├── Makefile                     NEW  install / data / page / test / serve
├── pipeline.py                  CHANGED  shared config, parallel fetch, --strict quality gate
├── build_data.py                CHANGED  shared lags, O(T) HMM filter, provenance meta, narrow warnings
├── build_page.py                CHANGED  Content-Security-Policy with script hashes, strip preview-only meta
├── check_alerts.py              CHANGED  provisional / flip-back labelling, testable
├── template.html                CHANGED  'timeout' error mapped to retry message (2 words)
├── README.md                    CHANGED  new commands, security section
├── .gitignore                   CHANGED
├── .github/
│   ├── dependabot.yml           NEW
│   └── workflows/
│       ├── tests.yml            CHANGED  + unit/lint job, pip cache, concurrency, timeouts
│       └── refresh.yml          CHANGED  strict fetch, data gate, tests before commit, failure issue
├── tests/
│   ├── unit/conftest.py         NEW  synthetic 2000–2026 panel
│   ├── unit/test_model.py       NEW  22 unit tests
│   └── test_page.py             CHANGED  + CSP check
├── worker/
│   ├── src/handler.js           CHANGED  per-model fallback, timeout, security headers
│   └── test/handler.test.mjs    CHANGED  + 5 tests
├── site/build_site.py, alerts/digest.py, assets/og_image.py   CHANGED  bypass_csp for build automation
├── docs/                        REBUILT  index.html (with CSP), advisors/, portfolio/, 404, sitemap
└── (everything else unchanged: i18n/, assistant/, assets/, site/templates, data)
```
