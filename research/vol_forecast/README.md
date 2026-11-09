# Volatility forecast for the scaling step

**Question.** The page scales every past 12-month move by today's volatility over the volatility back then, and "today's volatility" is simply the last 12 months. Volatility reverts to its long-run level and tends to rise after large moves, and, unlike the 5% tail the [LightGBM challenger](../challenger/) failed to learn, every month is a training example. Does a real forecast of next year's volatility give a better bad year?

**Answer: no.** A LightGBM forecast is slightly more accurate on average, but used in the page's model it makes the bad year milder and beaten more often. The trailing rule stays.

## Setup

- **Forecasts** of each factor's volatility over the next 12 months, made every month on data known at the time (training rows: windows whose next 12 months were published 14 months later; at least 36 of them):
  - *trailing*: the page today, no forecast;
  - *linear*: log forward volatility on log trailing and log 36-month volatility (a HAR-style regression);
  - *LightGBM*: the same target from 38 inputs (every factor's trailing volatility, the phase, Euribor, inflation and the last published 12-month moves), settings fixed before the run.
- **The bad year**: the page's own model in the built page, in its strict form (no look-ahead), recomputed for the six examples with each forecast as today's volatility ("now"), or as both today's and back then ("both": each past move scaled by the forecast made at its own start). Same 213 months (2008-01 to 2025-09) and outcomes. The raw data come from the data log (`data/vintages.csv`), which rebuilds the published `docs/data.json` exactly.

## Results

Forecast accuracy, RMSE of log volatility over the next 12 months, average over the 16 factors relative to trailing: **LightGBM 0.983, linear 1.058** (per factor: `accuracy.csv`; LightGBM wins for inflation, food, the dollar, oil, output and plastics, loses for gas, paper, metal and wood).

The bad year, all six examples pooled (1,278 forecasts):

| Today's volatility | Bad year beaten (target 5%) | Passes Kupiec, of 6 | Quantile score (page = 1) | Average bad year |
|---|---|---|---|---|
| Trailing 12 months (the page) | 8.0% | 3 | 1.00 | −45% of profit |
| Linear forecast | 10.2% | 1 | 1.07 | −40% |
| Linear, also back then | 10.1% | 1 | 1.08 | −41% |
| LightGBM forecast | 9.9% | 1 | 1.07 | −40% |
| LightGBM, also back then | 9.8% | 1 | 1.07 | −40% |
| LightGBM, only where it is higher (exploratory) | 8.0% | 3 | 1.01 | −46% |

The last row was added after the first results, so treat it as exploratory: the forecast may only add caution, the same rule as the page's lower bound of 1 on the scale factor. It changes almost nothing.

## Why

A better average forecast is not a better tail. Forecasts pull volatility back toward its long-run level, and they do it hardest right after a shock, which is exactly when the next 12 months turned out bad (2008, 2011, 2021-22). The trailing rule is late, but in a crisis being late means staying cautious. And because the page never scales a past move below its own size (scale factor at least 1), a forecast can mostly take caution away, not add it.

## Run it

```bash
pip install -r research/challenger/requirements.txt
python research/vol_forecast/vol_forecast.py    # about 90 seconds; writes accuracy.csv and results.csv
```
