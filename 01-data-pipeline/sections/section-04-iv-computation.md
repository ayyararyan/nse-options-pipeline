# Section 04: IV Computation (`pipeline/iv.py`)

## Overview

This section implements the implied volatility (IV) computation module. It provides a Black-Scholes pricer for European-style options and a Brent root-finding solver that inverts the pricer to recover IV from the bid-ask midpoint. This module is consumed directly by section-06-vrp and by the pipeline orchestration (section-10).

**Depends on:** section-03-ingestion (the normalized DataFrame with `mid_price`, `time_to_expiry`, `underlying_value`, `strike_price`, `option_type`, `bid_price`, `ask_price` columns must already be present).

**Blocks:** section-06-vrp, section-10-pipeline-orchestration.

**File to create:** `pipeline/iv.py`

**Test file to create:** `tests/test_iv.py`

---

## Background and Design Decisions

### Why recompute IV instead of using the NSE column?

The `implied_volatility` column in the raw CSV is present but 43% of its values are zero (illiquid strikes). Recomputing IV from the bid-ask midpoint ensures full and consistent coverage across the entire chain.

### Why Black-Scholes European (not American)?

NSE NIFTY, BANKNIFTY, and FINNIFTY options are European-style, cash-settled on the index value. There is no early exercise premium. Standard Black-Scholes is correct; no binomial or finite-difference model is needed.

### Annualization convention: Calendar-365

Time to expiry is computed as:

```
T = max(0, (expiry_close_ist - snapshot_ist).total_seconds() / (365.25 × 86400))
```

Using `total_seconds()` preserves fractional days — this is critical on expiry day (NSE weekly expiries are Thursdays). Without it, T rounds to zero at 09:15 on expiry morning and the entire high-volume expiry-day session loses IV. The `365.25` denominator matches the India VIX methodology and is consistent with how RV is annualized in section-05 (`×365`). Do **not** mix calendar-365 IV with trading-252 RV — doing so introduces a ~3% systematic gap in the VRP.

### Discounted intrinsic bound

Rows where `mid_price < discounted_intrinsic` must be skipped (return NaN), not attempted. The discounted intrinsic is:

- Call: `max(S·e^{−qT} − K·e^{−rT}, 0)`
- Put: `max(K·e^{−rT} − S·e^{−qT}, 0)`

Using raw (undiscounted) intrinsic is the wrong bound for European options and causes unnecessary NaNs and Brent bracket failures.

---

## Tests First (`tests/test_iv.py`)

Write and pass these tests **before** completing the implementation. Tests use `pytest`.

```python
# tests/test_iv.py

"""
Tests for pipeline/iv.py — Black-Scholes pricer and IV solver.

Covers:
  - Call-put parity (dividend-adjusted)
  - IV round-trip accuracy for call and put
  - NaN sentinel returns for zero-bid, zero-ask, T ≤ 0
  - Finite IV at 09:15 on expiry day (fractional-T formula)
  - NaN return when mid_price < discounted intrinsic (call and put)
  - Convergence-bucket logging path (smoke test)
"""

import math
import pandas as pd
import pytest
from pipeline.iv import bs_price, compute_iv, add_computed_iv


# ── shared parameters ────────────────────────────────────────────────────────
S = 24000.0    # spot
K = 24000.0    # ATM strike
T = 30 / 365.25
r = 0.065
q = 0.0
sigma = 0.20


def test_bs_call_put_parity():
    """C − P ≈ S·e^{−qT} − K·e^{−rT}  (dividend-adjusted put-call parity)."""
    ...


def test_iv_roundtrip_call():
    """compute_iv(bs_price(sigma=0.20, CE)) recovers 0.20 to within 1e-6."""
    ...


def test_iv_roundtrip_put():
    """compute_iv(bs_price(sigma=0.20, PE)) recovers 0.20 to within 1e-6."""
    ...


def test_iv_zero_bid():
    """Returns NaN/None when bid_price = 0 (illiquid quote)."""
    ...


def test_iv_zero_ask():
    """Returns NaN/None when ask_price = 0."""
    ...


def test_iv_expired():
    """Returns NaN/None when T ≤ 0 (contract already expired at snapshot time)."""
    ...


def test_iv_expiry_day_fractional_t():
    """At 09:15 on expiry day, T > 0 and compute_iv returns a finite positive value.

    Expiry is today at 15:30; snapshot is at 09:15 — there are ~6.25 hours left.
    T must be fractional (not zero), so IV is finite and meaningful.
    """
    ...


def test_iv_deep_otm_call_below_intrinsic():
    """Returns NaN when mid_price < discounted intrinsic for a call."""
    # mid below S·e^{−qT} − K·e^{−rT}
    ...


def test_iv_deep_otm_put_below_intrinsic():
    """Returns NaN when mid_price < discounted intrinsic for a put."""
    # mid below K·e^{−rT} − S·e^{−qT}
    ...


def test_add_computed_iv_convergence_logging(tiny_day_df, capsys):
    """add_computed_iv() runs without error and produces a 'computed_iv' column.

    This is a smoke test: verify the column is added and convergence stats are emitted
    to logs (not checked by value here — the iv_roundtrip tests cover accuracy).
    tiny_day_df fixture comes from conftest.py.
    """
    ...
```

The `tiny_day_df` fixture is defined in `tests/conftest.py` (created in section-01). It is a minimal 3-snapshot DataFrame with realistic NIFTY values, 2 strikes, 2 expiries, CE + PE option types, and a valid `mid_price`, `time_to_expiry`, `underlying_value`, `bid_price`, `ask_price`.

---

## Implementation (`pipeline/iv.py`)

### Module-level constants and imports

```python
# pipeline/iv.py

import logging
import math
from typing import Optional

import numpy as np
import pandas as pd
from scipy.optimize import brentq

logger = logging.getLogger(__name__)

BRENTQ_LOWER = 1e-6
BRENTQ_UPPER = 10.0
```

---

### `bs_price`

```python
def bs_price(S: float, K: float, T: float, r: float, sigma: float,
             option_type: str, q: float = 0.0) -> float:
    """Return the Black-Scholes European option price with continuous dividend yield q.

    Parameters
    ----------
    S           : spot price of the underlying index
    K           : strike price
    T           : time to expiry in calendar years (must be > 0)
    r           : continuously compounded risk-free rate (decimal, e.g. 0.065)
    sigma       : annualised volatility (decimal, e.g. 0.20)
    option_type : 'CE' or 'PE'
    q           : continuous dividend yield (default 0.0 for v1)

    Returns
    -------
    float : option price

    Notes
    -----
    Formulae:
        d1 = (ln(S/K) + (r − q + σ²/2) × T) / (σ × √T)
        d2 = d1 − σ × √T
        C  = S·e^{−qT}·N(d1) − K·e^{−rT}·N(d2)
        P  = K·e^{−rT}·N(−d2) − S·e^{−qT}·N(−d1)
    """
```

---

### `compute_iv`

```python
def compute_iv(S: float, K: float, T: float, r: float, mid: float,
               option_type: str, q: float = 0.0) -> Optional[float]:
    """Return implied volatility in decimal annualised form, or None on failure.

    Skips (returns None) for any of the following conditions:
      1. mid <= 0, or bid_price == 0, or ask_price == 0
         (caller is responsible for passing mid only when both bid and ask > 0)
      2. T <= 0  (expired contract)
      3. mid < discounted intrinsic:
           - call: max(S·e^{−qT} − K·e^{−rT}, 0)
           - put:  max(K·e^{−rT} − S·e^{−qT}, 0)
      4. brentq raises ValueError (no root in (BRENTQ_LOWER, BRENTQ_UPPER))

    Parameters
    ----------
    S           : spot price
    K           : strike price
    T           : time to expiry in calendar years
    r           : risk-free rate (decimal)
    mid         : bid-ask midpoint
    option_type : 'CE' or 'PE'
    q           : continuous dividend yield (default 0.0)

    Returns
    -------
    float or None
    """
```

**Note:** The caller (`add_computed_iv`) already filters `bid_price == 0 or ask_price == 0` before calling `compute_iv`; the function should also guard internally for robustness.

---

### `add_computed_iv`

```python
def add_computed_iv(df: pd.DataFrame, rate: float, cfg) -> pd.DataFrame:
    """Add a `computed_iv` column to the options chain DataFrame.

    Applies compute_iv row-by-row. NaN is stored where compute_iv returns None.
    Convergence counts are logged at INFO level for the calling pipeline run.

    Convergence buckets logged:
      - iv_nan_zero_quote    : mid_price <= 0 or bid == 0 or ask == 0
      - iv_nan_expired       : T <= 0
      - iv_nan_intrinsic     : mid < discounted intrinsic
      - iv_nan_no_root       : brentq raised ValueError
      - iv_converged         : valid IV was recovered

    Parameters
    ----------
    df   : DataFrame from load_day() (section-03), must have columns
           ['mid_price', 'time_to_expiry', 'underlying_value', 'strike_price',
            'option_type', 'bid_price', 'ask_price']
    rate : risk-free rate in decimal form (e.g. 0.065)
    cfg  : Config object (used for cfg.dividend_yield, cfg.brentq_bounds)

    Returns
    -------
    DataFrame with added column `computed_iv` (float, NaN where IV not recoverable)

    Notes
    -----
    Row iteration is intentional: brentq is a scalar solver and vectorising it
    would require a custom Newton loop, adding complexity for marginal speed gain.
    ~300K calls per NIFTY symbol-day complete in <5 minutes on a modern laptop.
    """
```

---

## Acceptance Criteria

All of the following must hold before this section is considered done:

1. `pytest tests/test_iv.py -v` passes with all tests green.
2. `bs_price` satisfies put-call parity to `1e-6` tolerance across a range of moneyness values.
3. `compute_iv` round-trips: `compute_iv(bs_price(sigma=X, ...)) ≈ X` to `1e-6` for X in `[0.05, 0.20, 0.80]` for both CE and PE.
4. `compute_iv` returns `None` (not raises) for all four failure conditions documented above.
5. `add_computed_iv` adds a `computed_iv` column without modifying any existing column in the DataFrame.
6. Convergence log line is emitted at `INFO` level and includes all five buckets.
7. On the `tiny_day_df` fixture, at least one row reaches `iv_converged` (smoke test that the full path works).

---

## Integration Notes

- The `time_to_expiry` column on the DataFrame is set by `load_day()` (section-03) as `T = max(0, (expiry_close_ist - snapshot_ist).total_seconds() / (365.25 × 86400))`. Do not recompute it inside `iv.py`; read it directly from the row.
- The `mid_price` column is also set by `load_day()` as `(bid_price + ask_price) / 2`. Rows with no valid bid or ask have `mid_price = NaN`; these fall into the `iv_nan_zero_quote` bucket.
- The `rate` argument to `add_computed_iv` is fetched by the rates module (section-09) and passed in by the orchestrator (section-10). `iv.py` does not fetch rates itself.
- `add_computed_iv` returns a copy (or adds the column in place with `df.assign`); it must not silently modify the caller's DataFrame in a way that affects downstream modules.
