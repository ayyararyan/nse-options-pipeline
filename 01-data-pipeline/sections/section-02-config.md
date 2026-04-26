# section-02-config

## Overview

This section implements `pipeline/config.py`, which centralizes all tunable parameters and file paths for the NSE options data pipeline. Every other module imports `Config` rather than hardcoding values. Completing this section unblocks sections 03 through 10.

**Depends on:** section-01-project-setup (project directory scaffold, `pipeline/__init__.py`, and `pyproject.toml` must already exist)

**File to create:** `pipeline/config.py`

---

## Tests First

Write `tests/test_config.py` before implementing. The tests use `pytest`. All five tests must pass before the section is considered complete.

```python
# Test: Config loads with all required fields — no KeyError, no AttributeError, no TypeError
# Test: Config.atm_increments has entries for all three symbols: NIFTY, BANKNIFTY, FINNIFTY
# Test: Config.ann_factor == 365  (calendar-day convention, NOT 252)
# Test: Config.timezone == "Asia/Kolkata"
# Test: Providing a data_dir path that does not exist raises a clear error at construction time
#       (not silently stored and only discovered at runtime)
```

The fifth test is the critical design signal: the constructor must validate `data_dir` existence (and optionally `output_dir`) rather than letting callers discover a bad path later during pipeline execution. Implement this in `__post_init__`.

---

## Implementation

### File: `pipeline/config.py`

The module exposes a single `Config` dataclass. Use `@dataclass` from the standard library. No environment-variable loading — all values come from constructor arguments with defaults.

**Dataclass definition:**

```python
from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class Config:
    data_dir: Path              # NSEI-Data/
    output_dir: Path            # outputs/
    symbols: list[str]          # ["NIFTY", "BANKNIFTY", "FINNIFTY"]
    market_open: str            # "09:15"
    market_close: str           # "15:30"
    resample_freq: str          # "5min"
    rolling_windows: list[int]  # [5, 10, 21]
    ann_factor: int             # 365  (calendar-day — see note below)
    brentq_bounds: tuple        # (1e-6, 10.0)
    default_rate: float         # 0.065
    dividend_yield: float       # 0.0
    atm_increments: dict        # {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}
    timezone: str               # "Asia/Kolkata"

    def __post_init__(self):
        """Validate paths and coerce Path fields."""
        ...
```

### `__post_init__` responsibilities

1. Coerce `data_dir` and `output_dir` to `Path` objects (in case strings were passed).
2. Check `data_dir.exists()` — if it does not exist, raise `FileNotFoundError` with a message that includes the resolved path.
3. Create `output_dir` if it does not exist (`output_dir.mkdir(parents=True, exist_ok=True)`).
4. No other validation is required for v1.

### Default factory function

Provide a `default_config(data_dir: Path, output_dir: Path) -> Config` convenience function that constructs a `Config` with all the default values listed above. This is what `run_pipeline.py` will call.

```python
def default_config(data_dir: Path, output_dir: Path) -> Config:
    """Return a Config with all pipeline defaults for NIFTY/BANKNIFTY/FINNIFTY."""
    ...
```

---

## Critical Constants and Rationale

**`ann_factor = 365` (calendar-day convention, not 252 trading days)**

Both IV and realized volatility must use the same annualization convention. IV is computed as `T = delta.total_seconds() / (365.25 × 86400)`, a calendar-year fraction. Realized volatility is annualized by multiplying variance by 365. Mixing calendar-IV with trading-252-RV introduces a systematic ~3% gap in the VRP headline metric — options would appear persistently overpriced even when they are not. This matches the NSE India VIX methodology.

The `test_realized_vol.py` suite includes an explicit test `rk_ann_vol uses factor sqrt(365), NOT sqrt(252)` that will fail if `ann_factor` is set to 252.

**`timezone = "Asia/Kolkata"`**

All datetimes in the pipeline are stored as IST-aware (`Asia/Kolkata`). The ingestion module parses `captured_at` and `expiry` as IST-aware datetimes. Do not mix IST and UTC — the Indian market does not observe Daylight Saving Time, so `Asia/Kolkata` is always UTC+05:30 and there is no DST ambiguity.

**`dividend_yield = 0.0`**

NIFTY has an approximate 1.2% dividend yield in practice. For v1 this is set to zero. The field is present so it can be swapped in a later version without changing function signatures. The `bs_price` and `compute_iv` functions accept `q` as a parameter and will use `cfg.dividend_yield`.

**`atm_increments = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}`**

NSE NIFTY and FINNIFTY options are listed in 50-point strike increments; BANKNIFTY in 100-point increments. The VRP module uses these to identify the ATM strike by rounding `underlying_value` to the nearest increment with half-up tie-breaking.

**`brentq_bounds = (1e-6, 10.0)`**

The interval for `scipy.optimize.brentq` when solving for IV. The lower bound (1e-6) avoids numerical issues at σ → 0. The upper bound (10.0 = 1000% IV) is wide enough to bracket any plausible observed price. If `brentq` raises `ValueError` (no sign change in the interval), the row returns NaN — this is expected behavior for deep-OTM options with mispriced quotes.

---

## Dependency Notes

- **section-01** must have created `pipeline/__init__.py` before this file can be imported by tests or other modules.
- This module has no imports from other `pipeline` submodules. It only uses the Python standard library (`dataclasses`, `pathlib`).
- All other pipeline modules (`ingestion.py`, `iv.py`, `realized_vol.py`, etc.) will import `Config` from this module. Never import pipeline modules back into `config.py`.
