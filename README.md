# polymarket-nvidia-ceo-forecast-01

Forecast Polymarket's "Next CEO of NVIDIA?" Yes prices minute by minute, 4 hours
ahead. Entry for the make.fun bounty (Python 3.13, stdlib only, no network).

## How to run

```sh
MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

Reads every `<id>.in` in the input dir, writes `<id>.out` per case:
`{"forecasts":[{"id": <conditionId>, "q": [[5 quantiles] x 240 rows]}]}` for
quantiles [0.05, 0.25, 0.5, 0.75, 0.95].

## Approach

- **Median = last price.** The 1-minute series is a near-martingale: every drift
  estimate tried (OLS trend, mean reversion, MA deviation) made the median worse
  on practice. L1-optimal drift is ~0.
- **Spread = per-market empirical quantiles of h-step displacements**, centred on
  their median, at anchor horizons [1, 5, 15, 60, 240] min, log-linearly
  interpolated in h. A fixed sigma (house baseline) is badly mis-calibrated:
  1-min return sd varies ~150x across markets, and the displacement distribution
  is a point mass at 0 plus rare jumps, so normal quantiles of an sd are far too
  wide (early per-market-sigma models scored 0.48–0.84 vs baseline 0.36).
- **Two windows blended 90/10**: last 6h (adaptive to the current regime) and
  last 24h (tail stability). Markets with <25 displacement samples at an anchor
  fall back to pooled cross-market quantiles (centred per market) — this fixed
  the short-history markets (new candidates) that otherwise got zero-width
  intervals.
- No drift, no tail widening, no normal/t tails: all made practice loss worse.

## Measured practice loss

Scored against the real post-checkpoint prices fetched from Polymarket's public
API (`/prices-history`, 1-min grid exactly as the rules describe), mean pinball
loss in percentage points over all 6 practice cases / markets / minutes / quantiles:

| model | loss | median abs err | 90% coverage |
| --- | --- | --- | --- |
| house baseline | 0.363589 | 0.767503 | 0.9496 |
| **this entry** | **0.285642** | 0.767503 | 0.8521 |

Beats the baseline on all 6 practice cases (p00 0.471 vs 0.536, p01 0.315 vs
0.380, p02 0.399 vs 0.408, p03 0.289 vs 0.369, p04 0.195 vs 0.299, p05 0.114 vs
0.238) and on both halves p00–p02 / p03–p05, so the edge is not one lucky case.
The median error equals the baseline's because the median is the last price in
both; the entire gain is the per-market empirical spread.

## Verification

- Parses clean; runs all 6 practice cases in ~0.48s total (~0.08s per case,
  cap is 8s). Deterministic: two runs produce byte-identical outputs.
- Outputs validated: market ids and order match the input, 240 rows x 5
  quantiles, non-decreasing rows, values in [0,1], files 180–247 KB (< 1 MiB).
