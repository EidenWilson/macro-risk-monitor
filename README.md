# Macro Risk Monitor

A risk tool for German small and mid-sized businesses (Mittelstand). It turns live macro data into one question an owner can act on: **how much of next year's profit could the economy take, and what is protection worth?**

Built by Eiden Wilson Neralaily (data and modelling) and Channappa (finance and risk), ISM Munich.

## What it does

| Feature | What the owner sees |
|---|---|
| Regime compass | Whether Germany is in Recovery, Overheating, Stagflation or Contraction, and the path over the last 24 months |
| Statistical second opinion | A 4-state hidden Markov model on growth and inflation levels with filtered probabilities |
| Profit at risk | A 1-in-20 bad-year loss for the business, from 280+ historical 12-month windows |
| Cash runway | Month-by-month bank balance in a flat, typical and bad year, against the credit line |
| Contract calendar | When each exposure becomes live: loan resets, energy contract end, Indexmiete adjustment, minimum wage rise |
| Stress scenarios | Profit bridge if 2008, 2011, 2022, the 2020 lockdown or the last 12 months repeated |
| Protection value | Net value of fixing the loan rate, fixing energy, passing on costs, hedging USD, after the cost of each |
| Backtest | Out-of-sample check of the bad-year forecast since 2008 with a Kupiec test, and which shocks the misses came from |
| DATEV import | Reads a Summen- und Saldenliste (SKR03 or SKR04) in the browser and fills the exposures |
| Report | One-page PDF for a bank or tax advisor meeting |
| Advisor mode | Save many businesses and rank them by risk, with an alert threshold |
| English and German | Full UI in both languages; reads 900.000 and 900,000 either way |
| Phone layout | Results first; the inputs open as a sheet from the bottom |

## How it works

```
ECB Data Portal ─┐
Eurostat ────────┼─> pipeline.py ──> macro.db (SQLite, publication-lag aware)
FRED ────────────┘                        │
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

**Data.** Euribor 3M, EUR/USD, the AAA yield curve and German HICP (headline, food, energy, electricity, gas) from the ECB; industrial production, retail and food-service sales, consumer confidence and producer prices for food, metals, paper, plastics and wood from Eurostat; Brent, EU gas and the US 1-year rate from FRED. 21 series in total. Each series is shifted by its publication lag so every month only uses what was public at the time. The ECB replaced its ICP dataset with HICP (ECOICOP 2) in February 2026; the pipeline uses the new codes.

**Regimes.** The compass uses the sign of the 6-month change in 3-month-average inflation and industrial output. The HMM is a 4-state Gaussian model on their levels, fitted with 10 restarts; states are labelled from their means (Expansion, Sluggish growth, Inflation shock, Recession).

**Covid.** From March 2020 to March 2022, Covid rules closed or restricted restaurants and shops. Those sales swings came from public-health orders, not the economy, so retail and food-service windows that touch this period are left out of the risk range and the backtest, and their volatility excludes it. The 2020 lockdown is a separate stress scenario instead (before government support such as Kurzarbeit). Other scenarios that overlap the rules, such as the 2022 energy shock, measure sales over the first 12 months after the rules ended (April 2022 to April 2023).

**Risk model.** For each past month, the actual moves of rates, prices, FX and demand over the following 12 months are applied to the business, building up linearly through the year and respecting contract timing. Moves are scaled by today's volatility relative to volatility at the time (filtered historical simulation), with the past volatility floored at its median so calm periods such as zero rates do not inflate the scale. The bad year is the 5th percentile of that distribution, conditional on the current regime.

**Validation.** Since 2008, for every month the model forecast a 1-in-20 loss using only data available then and compared it with what happened (213 checks per business, target 5% of months worse than forecast):

| Example business | Worse than forecast | Kupiec test | Where the misses came from |
|---|---|---|---|
| Restaurant | 5.6% | passes (p = 0.68) | 9 of 12 in the 2021–23 energy and inflation shock |
| Bakery | 7.5% | passes (p = 0.12) | all 16 in the 2021–23 shock |
| Metal workshop | 7.0% | passes (p = 0.20) | 13 of 15 in the 2021–23 shock |
| Food truck | 1.4% | fails on the cautious side (p = 0.005) | the bad year is overstated |
| Online importer | 10.3% | fails (p = 0.002) | 2021–23 shock (11), 2014–15 dollar surge (7), 2008–09 (3) |
| Print shop | 16.0% | fails (p < 0.001) | 2021–23 paper prices (13), 2008–09 (8), 2020 (5) |

The model learns from history, so it misses shocks bigger than anything it had seen; the page says which shock each business's misses came from and, when it fails, how to read the bad year instead (for the print shop, roughly 1 in 6 rather than 1 in 20). Volatility scaling cut the restaurant's miss rate from about 21%, and taking Covid closures out of the history from 8.5%.

**Privacy and speed.** The page sets no cookies, loads nothing from third parties (the Archivo font is embedded and jsPDF is served from `docs/vendor/`), processes DATEV files and builds the PDF in the browser, and only writes to the browser's local storage once someone uses it (with a button to delete it). The backtest runs only when its section comes into view.

## Run it locally

```bash
pip install -r requirements.txt
echo "FRED_API_KEY=your_key" > .env          # free key from fred.stlouisfed.org
python pipeline.py --db macro.db --start 2000-01
python build_data.py --db macro.db --out docs/data.json
python build_page.py                           # writes docs/index.html (font from assets/, jsPDF from docs/vendor/)
```

## Deploy (free)

1. Push this folder to a GitHub repository.
2. Settings → Secrets and variables → Actions → add `FRED_API_KEY`.
3. Settings → Pages → deploy from branch `main`, folder `/docs`.
4. Actions → "Refresh data and site" → Run workflow. It then runs twice a month by itself and opens an issue (which GitHub emails you) when the regime changes.

## Legal pages

Germany requires a legal notice (Impressum) and a privacy policy. Put `impressum.html` and `datenschutz.html` in `docs/`; `build_page.py` links them in the footer once neither contains a `[[placeholder]]`.

## Licences

Archivo font: SIL Open Font License 1.1 (`assets/OFL.txt`). jsPDF: MIT (`docs/vendor/jspdf.LICENSE.txt`).

## Limits

- History starts in 2000, so the model has seen only a handful of big shocks; it underestimates anything larger than 2022.
- Overlapping 12-month windows mean the backtest's observations are not independent.
- Hedge costs are editable assumptions; the loan fixing cost uses the ECB 1-year rate when available.
- Not financial advice.
