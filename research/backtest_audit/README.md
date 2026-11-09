# Backtest audit: the importer, the print shop, and the test itself

**Question.** The online importer (10.3% of months beaten) and the print shop (16.0%) fail the backtest. Why, and what would fix it?

## 1. The test overstates the failures

The backtest has 213 monthly forecasts of the next 12 months. Neighbouring forecasts share 11 of their 12 months, so one bad year shows up as a run of up to 12 misses, and the 213 forecasts hold only about 18 independent years. Kupiec's test assumes independent forecasts, so on overlapping windows it rejects far too often. Tested with Newey-West errors over the 11 lags the overlap creates, the importer's p-value goes from 0.002 to 0.26 and the print shop's from 0.000 to 0.07. With about 18 independent years, the test can catch a badly wrong model but cannot tell 5% from 8%. (The food truck shows the limit of that correction: with only 3 misses, the estimated error is itself unreliable.)

## 2. The misses were moves beyond the history in the pool

| Example | Misses | Episodes | Months whose key move lay beyond every past move in the forecast's pool |
|---|---|---|---|
| Online importer (dollar) | 22 | 2021-23 energy and dollar (11), 2014-15 dollar (7), 2008-09 (4) | 15 of 22 |
| Print shop (paper prices) | 34 | 2021-23 (13), 2008-09 crisis (8), 2017-19 (8), Covid (5) | 13 of 34 |

In 2014 the euro fell up to 22% against the dollar while the worst move in the pool (same phase, scaled to that day's volatility) was 13%; in 2021 paper prices rose 22% to 32% against a pool maximum of 17%. No reweighting of the past catches a move the pool does not hold.

## 3. One alternative, set before looking: all past months instead of the same phase

The page draws scenarios only from past windows in today's business-cycle phase (49 in Stagflation). The page already offers all months as a tab. Scored on all six examples:

| Example | Phase pool (the page) | All months | Quantile score, all months (phase = 1) |
|---|---|---|---|
| Restaurant | 5.6% | 5.6% | 0.99 |
| Bakery | 7.5% | 7.5% | 1.05 |
| Food truck | 1.4% | 1.4% | 1.03 |
| Online importer | 10.3% | 8.5% | 0.84 |
| Metal workshop | 7.0% | 4.7% | 0.61 |
| Print shop | 16.0% | 8.9% | 0.77 |
| **All six** | **8.0%** | **6.1%** | **0.88** |

Fewer misses and a 12% better quantile score on average, the same lesson as the [challenger study](../challenger/): with few bad years, conditioning on less is more robust. But the gain is not statistically significant (Diebold-Mariano, Newey-West over 12 lags, p = 0.22), and it changes today's figures: restaurant 64% to 58% of profit, bakery 77% to 60%, food truck 27% to 37%, importer 35% to 44%, metal workshop 49% to 63%, print shop 54% to 71%.

## Run it

```bash
python research/backtest_audit/audit.py    # writes results.csv
```
