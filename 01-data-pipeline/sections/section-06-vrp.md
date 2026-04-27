# Section 06: VRP — Variance Risk Premium (`pipeline/vrp.py`)

## Overview

This section implements `pipeline/vrp.py`, which computes the daily Variance Risk Premium (VRP) as the difference between 30-day constant-maturity ATM implied volatility and realized volatility.

**Dependencies (must be complete before starting this section):**
- `section-04-iv-computation` — provides `computed_iv` column in the options chain DataFrame
- `section-05-realized-vol` — provides `rk_ann_vol` and `rk_daily_var` per trading day

---

## Files Created

- `pipeline/vrp.py` — implementation
- `tests/test_vrp.py` — 17 tests, all passing

## Deviations from Plan

- `compute_vrp()` returns a DataFrame indexed by date with **no `date` column** (dropped redundant column per code review; callers use `.reset_index()` if a flat column is needed).
- `extract_atm_iv()` extrapolation logic: when both available expiries bracket T_TARGET, the flag is `False` (correct); single-expiry or non-bracketing case sets `True`. Exact T == T_TARGET edge case (two entries in `upper`, none in `lower`) is treated as extrapolated per plan spec — this is practically never encountered with NSE weekly/monthly expiries.

---

## Tests First

**Test file: `tests/test_vrp.py`**

Write and make all of the following tests pass before considering the implementation complete. Use `pytest`. Run tests with `uv run pytest tests/test_vrp.py -v`.

```python
# Test: vrp_vol > 0 when IV (25%) > RK ann vol (18%)
# Test: vrp_vol < 0 when IV (18%) < RK ann vol (25%)
# Test: vrp_variance and vrp_vol have identical signs (always)
# Test: vrp_variance = 0 and vrp_vol = 0 when IV == RK ann vol exactly
# Test: ATM strike for NIFTY spot 24100 → strike 24100 (exact multiple of 50)
# Test: ATM strike for NIFTY spot 24125 → 24150 (tie-break rounds half-up)
# Test: ATM IV = call IV when put IV is NaN (single-side fallback)
# Test: ATM IV = put IV when call IV is NaN
# Test: ATM IV = NaN when both call and put IV are NaN
# Test: 30-day interpolated IV is between T1 and T2 IV values
# Test: iv_30d_is_extrapolated = True when only one expiry available
# Test: iv_30d_is_extrapolated = False when two bracketing expiries are present
# Test: daily ATM IV uses median, not mean, across snapshots
```

Test stubs and what each must verify:

```python
def test_vrp_positive_when_iv_gt_rv():
    """IV=25%, RV=18% → vrp_vol > 0, vrp_variance > 0."""

def test_vrp_negative_when_iv_lt_rv():
    """IV=18%, RV=25% → vrp_vol < 0, vrp_variance < 0."""

def test_vrp_zero_when_equal():
    """IV == RK ann vol exactly → both vrp_variance and vrp_vol equal 0."""

def test_vrp_unit_consistency():
    """vrp_variance and vrp_vol must always have the same sign.

    Try several (iv, rv) pairs (iv > rv, iv < rv, iv == rv) and confirm the signs agree.
    """

def test_atm_strike_selection_nifty():
    """NIFTY spot 24100 → ATM strike 24100 (exact multiple of 50, no rounding needed)."""

def test_atm_tie_break():
    """NIFTY spot 24125 is equidistant between 24100 and 24150 → rounds half-up to 24150."""

def test_atm_iv_call_nan():
    """Call IV is NaN, put IV valid (e.g. 0.20) → ATM IV = 0.20."""

def test_atm_iv_put_nan():
    """Put IV is NaN, call IV valid (e.g. 0.22) → ATM IV = 0.22."""

def test_atm_iv_both_nan():
    """Both call and put IV are NaN → ATM IV = NaN. Do not fabricate a value."""

def test_constant_maturity_interpolation():
    """30-day IV is linearly interpolated between two bracketing expiries.

    Given T1 = 21 days (IV1 = 0.18) and T2 = 42 days (IV2 = 0.22), the interpolated
    IV at exactly 30 days should be strictly between 0.18 and 0.22.
    """

def test_constant_maturity_extrapolation_flag():
    """When only one expiry is available, iv_30d_is_extrapolated must be True."""

def test_constant_maturity_no_extrapolation_flag():
    """When two bracketing expiries are present, iv_30d_is_extrapolated must be False."""

def test_daily_atm_iv_uses_median():
    """Daily representative ATM IV is the median across intraday snapshots, not the mean.

    Construct snapshots with a skewed distribution of IV_30d values. Confirm the returned
    daily value equals the median and differs from the mean.
    """
```

---

## Background and Concepts

### What is the VRP?

The Variance Risk Premium is the premium that option sellers receive for bearing variance uncertainty. It is defined as:

```
vrp_variance = iv_30d_decimal² − rk_ann_var
vrp_vol      = iv_30d_decimal  − rk_ann_vol
```

`vrp_variance` is the theoretically correct quantity (variance space); `vrp_vol` is more intuitive for visualization. Both are stored. A **positive VRP** means IV > RV, the typical regime where options are richly priced relative to what actually realized. A **negative VRP** indicates jump or crisis periods where realized vol spiked above implied.

### Annualization Convention — Critical Constraint

Both `iv_30d_decimal` and `rk_ann_var` must use **calendar-365** annualization. The pipeline uses:
- IV: `T = seconds / (365.25 × 86400)` (see `section-04-iv-computation`)
- RV: annualized by `× 365` (see `section-05-realized-vol`)

Mixing calendar-IV with trading-252-RV introduces a ~3% systematic gap that creates a persistent artificial VRP bias. Do not use 252 anywhere in this module.

---

## Implementation Details

### ATM Strike Selection

At each snapshot, the ATM strike is determined by rounding `underlying_value` to the nearest ATM increment:

- NIFTY: increment = 50
- BANKNIFTY: increment = 100
- FINNIFTY: increment = 50

These increments come from `cfg.atm_increments`, a dict keyed by symbol name.

**Tie-break rule (half-up):** When the spot falls exactly between two strikes (e.g., NIFTY at 24,125 is equidistant between 24,100 and 24,150), round up to 24,150. This is "round half-up", not Python's default "round half to even" (banker's rounding). Implement explicitly, do not rely on Python's built-in `round()`.

```python
def _nearest_strike(spot: float, increment: int) -> int:
    """Return nearest strike to spot, rounding half-up on ties.

    Uses math.floor((spot + increment / 2) / increment) * increment.
    Example: spot=24125, increment=50 → 24150 (not 24100).
    Do not use Python's built-in round() — it uses banker's rounding.
    """
```

### ATM IV Value Per Snapshot

For each snapshot (i.e., each unique `captured_at`):
1. Look up the ATM strike using `_nearest_strike` with the snapshot's `underlying_value`.
2. Find rows in `df_day` with `strike_price == atm_strike` at that `captured_at`.
3. Extract `computed_iv` for the CE and PE rows.
4. **ATM IV = average of call and put IV** if both are non-NaN.
5. If only one side has a valid IV, use that side.
6. If both are NaN, the snapshot's ATM IV is NaN. Do not fabricate.

### Constant-Maturity (30-Day) Interpolation

For each snapshot, gather the available expiries that have a non-NaN ATM IV. For each such expiry, the time-to-expiry `T_expiry = (expiry_close_ist - captured_at).total_seconds() / (365.25 × 86400)`.

Target: `T_target = 30 / 365.25` (30 calendar days).

Identify the two expiries that bracket `T_target`:
- `T1 < T_target ≤ T2`

Linear interpolation in time-to-expiry space:

```
IV_30d = IV1 + (IV2 − IV1) × (T_target − T1) / (T2 − T1)
```

**Extrapolation flag:** If fewer than two expiries are available (no bracketing pair exists), use the single available expiry's ATM IV as a proxy and set `iv_30d_is_extrapolated = True`. When two bracketing expiries are present, set `iv_30d_is_extrapolated = False`.

**Context note on the flag:** When only a short-dated expiry is available (e.g., only a 7-day expiry near a weekly rollover), the proxy will overstate true 30-day IV due to the upward-sloping term structure. Downstream consumers should filter or highlight rows where `iv_30d_is_extrapolated = True`.

### Daily Representative IV

After computing `IV_30d` at each snapshot timestamp, take the **daily median** as the representative daily ATM IV. The median is preferred over the mean because it is resistant to anomalous values at market open and close when spreads are wide and IV estimates are unreliable.

---

## Key Function Signatures

```python
def _nearest_strike(spot: float, increment: int) -> int:
    """Return nearest strike to spot, rounding half-up on ties."""


def extract_atm_iv(df_day: pd.DataFrame, cfg) -> pd.DataFrame:
    """Return time series of 30-day constant-maturity ATM IV for all snapshots in df_day.

    Parameters
    ----------
    df_day : pd.DataFrame
        Options chain DataFrame for one symbol/day, with `computed_iv` column present
        (output of add_computed_iv() from section-04-iv-computation). Must contain columns:
        captured_at, symbol, strike_price, option_type, underlying_value, expiry, computed_iv.
    cfg : Config
        Pipeline configuration (used for cfg.atm_increments, cfg.symbols).

    Returns
    -------
    pd.DataFrame
        Index: captured_at (IST-aware datetime).
        Columns:
          - iv_30d: float, 30-day constant-maturity ATM IV in decimal annualized form
          - iv_30d_is_extrapolated: bool, True if only one expiry was available
    """


def compute_vrp(daily_atm_iv: pd.Series, daily_rv: pd.Series) -> pd.DataFrame:
    """Join daily ATM IV and RK RV and compute vrp_variance and vrp_vol.

    Parameters
    ----------
    daily_atm_iv : pd.Series
        Index: date string or datetime. Values: daily median IV_30d in decimal form.
        (Produced by taking daily median of extract_atm_iv output.)
    daily_rv : pd.Series
        Index: date string or datetime. Values: rk_ann_vol (annualized realized vol, decimal).
        (Produced by compute_daily_rk() from section-05-realized-vol.)

    Returns
    -------
    pd.DataFrame
        Columns: date, symbol, iv_30d, rk_ann_vol, rk_ann_var, vrp_variance, vrp_vol.
        vrp_variance = iv_30d² − rk_ann_var
        vrp_vol      = iv_30d  − rk_ann_vol
        NaN rows where either input is missing on a given date.
    """
```

---

## Implementation Notes

1. **Symbol lookup for ATM increment:** `df_day` contains a `symbol` column. Read the symbol from `df_day['symbol'].iloc[0]` to look up `cfg.atm_increments[symbol]`.

2. **Expiry column type:** After ingestion (section-03), `expiry` is an IST-aware datetime at 15:30 on the expiry date. Use it directly for time-to-expiry calculations.

3. **No fabrication on NaN:** If a snapshot has no valid ATM IV on either side, the row's `iv_30d` must be NaN. Do not forward-fill, interpolate, or substitute a prior snapshot's value within `extract_atm_iv`.

4. **`compute_vrp` uses variance, not vol, for RV input:** The function receives `rk_ann_vol` (not `rk_ann_var`). Compute `rk_ann_var = rk_ann_vol²` internally before differencing.

5. **Handling NaN in VRP computation:** Use `pd.Series.align()` or a DataFrame join on date index before differencing. Where either `iv_30d` or `rk_ann_vol` is NaN for a date, the VRP for that date should be NaN.

6. **Output CSV location:** Callers in `run_pipeline.py` (section-10) will pass the output of `compute_vrp` to `writer.append_to_csv()` targeting `outputs/{SYMBOL}_vrp.csv`. This module does not write files directly.

---

## Dependency Reference (Do Not Re-implement)

- `Config` dataclass, including `cfg.atm_increments` dict — from `pipeline/config.py` (section-02)
- `add_computed_iv(df, rate, cfg)` producing `computed_iv` column — from `pipeline/iv.py` (section-04)
- `compute_daily_rk(df, cfg, prev_close)` returning `rk_ann_vol` — from `pipeline/realized_vol.py` (section-05)
- `compute_rolling_rv(daily_rk_var, windows, ann_factor)` — from `pipeline/realized_vol.py` (section-05)
