# polymarket-nvidia-ceo-forecast-01 — approach & practice numbers

## Approach

Per-market empirical quantiles of h-step price displacements, median = last price.

- Input per case: 24 markets (some newer ones with short histories), each 72h of
  1-minute Yes prices up to a checkpoint. Output: 240 rows x 5 quantiles
  [0.05, 0.25, 0.5, 0.75, 0.95] per market, non-decreasing, in [0,1].
- Displacement sd grows slower than sqrt(h) (OU-like, saturating, timescale
  ~85 min); 1-min returns are heavy-tailed (kurtosis 257) and mostly zero
  (step prices). A fixed sigma is wrong per market (1-min sd varies ~150x:
  0.00007 Hock Tan to 0.01076 Colette Kress).
- OLS trend slopes look mean-reverting (corr(1h trend, 240-min move) = -0.358,
  slope -0.47) but the L1-optimal drift is ~0, so drift only ever hurt the
  median. Normal/t tails on an sd hurt too (0.48-0.84). Empirical centred
  quantiles are the thing that works.
- Windows: 6h inner (90%) + 24h outer (10%), anchors [1,5,15,60,240], log-h
  interpolation, step 5 min, min 25 samples else pooled cross-market fallback.
  No tail widening (costs loss on practice), no drift, no shrinkage to normal.

## Practice numbers (mean pinball loss, percentage points)

Measured against real post-checkpoint prices from Polymarket's public API
(prices-history, fidelity=1, 1-min grid = last point at or before each minute,
carried forward). Harness reproduces the published baseline exactly (0.363589),
so the truth construction is correct.

| model | loss | cov |
| --- | --- | --- |
| house baseline (0.0025*sqrt(h)) | 0.363589 | 0.9496 |
| per-market sigma, normal (24h) | 0.684581 | — |
| displacement power-law scale | 0.599571 | — |
| displacement log-log interp (24h) | 0.580776 | 0.9685 |
| baseline sigma + drift a=0.47 | 0.368966 | — |
| empirical centred quantiles 24h | 0.332997 | 0.9000 |
| empirical centred quantiles 12h | 0.313306 | 0.8434 |
| blend 12h/24h 70/30 + pooled fallback | 0.313789 | 0.9120 |
| **final: blend 6h/24h 90/10 + pooled fallback** | **0.285642** | **0.8521** |
| v2: STEP 1 + recent-activity scale (30m, clip [0.7, 1.6]) | **0.276209** | **0.8306** |

Per-case final vs baseline: p00 0.471/0.536, p01 0.315/0.380, p02 0.399/0.408,
p03 0.289/0.369, p04 0.195/0.299, p05 0.114/0.238. Split-half: p00-p02
0.395 vs 0.442, p03-p05 0.198 vs 0.301. Median abs error 0.767503 (same as
baseline; median is the last price in both). Shock test: a +/-0.12 step jump on
the most volatile market in one case costs the 6h model ~0.02-0.04 more than
the 12h model on that case, while the 6h model saves ~0.016 per quiet case —
expected value favours the 6h window.

## Runtime per case

~0.08s per case on this VM (6 cases in 0.48s total), cap 8s. Deterministic
(byte-identical repeat runs). Output 180-247 KB per case (< 1 MiB cap).
