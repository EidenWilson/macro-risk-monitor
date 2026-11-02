# Challenger: LightGBM against the historical simulation

**Question.** Does a machine-learning model forecast the 1-in-20 bad year better than the page's filtered historical simulation?

**Answer: no.** Over the same 213 months (January 2008 to September 2025) and the six example businesses, LightGBM's bad year was beaten in 20.6% of months against a 5% target, and its quantile score was about 30% worse. The page model stays.

| Model | Bad year beaten (target 5%) | Passes Kupiec, of 6 | Quantile score (page model = 1) | Average bad year |
|---|---|---|---|---|
| Page model (filtered historical simulation) | 7.9% | 3 | 1.00 | −45% of profit |
| Plain history (no phase, no volatility scaling) | 9.9% | 0 | 1.12 | −38% |
| LightGBM, 38 inputs | 20.6% | 0 | 1.30 | −26% |
| LightGBM, 8 inputs | 23.7% | 0 | 1.47 | −25% |
| Linear quantile regression, 8 inputs | 27.1% | 0 | 2.52 | −29% |

All six businesses pooled, 1,278 forecasts per model. Quantile score = average pinball loss at 5%, lower is better. Per business: `results.csv`.

## How the contest was set up

- **Same outcomes.** The realized profit change of every 12-month window and the page model's forecasts come from the built page itself (`extract.py`), so there is no second copy of the business model in Python.
- **No look-ahead for anyone.** Inputs at each date use only published data (publication lags from `macro_config.py`); a window's outcome is used for training only 14 months after it starts. The page model is scored in a strict form too: the same publication rule for its past windows, and its volatility floor computed from data known at the time. That changed 1 of 1,278 misses (the importer, 22 to 21), so the page's own backtest stands.
- **The challenger.** LightGBM with quantile loss at 0.05, one model per business, refitted every month on all windows known by then (expanding window). 38 inputs: business-cycle phase, Euribor, inflation, and the trailing volatility and last published 12-month move of all 16 risk factors. Settings fixed before the first run: 4 leaves, depth 2, at least 15 windows per leaf, 150 trees, learning rate 0.05.
- **Sensitivity** (`grid.py`, `grid.csv`). Eight other settings (4 or 8 leaves, 5 or 20 windows per leaf, 150 or 500 trees): beaten in 18.7% to 26.4% of months, quantile score 21% to 53% worse. No setting rescues it.

## Why it lost

- **Too few bad years.** The 287 windows overlap, so they hold about 24 independent years and three or four real shocks. At 5%, each fit sees 3 to 14 bad outcomes.
- **It learns coincidences.** In the last restaurant fit, the strongest inputs were past moves in the dollar, timber and metal prices; timber and metal play no part in a restaurant's costs, they moved before earlier crises. In sample it was beaten 7.3% of the time, out of sample 14.6%.
- **The page model borrows strength.** Every past window is a whole scenario run through the business's own costs; only the phase and today's volatility are conditioned on.

**How sure.** The gap in quantile score alone is not significant (Diebold-Mariano with Newey-West errors over 12 lags, p = 0.14): with overlapping 12-month outcomes, 213 months hold about 18 independent years. The calibration failure is not in doubt: every LightGBM version fails the Kupiec test for every business.

**What it changes.** Nothing on the page. Where the page model fails (the importer's dollar costs, the print shop's paper prices) every model fails, so those need better exposures and longer sector price histories, not a different learner. A better job for machine learning here is forecasting next year's volatility, which learns from every month rather than only the bad ones, for the scaling step.

## Run it

```bash
pip install -r requirements-dev.txt -c constraints.txt && pip install -r research/challenger/requirements.txt
python build_page.py
python research/challenger/extract.py      # page_model.json (included: data through September 2026)
python research/challenger/challenger.py   # results.csv and the summary, about a minute
python research/challenger/grid.py         # grid.csv, a few minutes
```
