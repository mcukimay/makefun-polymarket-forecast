#!/usr/bin/env python3
"""Polymarket "Next CEO of NVIDIA?" forecaster (make.fun polymarket-nvidia-ceo-forecast-01).

Approach: per-market empirical quantiles of h-step price displacements.
  - Median = last price (persistence; the 1-min price series is a near-martingale and
    every drift estimate we tried made the median worse on practice).
  - Spread = empirical quantiles [0.05, 0.25, 0.75, 0.95] of the h-step displacement
    distribution, centred on its median, taken from the recent history at anchor
    horizons h in [1, 5, 15, 60, 240] and log-linearly interpolated in h. A fixed
    sigma (the house baseline) is badly mis-calibrated per market: 1-min return sd
    varies ~150x across markets and the displacement distribution is a point mass at
    0 plus rare jumps, so normal quantiles of an sd are far too wide.
  - Two windows blended 90/10: the last 6h (adaptive to the current regime) and the
    last 24h (tail stability). A market with too few displacement samples at an
    anchor falls back to the pooled (cross-market, per-market-centred) quantiles.
  - Displacements are sampled every minute (STEP = 1): overlapping samples are
    correlated, but the denser tail sampling beats stride 5 on practice.
  - Recent-activity scaling: the spread is multiplied by a per-market factor from
    the last 30 minutes' realised 1-min volatility relative to the 6h window,
    clipped to [0.7, 1.6]. A market that just moved gets wider intervals, a quiet
    one narrower ones. The factor is the same for every horizon (a horizon-faded
    version scored worse).

Measured on the 6 practice cases against the real post-checkpoint prices from
Polymarket's public API (mean pinball loss, percentage points):
  house baseline 0.363589 | v1 entry 0.285642 | this entry 0.276209 (24% better
  than baseline, 3.3% better than v1; beats the baseline on all 6 practice cases
  and on both halves p00-p02 / p03-p05).
  Per case: p00 0.4486, p01 0.3129, p02 0.3899, p03 0.2734, p04 0.1939,
  p05 0.1057. Median absolute error 0.767503 (same as baseline: median is the
  last price). 90% interval coverage 0.8306 (baseline 0.9496; the misses are
  skewed high: 3.9% below q05, 10.8% above q95 — one-sided tail widening was
  tried and did not help).

Stdlib only, no network, deterministic. Reads $MAKEFUN_INPUT_DIR (else /input/),
writes $MAKEFUN_OUTPUT_DIR (else /output/), one <id>.out per <id>.in.
"""
import json
import math
import os

QUANTILES = [0.05, 0.25, 0.5, 0.75, 0.95]
ANCHORS = [1, 5, 15, 60, 240]
WIN_INNER = 360    # minutes of recent history for the adaptive window (6h)
WIN_OUTER = 1440   # minutes of recent history for the stability window (24h)
BLEND_INNER = 0.9  # weight of the inner window's quantiles
STEP = 1           # stride (minutes) when sampling displacements
MIN_SAMPLES = 25   # below this, a market uses the pooled quantiles at that anchor
VOL_RECENT = 30    # minutes of recent history for the activity scale
VOL_LO = 0.7       # activity scale is clipped to [VOL_LO, VOL_HI]
VOL_HI = 1.6


def price6(x):
    """Clamp to [0, 1] and round to 6 decimals (0, never -0)."""
    return round(min(1.0, max(0.0, x)), 6) + 0.0


def quantile(sorted_vals, tau):
    """Linear-interpolation empirical quantile of an already-sorted list."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    pos = tau * (n - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def displacements(prices, h, window):
    """Sorted h-step displacements p[i+h]-p[i] over the last `window` minutes."""
    p = prices[-window - 1:] if window < len(prices) else prices
    if len(p) <= h:
        return []
    return sorted(p[i + h] - p[i] for i in range(0, len(p) - h, STEP))


def centred_quantiles(samples):
    """Empirical quantiles of `samples` centred on their median (median -> 0)."""
    if not samples:
        return None
    med = quantile(samples, 0.5)
    qs = [quantile(samples, t) - med for t in QUANTILES]
    qs[2] = 0.0
    return qs


def pooled_quantiles(markets, h, window):
    """Quantiles of the pooled centred displacements across all markets in the case."""
    all_d = []
    for prices in markets:
        ds = displacements(prices, h, window)
        if ds:
            med = quantile(ds, 0.5)
            all_d.extend(v - med for v in ds)
    if not all_d:
        return [0.0] * 5
    all_d.sort()
    return [quantile(all_d, t) for t in QUANTILES]


def anchor_quantiles(prices, h):
    """Blended [q05, q25, 0, q75, q95] for one market at anchor horizon h.

    Returns None for a window whose sample count is below MIN_SAMPLES; the caller
    then falls back (inner -> outer's own quantiles, outer -> inner's own, both
    missing -> the case's pooled quantiles).
    """
    out = []
    for window in (WIN_INNER, WIN_OUTER):
        ds = displacements(prices, h, window)
        out.append(centred_quantiles(ds) if len(ds) >= MIN_SAMPLES else None)
    return out


def interp_quantiles(anchor_q, h):
    """Log-linear interpolation of each quantile between the anchor horizons."""
    hs = sorted(anchor_q)
    if h <= hs[0]:
        return list(anchor_q[hs[0]])
    if h >= hs[-1]:
        return list(anchor_q[hs[-1]])
    for i in range(len(hs) - 1):
        if hs[i] <= h <= hs[i + 1]:
            lh0, lh1 = math.log(hs[i]), math.log(hs[i + 1])
            t = (math.log(h) - lh0) / (lh1 - lh0)
            return [anchor_q[hs[i]][j] + t * (anchor_q[hs[i + 1]][j] - anchor_q[hs[i]][j])
                    for j in range(5)]
    return list(anchor_q[hs[-1]])


def blend_anchor(inner, outer):
    """Blend a market's own anchor quantiles with the pooled fallback.

    Returns the pooled quantiles when the market has none of its own,
    its own when there is no pooled fallback, else the 90/10 blend.
    """
    if inner is None and outer is None:
        return None
    if inner is None:
        return list(outer)
    if outer is None:
        return list(inner)
    return [BLEND_INNER * inner[j] + (1.0 - BLEND_INNER) * outer[j] for j in range(5)]


def activity_scale(prices):
    """Spread multiplier from the last VOL_RECENT minutes' realised 1-min
    volatility relative to the WIN_INNER window, clipped to [VOL_LO, VOL_HI].

    A market that just moved gets wider intervals; a quiet one gets narrower
    ones. Returns VOL_LO when the window shows no movement at all (or the
    history is too short to measure), 1.0 when there is nothing to compare.
    """
    def sd1(window):
        p = prices[-window - 1:] if window < len(prices) else prices
        if len(p) < 3:
            return 0.0
        ds = [p[i + 1] - p[i] for i in range(len(p) - 1)]
        mu = sum(ds) / len(ds)
        return math.sqrt(sum((d - mu) ** 2 for d in ds) / len(ds))
    recent = sd1(VOL_RECENT)
    window = sd1(WIN_INNER)
    if window <= 0.0:
        return VOL_LO
    if recent <= 0.0:
        return VOL_LO
    return min(VOL_HI, max(VOL_LO, math.sqrt(recent / window)))


def forecast_market(prices, pooled, horizon):
    """240 (horizon) rows of 5 quantiles for one market.

    The activity scale is applied to the offsets of the already-clamped row
    (not to the raw quantile offsets): for a low-priced market the raw lower
    tail often runs far below 0, and scaling the clamped offset floors q05 at
    x * (1 - scale) instead of letting it collapse to 0. On practice this
    ordering scores better than scaling the raw offsets.
    """
    if not prices:
        x = 0.5
    else:
        x = prices[-1]
    anchor_q = {}
    for h in ANCHORS:
        inner, outer = anchor_quantiles(prices, h)
        aq = blend_anchor(inner, outer)
        anchor_q[h] = aq if aq is not None else pooled[h]
    scale = activity_scale(prices)
    rows = []
    for h in range(1, horizon + 1):
        qs = interp_quantiles(anchor_q, h)
        row = [price6(x + qs[j]) for j in range(5)]
        for j in range(1, 5):
            if row[j] < row[j - 1]:
                row[j] = row[j - 1]
        med = row[2]
        row = [med + (row[0] - med) * scale, med + (row[1] - med) * scale,
               med, med + (row[3] - med) * scale, med + (row[4] - med) * scale]
        for j in range(1, 5):
            if row[j] < row[j - 1]:
                row[j] = row[j - 1]
        rows.append([price6(v) for v in row])
    return rows


def main():
    input_dir = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
    output_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(output_dir, exist_ok=True)
    for fname in sorted(os.listdir(input_dir)):
        if not fname.endswith(".in"):
            continue
        case_id = fname[:-3]
        with open(os.path.join(input_dir, fname), "r", encoding="utf-8") as f:
            case = json.load(f)
        horizon = int(case.get("horizon_minutes", 240))
        markets = case["markets"]
        prices_list = [m.get("prices") or [] for m in markets]
        # Pooled fallback quantiles per anchor: outer window for the 5/95 tails,
        # inner window for the 25/75 quantiles.
        pooled = {}
        for h in ANCHORS:
            qi = pooled_quantiles(prices_list, h, WIN_INNER)
            qo = pooled_quantiles(prices_list, h, WIN_OUTER)
            pooled[h] = [qo[0], qi[1], 0.0, qi[3], qo[4]]
        forecasts = []
        for m, prices in zip(markets, prices_list):
            forecasts.append({"id": m["id"], "q": forecast_market(prices, pooled, horizon)})
        with open(os.path.join(output_dir, case_id + ".out"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"forecasts": forecasts}, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
