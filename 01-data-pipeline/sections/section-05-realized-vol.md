# section-05-realized-vol: Realized Volatility Module

## Overview

This section implements `pipeline/realized_vol.py`, which computes microstructure-noise-robust daily realized volatility from the intraday `underlying_value` series in the options chain DataFrame. The module produces per-day realized variance estimates and rolling annualized volatility that feed directly into the Variance Risk Premium (VRP) module (section-06-vrp).

**Depends on:** section-01-project-setup, section-02-config, section-03-ingestion
**Blocks:** section-06-vrp, section-10-pipeline-orchestration

---

## File to Create

`pipeline/realized_vol.py`

---

## Tests First

File: `tests/test_realized_vol.py`

Write and pass all of the following tests before considering the implementation complete. Tests are listed with the exact behavioral contract they verify.

```python
# tests/test_realized_vol.py

import numpy as np
import pandas as pd
import pytest
from pipeline.realized_vol import (
    parzen_weights,
    optimal_bandwidth,
    realized_kernel,
    compute_daily_rk,
    compute_rolling_rv,
)


def test_parzen_weights_first_is_one():
    """parzen_weights(H)[0] == 1.0 (weight at lag 0 is always 1)."""
    ...


def test_parzen_weights_last_approaches_zero():
    """parzen_weights(H)[H] is close to 0 (monotone decreasing toward 1.0)."""
    ...


def test_rk_positive():
    """realized_kernel(r) >= 0 for any valid log-return array."""
    ...


def test_rk_h_zero_equals_rv():
    """With H=0, realized_kernel(r, H=0) == np.dot(r, r) (reduces to simple RV)."""
    ...


def test_rk_deterministic():
    """Hand-computed RK for a small (5-element) input vector matches formula output to 1e-10.

    Manually compute RK = k(0)*gamma_0 + 2*sum_{h=1}^{H} k(h/(H+1))*gamma_h
    for a known r and chosen H, then assert equality.
    """
    ...


def test_rk_gbm():
    """On a GBM path with known sigma, recovered RK is within 5% of sigma^2 * dt.

    Use multiple random seeds and assert that the mean error is within tolerance.
    """
    ...


def test_optimal_bandwidth_range():
    """For n=75 returns, optimal_bandwidth returns H in [1, 20]."""
    ...


def test_overnight_increases_variance():
    """Non-zero overnight return strictly increases total rk_daily_var vs. intraday only."""
    ...


def test_rolling_rv_in_variance_space():
    """Rolling RV is computed as sqrt(mean(rk_daily_var, N) * 365), NOT mean(rk_ann_vol).

    Verify: rk_5d_ann == sqrt(rolling_mean(rk_daily_var, 5) * 365)
    """
    ...


def test_rk_ann_vol_uses_365():
    """rk_ann_vol annualizes by sqrt(365), not sqrt(252)."""
    ...


def test_forward_fill_isolation():
    """Forward-filled underlying_value does NOT appear in the RK input series.

    A DataFrame where some underlying_value entries were forward-filled should
    yield no synthetic zero log-returns in the 5-min resampled spot series used
    by compute_daily_rk.
    """
    ...
```

Relevant conftest fixtures (already defined in section-01):
- `synthetic_spot_5m` — `pd.Series` of 75 log-normal 5-min prices at known σ, fixed random seed
- `tiny_day_df` — minimal 3-snapshot options chain DataFrame with `underlying_value` column populated

---

## Implementation Details

### Background and Motivation

The `underlying_value` column in the NSE options chain CSV contains the index spot price at each ~1-minute snapshot. Realized volatility (RV) is the standard backward-looking volatility estimate and is paired with forward-looking ATM implied volatility (from section-04-iv-computation) to compute the Variance Risk Premium in section-06-vrp.

Simple 5-minute squared returns (classical realized variance) are contaminated by microstructure noise — bid-ask bounce, discreteness, and reporting latency. The Barndorff-Nielsen, Hansen, Lunde & Shephard (BNHLS 2008/2009) realized kernel with Parzen weights is the industry standard that eliminates this noise while guaranteeing a non-negative variance estimate.

### Spot Price Extraction

The DataFrame received from section-03-ingestion has `underlying_value` for each options chain row. The realized vol computation needs a clean time series of the index level, not the full options chain.

**Extraction steps:**

1. Drop duplicates on `captured_at` (keep the first; all rows at the same timestamp have the same `underlying_value`)
2. Set `captured_at` as index, select the `underlying_value` column
3. Resample to 5-minute bars: `spot.resample('5min').last()`
4. Clip to market hours `between_time('09:15', '15:30')` and `dropna()`
5. Compute log returns: `log_rets = np.log(spot_5m).diff().dropna()`

This yields approximately n = 75 returns per trading day (09:15–15:30 at 5-min resolution).

**Critical:** Use the raw `underlying_value` from the DataFrame as loaded by `load_day()`, before any forward-fill operation. The `load_day()` function in section-03-ingestion forward-fills `underlying_value` only for the IV computation path. The RK spot extraction must not use the forward-filled version — forward-filled values create synthetic zero log-returns that contaminate the RK estimate. The `compute_daily_rk` function receives the DataFrame from the pipeline and must extract `underlying_value` before any fill is applied.

### Parzen Weights

The Parzen kernel function is:

```
k(x) = 1 - 6x²(1 - x)   for x ≤ 0.5
k(x) = 2(1 - x)³         for x > 0.5
```

`parzen_weights(H)` returns an array of shape `(H+1,)` containing `k(h/(H+1))` for `h = 0, 1, ..., H`.

Properties to preserve in implementation:
- `k(0) = 1` (weight at lag 0 is exactly 1.0)
- `k(x)` is strictly decreasing from 1 to 0 over `[0, 1]`
- `k(1)` approaches 0 (the last weight should be near zero but not exactly zero unless H is large)

```python
def parzen_weights(H: int) -> np.ndarray:
    """Return Parzen kernel weights k(0/(H+1)) ... k(H/(H+1)), shape (H+1,).

    h=0 gives weight 1.0. Weights decrease monotonically.
    """
```

### Realized Kernel Formula

```
RK = k(0)·γ₀ + 2·Σ_{h=1}^{H} k(h/(H+1))·γ_h
```

where `γ_h = Σ_{j > h} r_j · r_{j−h}` is the h-th sample autocovariance (NOT divided by n).

Implementation note:
- `γ_0 = Σ r_j²` = `np.dot(r, r)` (this is the simple realized variance when H=0)
- For `H=0`: `RK = γ₀ = np.dot(r, r)` exactly — this is a key invariant to test
- For `H > 0`: compute each autocovariance using `np.dot(r[h:], r[:-h])` for `h >= 1`
- The Parzen kernel guarantees `RK ≥ 0` for all input vectors

```python
def realized_kernel(log_rets: np.ndarray, H: int | None = None) -> float:
    """Compute BNHLS realized kernel for one day's intraday log-returns.

    Returns non-negative variance estimate. H=None triggers adaptive bandwidth via
    optimal_bandwidth(). H=0 returns simple realized variance (sum of squared returns).
    """
```

### Optimal Bandwidth Selection

Following BNHLS (2009) §3, the adaptive bandwidth is:

```
H* = ceil(3.5134 × ξ^(4/5) × n^(3/5))
```

where:
- `n` = number of log-returns
- `ξ = ω / IQ^(1/4)` (noise-to-signal ratio)
- `ω²` = noise variance = `max(-0.5 × mean(r_j × r_{j+1}), ε)` where `ε` is a small positive floor (e.g., 1e-10)
- `IQ` = integrated quarticity = `(n/3) × Σ r_j⁴`

**Conservative fallback:** If the formula produces `H < 1` or `H > n/2` (numerical issues), use `H = ceil(0.5 × sqrt(n))`.

```python
def optimal_bandwidth(log_rets: np.ndarray) -> int:
    """Return BNHLS optimal bandwidth H* for the Parzen kernel.

    For n=75 typical NSE intraday returns, H should fall in [1, 20].
    Falls back to ceil(0.5 * sqrt(n)) on numerical edge cases.
    """
```

### Overnight Return

The realized kernel captures intraday variance only. To make RV comparable to IV (which prices full 24-hour variance including overnight risk), add the squared overnight log-return:

```
rk_full_var = rk_intraday + (log(today_open / yesterday_close))²
```

where `today_open` is the first available spot price on the current day and `yesterday_close` is the last `underlying_value` from the previous trading day.

- `prev_close` is passed in as a parameter to `compute_daily_rk`. If `None` (first day in the dataset, no prior data), skip the overnight addition.
- The `today_open` is the first value from the resampled 5-min spot series (the 09:15 bar).

### Daily Output

`compute_daily_rk` returns a dict with the following keys:

| Key | Definition |
|-----|-----------|
| `rk_daily_var` | `rk_intraday + overnight²` (or just `rk_intraday` if no prev_close) |
| `rk_daily_vol` | `sqrt(rk_daily_var)` — daily standard deviation |
| `rk_ann_vol` | `rk_daily_vol × sqrt(365)` — annualized volatility, calendar-365 |
| `bandwidth_H` | The H value used (for diagnostic logging) |
| `n_bars` | Number of 5-min bars used (for diagnostic logging) |

```python
def compute_daily_rk(df: pd.DataFrame, cfg, prev_close: float | None) -> dict:
    """Extract spot from df, resample to 5min, apply realized kernel + overnight return.

    df must contain raw (non-forward-filled) underlying_value entries.
    cfg provides resample_freq ('5min'), market_open ('09:15'), market_close ('15:30'),
    and ann_factor (365).
    prev_close is the last underlying_value from the previous trading day; None means
    no overnight return is added.

    Returns dict: {rk_daily_var, rk_daily_vol, rk_ann_vol, bandwidth_H, n_bars}
    """
```

### Rolling RV

Rolling RV must be computed **in variance space** (not vol space) to avoid Jensen's inequality bias:

```
rk_Nd_ann = sqrt(rolling_mean(rk_daily_var, N) × 365)
```

**Do NOT** compute `rolling_mean(rk_ann_vol, N)` — averaging vols directly is systematically low by a few percent due to Jensen's inequality.

```python
def compute_rolling_rv(daily_rk_var: pd.Series, windows: list[int], ann_factor: int) -> pd.DataFrame:
    """Rolling-mean of daily variance, then annualize. Input series is variance (not vol).

    windows: list of integers, e.g. [5, 10, 21] (trading days)
    ann_factor: 365 (calendar-day convention, from Config.ann_factor)

    Returns DataFrame with columns: rk_5d_ann, rk_10d_ann, rk_21d_ann
    (or generalized: rk_{N}d_ann for each N in windows).
    Minimum periods for each window = the window size itself (NaN until enough data).
    """
```

### Annualization Convention

Calendar-365 is used throughout. Specifically:
- `rk_ann_vol = rk_daily_vol × sqrt(365)` — NOT `sqrt(252)`
- Rolling: `rk_Nd_ann = sqrt(mean(rk_daily_var, N) × 365)`
- This matches the IV annualization convention in section-04-iv-computation which uses `T = seconds / (365.25 × 86400)`

Mixing calendar-365 IV with trading-252 RV produces a ~3% systematic gap in the VRP that would create a persistent positive VRP bias.

### Integration with Pipeline Orchestration (section-10)

In `run_pipeline.py`, this module is called as:

```python
rv_result = compute_daily_rk(raw_df, cfg, prev_close=prev_close_price)
```

where `raw_df` is the DataFrame returned by `load_day()` **before** `add_computed_iv()` is called (to avoid using the forward-filled `underlying_value`). The `prev_close` is tracked across the date loop for each symbol.

Rolling RV is computed once per symbol after all daily RK values have been accumulated:

```python
rv_series = pd.Series({date: result['rk_daily_var'] for date, result in daily_results.items()})
rolling_df = compute_rolling_rv(rv_series, cfg.rolling_windows, cfg.ann_factor)
```

---

## Annualization Summary (to avoid errors)

| Quantity | Formula | Factor |
|---------|---------|--------|
| `rk_ann_vol` | `sqrt(rk_daily_var) × sqrt(365)` | 365 |
| `rk_5d_ann` | `sqrt(mean(rk_daily_var, 5) × 365)` | 365 |
| `rk_10d_ann` | `sqrt(mean(rk_daily_var, 10) × 365)` | 365 |
| `rk_21d_ann` | `sqrt(mean(rk_daily_var, 21) × 365)` | 365 |

Never use `sqrt(252)` anywhere in this module.
