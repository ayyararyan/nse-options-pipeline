# Section 08: Liquidity Metrics (`pipeline/liquidity.py`)

## Overview

This section implements the Liquidity Metrics module for the NSE options pipeline. The module computes per-contract and aggregate liquidity measures — relative bid-ask spread, depth, open interest, volume, and put-call OI ratio — from the cleaned options chain DataFrame produced by the ingestion module.

**File to create:** `pipeline/liquidity.py`

**Test file to create:** `tests/test_liquidity.py`

**Dependencies:** section-01 (project setup), section-02 (Config), section-03 (Ingestion — cleaned DataFrame)

---

## Tests First (`tests/test_liquidity.py`)

Write and pass these tests before finalizing the implementation. Use the shared `tiny_day_df` fixture from `conftest.py` (see section-01), which provides a minimal 3-snapshot options chain with 2 strikes, 2 expiries, CE + PE, and realistic column values.

### Test Stubs

```python
# tests/test_liquidity.py

import pandas as pd
import numpy as np
import pytest
from pipeline.liquidity import compute_liquidity


# ── Relative Spread ───────────────────────────────────────────────────────────

def test_relative_spread_valid_quotes():
    """
    relative_spread = (ask_price - bid_price) / mid_price for valid quotes.
    With bid=99, ask=101: mid=100, relative_spread=0.02 (2%).
    """
    ...


def test_relative_spread_nan_when_mid_zero():
    """
    relative_spread must be NaN when mid_price <= 0.
    Covers cases where bid=0 and ask=0 (zero quotes).
    """
    ...


# ── Put-Call OI Ratio ─────────────────────────────────────────────────────────

def test_put_call_oi_ratio_nan_when_ce_oi_zero():
    """
    put_call_oi_ratio must be NaN (or inf) when the sum of CE open_interest
    across all contracts is zero. Must not raise ZeroDivisionError.
    """
    ...


# ── ATM Spread Computation ────────────────────────────────────────────────────

def test_atm_spread_mean_uses_two_percent_band():
    """
    atm_spread_mean must use only strikes whose price is within 2% of
    underlying_value (NOT the nearest single strike as used in section-06 VRP).
    With spot=24000: strikes 23520 to 24480 are ATM-band; strikes outside are excluded.
    Verify that a strike at 25000 (> 2% away) does NOT contribute to atm_spread_mean.
    """
    ...


# ── Chain Spread Exclusions ───────────────────────────────────────────────────

def test_chain_spread_excludes_zero_depth_rows():
    """
    chain_spread_mean and chain_spread_p50 must exclude rows where
    depth = bid_qty + ask_qty == 0. Zero-depth rows have no meaningful spread
    and would bias the aggregate.
    """
    ...


# ── Output Columns ────────────────────────────────────────────────────────────

def test_all_output_columns_present(tiny_day_df, default_config):
    """
    compute_liquidity() must return a DataFrame containing all expected columns:
    date, symbol, expiry, atm_spread_mean, chain_spread_mean, chain_spread_p50,
    total_oi, total_volume, atm_oi, put_call_oi_ratio.
    No column should be missing for any valid symbol-day input.
    """
    ...
```

### Key Assertions by Test

| Test | Critical Assertion |
|------|--------------------|
| `test_relative_spread_valid_quotes` | `relative_spread == (ask - bid) / ((bid + ask) / 2)` |
| `test_relative_spread_nan_when_mid_zero` | `pd.isna(relative_spread)` when `mid_price <= 0` |
| `test_put_call_oi_ratio_nan_when_ce_oi_zero` | `pd.isna(ratio)` or `np.isinf(ratio)` — no exception |
| `test_atm_spread_mean_uses_two_percent_band` | Strike at `spot * 1.05` excluded; strike at `spot * 1.01` included |
| `test_chain_spread_excludes_zero_depth_rows` | Row with `bid_qty=0, ask_qty=0` absent from spread computation |
| `test_all_output_columns_present` | All required columns present with no `KeyError` |

---

## Implementation Details

### File Location

`pipeline/liquidity.py`

### Per-Contract Metrics (per snapshot row)

Compute these columns on each row of the cleaned options chain DataFrame:

| Column | Formula | Edge Cases |
|--------|---------|------------|
| `mid_price` | `(bid_price + ask_price) / 2` | Carry through from ingestion if already present |
| `relative_spread` | `(ask_price - bid_price) / mid_price` | NaN when `mid_price <= 0` |
| `depth` | `bid_qty + ask_qty` | Zero depth rows are excluded from spread aggregates |

These per-contract, per-snapshot values are intermediate; they feed the daily aggregates below.

### ATM Definition for Liquidity (2% Band — NOT Nearest Strike)

**Important distinction:** The ATM definition here is a range filter, not the single-nearest-strike lookup used in section-06 VRP.

A strike is considered ATM-band for liquidity purposes if:

```
abs(strike_price - underlying_value) / underlying_value <= 0.02
```

For NIFTY at spot 24,000: strikes in [23,520, 24,480] are ATM-band; a strike at 25,000 is not included. Do not use `cfg.atm_increments` for this calculation.

### Daily Aggregates (per symbol per date)

| Metric | Definition |
|--------|------------|
| `atm_spread_mean` | Mean `relative_spread` for rows where strike is within 2% of `underlying_value` |
| `chain_spread_mean` | Mean `relative_spread` across all rows where `depth > 0` |
| `chain_spread_p50` | Median `relative_spread` across all rows where `depth > 0` (robust to illiquid tails) |
| `total_oi` | Sum of `open_interest` across all contracts |
| `total_volume` | Sum of `total_traded_volume` across all contracts |
| `atm_oi` | Sum of `open_interest` for ATM-band strikes only (same 2% filter as above) |
| `put_call_oi_ratio` | Sum of PE `open_interest` / Sum of CE `open_interest`; NaN when CE OI = 0 |

For `atm_spread_mean`, compute the mean over all snapshots and ATM-band strikes together (i.e., flatten to one average per day). If no ATM-band rows exist, return NaN.

### Function Signature

```python
def compute_liquidity(df_day: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Return daily liquidity aggregates for one symbol-day.

    Parameters
    ----------
    df_day : pd.DataFrame
        Cleaned options chain for one symbol-day. Must contain columns:
        captured_at, symbol, expiry, strike_price, option_type,
        bid_price, ask_price, bid_qty, ask_qty,
        open_interest, total_traded_volume, underlying_value.

    cfg : Config
        Pipeline configuration. Used for symbol validation; ATM band uses
        a hardcoded 2% threshold (not cfg.atm_increments).

    Returns
    -------
    pd.DataFrame
        One row per (date, expiry) with symbol-level aggregates included.
        Columns: date, symbol, expiry, atm_spread_mean, chain_spread_mean,
        chain_spread_p50, total_oi, total_volume, atm_oi, put_call_oi_ratio.
    """
    ...
```

### Output Schema

Columns written to `outputs/{SYMBOL}_liquidity.csv`:

| Column | Type | Description |
|--------|------|-------------|
| `date` | string `YYYY-MM-DD` | Trading date |
| `symbol` | string | `NIFTY`, `BANKNIFTY`, or `FINNIFTY` |
| `expiry` | string | Contract expiry date |
| `atm_spread_mean` | float | Mean relative spread for ATM-band strikes |
| `chain_spread_mean` | float | Mean relative spread (liquid strikes only) |
| `chain_spread_p50` | float | Median relative spread (liquid strikes only) |
| `total_oi` | float | Total open interest across all contracts |
| `total_volume` | float | Total traded volume across all contracts |
| `atm_oi` | float | Open interest for ATM-band strikes |
| `put_call_oi_ratio` | float | Sum PE OI / Sum CE OI |

Rows are appended to this file by the writer module (section-09) using `(date, symbol)` as the deduplication key. The file is created on first write with header.

### Edge Cases

| Situation | Correct Behavior |
|-----------|-----------------|
| `mid_price <= 0` | `relative_spread = NaN`; row excluded from spread aggregates |
| `depth = 0` at a row | Row excluded from `chain_spread_mean` and `chain_spread_p50` |
| No ATM-band strikes in day | `atm_spread_mean = NaN`, `atm_oi = NaN` |
| CE `open_interest` sum = 0 | `put_call_oi_ratio = NaN` (do not raise; use `pd.NA` or `np.nan`) |
| Rows with `ask_price < bid_price` (crossed quotes) | `relative_spread` will be negative; do not filter — preserve and let downstream consumers decide |

---

## Dependencies on Other Sections

- **section-01 (Project Setup):** Provides `conftest.py` with the `tiny_day_df` fixture and `default_config` fixture used in tests.
- **section-02 (Config):** `compute_liquidity()` accepts a `Config` argument. The 2% ATM band threshold is not in `Config`; it is a hardcoded domain constant.
- **section-03 (Ingestion):** `compute_liquidity()` operates on the DataFrame returned by `load_day()`. Required columns are guaranteed by the ingestion cleaning step. Do not add duplicate cleaning logic here.

This section does not depend on section-04 (IV), section-05 (Realized Vol), section-06 (VRP), or section-07 (OFI). It can be implemented in parallel with those sections after section-03 is complete.
