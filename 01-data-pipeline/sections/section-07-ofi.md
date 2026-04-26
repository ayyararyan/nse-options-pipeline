# Section 07: OFI — Order Flow Imbalance (`pipeline/ofi.py`)

## Overview

This section implements the Order Flow Imbalance (OFI) module for the NSE options pipeline, following the Cont, Kukanov & Stoikov (2014) best-bid-ask definition. The module computes snapshot-level and daily OFI from the cleaned options chain DataFrame produced by the ingestion module.

**File to create:** `pipeline/ofi.py`

**Test file to create:** `tests/test_ofi.py`

**Dependencies:** section-01 (project setup), section-02 (Config), section-03 (Ingestion — cleaned DataFrame)

---

## Tests First (`tests/test_ofi.py`)

Write and pass these tests before finalizing the implementation. Use the shared `tiny_day_df` fixture from `conftest.py` (see section-01), which provides a minimal 3-snapshot options chain with 2 strikes, 2 expiries, CE + PE, and realistic column values.

### Test Stubs

```python
# tests/test_ofi.py

import pandas as pd
import numpy as np
import pytest
from pipeline.ofi import compute_ofi, daily_ofi_summary


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_two_snapshot_df(
    bid_price_t0, bid_qty_t0, ask_price_t0, ask_qty_t0,
    bid_price_t1, bid_qty_t1, ask_price_t1, ask_qty_t1,
    expiry="2026-05-01",
    strike=24000,
    option_type="CE",
):
    """
    Build a minimal 2-snapshot DataFrame for a single contract.
    captured_at values are 1-minute apart.
    Returns a DataFrame suitable for compute_ofi().
    """
    ...


# ── Three-Case Bid Formula ────────────────────────────────────────────────────

def test_ofi_bid_up_three_case():
    """
    When bid_price(t) > bid_price(t-1):
    e_bid must equal +bid_qty(t), NOT bid_qty(t) - bid_qty(t-1).
    """
    ...


def test_ofi_bid_same_three_case():
    """
    When bid_price(t) == bid_price(t-1):
    e_bid must equal bid_qty(t) - bid_qty(t-1).
    """
    ...


def test_ofi_bid_down_three_case():
    """
    When bid_price(t) < bid_price(t-1):
    e_bid must equal -bid_qty(t-1).
    """
    ...


# ── Three-Case Ask Formula (mirror with sign reversal) ───────────────────────

def test_ofi_ask_improvement():
    """
    When ask_price(t) < ask_price(t-1) (ask improves, i.e., selling pressure):
    e_ask must equal +ask_qty(t).
    """
    ...


def test_ofi_ask_same():
    """
    When ask_price(t) == ask_price(t-1):
    e_ask must equal ask_qty(t) - ask_qty(t-1).
    """
    ...


def test_ofi_ask_worsens():
    """
    When ask_price(t) > ask_price(t-1):
    e_ask must equal -ask_qty(t-1).
    """
    ...


# ── Chain Aggregation ─────────────────────────────────────────────────────────

def test_ofi_chain_aggregation():
    """
    OFI_chain(t) = sum of (e_bid - e_ask) across all contracts at snapshot t.
    Build a 2-contract, 2-snapshot DataFrame and verify sum manually.
    """
    ...


# ── First Snapshot Must Be NaN ────────────────────────────────────────────────

def test_ofi_first_snapshot_nan():
    """
    OFI for the very first snapshot of the day must be NaN (no prior snapshot to diff).
    Check ofi_raw and ofi_normalized are both NaN at the earliest captured_at.
    """
    ...


# ── Sign Preservation Under Normalization ────────────────────────────────────

def test_ofi_sign_matches_numerator():
    """
    sign(ofi_normalized) must equal sign(ofi_raw) wherever both are non-NaN.
    ofi_normalized is NOT bounded to [-1, 1]; test sign only, not magnitude.
    """
    ...


# ── Expiry Key Isolation ──────────────────────────────────────────────────────

def test_ofi_expiry_key():
    """
    Two contracts at the same (strike, option_type) but different expiry dates
    must be treated as separate contracts — no cross-expiry collision.
    NIFTY has both weekly and monthly expiries at the same strikes.
    """
    ...


# ── NaN Propagation for Appearing/Disappearing Strikes ───────────────────────

def test_ofi_missing_strike_produces_nan():
    """
    A strike that appears at t1 but not at t0 (or vice versa) should produce
    NaN OFI for that contract, not zero. The NaN must NOT be filled with 0
    before chain-level aggregation (it should be excluded via skipna behavior
    or equivalent, but not injected as a false zero).
    """
    ...


# ── Daily Summary ─────────────────────────────────────────────────────────────

def test_daily_ofi_summary_keys():
    """
    daily_ofi_summary() returns a dict with keys:
    ofi_cumsum, ofi_mean, ofi_std
    """
    ...
```

### Key Assertions by Test

| Test | Critical Assertion |
|------|--------------------|
| `test_ofi_bid_up_three_case` | `ofi_raw.iloc[1] == bid_qty_t1` (full new size, not delta) |
| `test_ofi_bid_same_three_case` | `e_bid == bid_qty_t1 - bid_qty_t0` |
| `test_ofi_bid_down_three_case` | `e_bid == -bid_qty_t0` |
| `test_ofi_ask_improvement` | `e_ask == +ask_qty_t1` |
| `test_ofi_ask_same` | `e_ask == ask_qty_t1 - ask_qty_t0` |
| `test_ofi_ask_worsens` | `e_ask == -ask_qty_t0` |
| `test_ofi_first_snapshot_nan` | `pd.isna(result.iloc[0]["ofi_raw"])` |
| `test_ofi_sign_matches_numerator` | `np.sign(ofi_norm) == np.sign(ofi_raw)` for all non-NaN rows |
| `test_ofi_expiry_key` | Running with two expiries gives 2 separate OFI contributions per snapshot; collapsing expiry gives wrong result |

---

## Implementation Details

### File Location

`pipeline/ofi.py`

### Formula Reference — Cont, Kukanov & Stoikov (2014)

For each contract `k` identified by `(expiry, strike_price, option_type)`, between consecutive snapshots at times `t` and `t-1`:

**Bid-side event:**
- `bid_price(t) > bid_price(t-1)` → `e_bid = +bid_qty(t)` (entire new size at improved price enters)
- `bid_price(t) == bid_price(t-1)` → `e_bid = bid_qty(t) − bid_qty(t-1)` (net change at same level)
- `bid_price(t) < bid_price(t-1)` → `e_bid = −bid_qty(t-1)` (entire prior level is wiped)

**Ask-side event** (sign reversal — ask improving means increased selling pressure):
- `ask_price(t) < ask_price(t-1)` → `e_ask = +ask_qty(t)` (improved ask = full new selling supply)
- `ask_price(t) == ask_price(t-1)` → `e_ask = ask_qty(t) − ask_qty(t-1)`
- `ask_price(t) > ask_price(t-1)` → `e_ask = −ask_qty(t-1)` (prior supply wiped)

```
OFI(k, t) = e_bid(k, t) − e_ask(k, t)
```

**Important:** The `Δqty × indicator` shorthand sometimes written in simplified treatments is WRONG for the price-improvement case. When `bid_price` improves, the correct contribution is the full new `bid_qty(t)`, not the delta. This distinction is essential for correctly capturing directional quote events.

### Chain-Level Aggregation

```
OFI_chain(t) = Σ_k OFI(k, t)

OFI_norm(t) = OFI_chain(t) / Σ_k (bid_qty(k,t) + ask_qty(k,t))
```

`OFI_norm` is **not bounded to [-1, 1]**. Tests should check that its sign matches `ofi_raw`, not check a magnitude range.

### Grouping Key

The grouping key for each contract is `(expiry, strike_price, option_type)`. NIFTY has weekly and monthly expiries at the same strikes — omitting `expiry` from the key causes cross-expiry collisions in the pivot/diff step. This is a required constraint.

### Efficiency Approach

1. Sort the DataFrame by `(captured_at, expiry, strike_price, option_type)`.
2. Set the first snapshot's OFI to NaN — there is no `t-1` for the earliest timestamp.
3. For performance: pivot to a matrix where rows are `captured_at` values and columns are `(expiry, strike_price, option_type)` tuples, then compute differences along the time axis using `DataFrame.diff()`. This avoids an explicit Python-level loop over snapshots.
4. After pivoting, apply the three-case logic using `numpy.where` or `pd.Series.where` comparisons on the price-change direction.
5. NaN gaps from strikes that appear or disappear intraday are correct behavior — treat missing data as "no observation" for that contract. Do not zero-fill NaN deltas before aggregation.

### Function Signatures

```python
def compute_ofi(df_day: pd.DataFrame) -> pd.DataFrame:
    """Apply Cont et al. (2014) three-case OFI definition to one day's options chain.

    Parameters
    ----------
    df_day : pd.DataFrame
        Cleaned options chain for one symbol-day. Must contain columns:
        captured_at, expiry, strike_price, option_type,
        bid_price, bid_qty, ask_price, ask_qty.

    Returns
    -------
    pd.DataFrame
        Indexed by captured_at with columns:
            ofi_raw         : float  — chain-level OFI (sum across contracts)
            ofi_normalized  : float  — ofi_raw / total_depth
            total_depth     : float  — sum of (bid_qty + ask_qty) across contracts
        First row is NaN for ofi_raw and ofi_normalized (no prior snapshot).
    """
    ...


def daily_ofi_summary(ofi_df: pd.DataFrame) -> dict:
    """Aggregate snapshot-level OFI DataFrame to daily summary statistics.

    Parameters
    ----------
    ofi_df : pd.DataFrame
        Output of compute_ofi() — indexed by captured_at.

    Returns
    -------
    dict with keys:
        ofi_cumsum  : float  — cumulative sum of ofi_raw (ignores NaN)
        ofi_mean    : float  — mean of ofi_raw (ignores NaN)
        ofi_std     : float  — std dev of ofi_raw (ignores NaN)
    """
    ...
```

### Output Schema

Both snapshot-level and daily summary outputs are written to `outputs/{SYMBOL}_ofi.csv` by the writer module (section-09). The snapshot-level records enable time-of-day analysis; the daily summary enables trend analysis across dates.

Columns in `{SYMBOL}_ofi.csv`:
- `date` — trading date (string, `YYYY-MM-DD`)
- `symbol` — e.g., `NIFTY`
- `captured_at` — snapshot timestamp (IST-aware)
- `ofi_raw` — raw chain-level OFI
- `ofi_normalized` — depth-normalized OFI
- `total_depth` — denominator for normalization
- `ofi_cumsum`, `ofi_mean`, `ofi_std` — from `daily_ofi_summary()`

### Edge Cases

| Situation | Correct Behavior |
|-----------|-----------------|
| First snapshot of day | `ofi_raw = NaN`, `ofi_normalized = NaN` |
| Strike appears mid-day (no t-1) | NaN for that contract's OFI — not 0 |
| Strike disappears mid-day | NaN for that contract from disappearance onward |
| `total_depth = 0` at some snapshot | `ofi_normalized = NaN` (avoid divide-by-zero) |
| Same strike at two expiries | Treated as two separate contracts (expiry in grouping key) |

---

## Dependencies on Other Sections

- **section-01 (Project Setup):** Provides `conftest.py` with the `tiny_day_df` fixture used in tests. Tests import from `pipeline.ofi`.
- **section-02 (Config):** `compute_ofi()` does not require a `Config` argument; all behavior is self-contained. However `daily_ofi_summary()` output is appended by the writer (section-09), which uses `Config.output_dir`.
- **section-03 (Ingestion):** `compute_ofi()` operates on the DataFrame returned by `load_day()`. Required columns — `captured_at`, `expiry`, `strike_price`, `option_type`, `bid_price`, `bid_qty`, `ask_price`, `ask_qty` — are guaranteed by the ingestion cleaning step. Do not add duplicate cleaning logic here.

This section does not depend on section-04 (IV), section-05 (Realized Vol), section-06 (VRP), or section-08 (Liquidity). It can be implemented in parallel with those sections after section-03 is complete.
