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
| Stress scenarios | Profit bridge if 2008, 2011, 2022 or the last 12 months repeated |
| Protection value | Net value of fixing the loan rate, fixing energy, passing on costs, hedging USD, after the cost of each |
| Backtest | Out-of-sample check of the bad-year forecast since 2008 with a Kupiec test |
| DATEV import | Reads a Summen- und Saldenliste (SKR03 or SKR04) in the browser and fills the exposures |
| Report | One-page PDF for a bank or tax advisor meeting |
| Advisor mode | Save many businesses and rank them by risk, with an alert threshold |
| English and German | Full UI in both languages |

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

**Risk model.** For each past month, the actual moves of rates, prices, FX and demand over the following 12 months are applied to the business, building up linearly through the year and respecting contract timing. Moves are scaled by today's volatility relative to volatility at the time (filtered historical simulation), with the past volatility floored at its median so calm periods such as zero rates do not inflate the scale. The bad year is the 5th percentile of that distribution, conditional on the current regime.

**Validation.** Since 2008, for every month the model forecast a 1-in-20 loss using only data available then and compared it with what happened. Results with sector-specific prices: bakery 8.0% and metal workshop 7.0% of months worse than forecast (both pass the Kupiec test at 5%); restaurant and importer 8.5% (narrow fail, p = 0.035) against a 5% target. Misses concentrate in 2020 to 2022. Volatility scaling cut the restaurant's miss rate from about 21%. The page reports this per business and translates it for the owner (read the bad year as roughly 1 in 12).

## Run it locally

```bash
pip install -r requirements.txt
echo "FRED_API_KEY=your_key" > .env          # free key from fred.stlouisfed.org
python pipeline.py --db macro.db --start 2000-01
python build_data.py --db macro.db --out docs/data.json
python build_page.py                           # writes docs/index.html
```

## Deploy (free)

1. Push this folder to a GitHub repository.
2. Settings → Secrets and variables → Actions → add `FRED_API_KEY`.
3. Settings → Pages → deploy from branch `main`, folder `/docs`.
4. Actions → "Refresh data and site" → Run workflow. It then runs twice a month by itself and opens an issue (which GitHub emails you) when the regime changes.

## Limits

- History starts in 2000, so the model has seen only a handful of big shocks; it underestimates anything larger than 2022.
- Overlapping 12-month windows mean the backtest's observations are not independent.
- Hedge costs are editable assumptions; the loan fixing cost uses the ECB 1-year rate when available.
- Not financial advice.
