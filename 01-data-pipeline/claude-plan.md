# Implementation Plan: NSE Options Data Pipeline & Core Metrics

## What We're Building

A Python pipeline that reads NSE (National Stock Exchange of India) options chain CSV snapshots for NIFTY, BANKNIFTY, and FINNIFTY, computes five families of derived analytics metrics, and writes the results as CSV files consumed downstream by a Streamlit dashboard and a volatility surface fitting module.

The pipeline runs weekly via a scheduled script (`run_pipeline.py`), processes each newly arrived trading day incrementally, and appends results to per-symbol output CSVs. All data stays in CSV format — no format conversion needed.

---

## Data Source and Schema

Data lives in `NSEI-Data/date=YYYY-MM-DD/{SYMBOL}.csv` (one file per underlying per trading day). Each file contains the full options chain captured at approximately 1-minute intervals throughout the NSE trading session (09:15–15:30 IST), yielding ~280 snapshots and ~2,000 rows per snapshot (all active strikes × all expiry dates × calls and puts). A NIFTY day file runs to ~562K rows; BANKNIFTY to ~368K.

The key columns the pipeline relies on:

- `captured_at` — ISO timestamp of the snapshot (09:15–15:45)
- `symbol` — NIFTY, BANKNIFTY, or FINNIFTY (some rows have blank symbol — these are filtered out)
- `expiry` — contract expiry date, formatted `DD-MM-YYYY`
- `strike_price`, `option_type` — CE/PE
- `bid_price`, `ask_price`, `bid_qty`, `ask_qty` — best bid/ask quote
- `open_interest`, `total_traded_volume`
- `total_buy_quantity`, `total_sell_quantity` — whole-book aggregated quantities
- `underlying_value` — spot price of the index at snapshot time

The `implied_volatility` column is present but 43% of its values are zero (illiquid strikes). The pipeline always computes IV from bid-ask midpoint for consistency; the NSE-provided column is ignored.

---

## Directory Structure

```
project_root/
├── NSEI-Data/
│   └── date=YYYY-MM-DD/
│       ├── NIFTY.csv
│       ├── BANKNIFTY.csv
│       └── FINNIFTY.csv
│
├── outputs/
│   ├── options_chain/             ← per-date chain files (one per symbol per day)
│   │   ├── NIFTY/
│   │   │   ├── date=2026-04-20.csv
│   │   │   └── date=2026-04-21.csv
│   │   ├── BANKNIFTY/
│   │   └── FINNIFTY/
│   ├── NIFTY_realized_vol.csv
│   ├── NIFTY_vrp.csv
│   ├── NIFTY_ofi.csv
│   ├── NIFTY_liquidity.csv
│   ├── BANKNIFTY_realized_vol.csv
│   ├── ... (same pattern for BANKNIFTY, FINNIFTY)
│   ├── rates.csv
│   └── processed_dates.json
│
├── pipeline/
│   ├── __init__.py
│   ├── config.py
│   ├── ingestion.py
│   ├── iv.py
│   ├── realized_vol.py
│   ├── vrp.py
│   ├── ofi.py
│   ├── liquidity.py
│   ├── rates.py
│   └── writer.py
│
├── run_pipeline.py
├── pipeline.log
└── tests/
    ├── test_iv.py
    ├── test_realized_vol.py
    ├── test_ofi.py
    ├── test_vrp.py
    └── test_pipeline_idempotency.py
```

---

## Module 1: Configuration (`pipeline/config.py`)

Centralizes all tunable parameters and paths so the rest of the pipeline never hardcodes them.

```python
@dataclass
class Config:
    data_dir: Path              # NSEI-Data/
    output_dir: Path            # outputs/
    symbols: list[str]          # ["NIFTY", "BANKNIFTY", "FINNIFTY"]
    market_open: str            # "09:15"
    market_close: str           # "15:30"
    resample_freq: str          # "5min"
    rolling_windows: list[int]  # [5, 10, 21]
    ann_factor: int             # 365  (calendar-day convention; see note below)
    brentq_bounds: tuple        # (1e-6, 10.0)
    default_rate: float         # 0.065
    dividend_yield: float       # 0.0  (NIFTY ~1.2% yield; set to 0 in v1, swappable)
    atm_increments: dict        # {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}
    timezone: str               # "Asia/Kolkata"  (all datetimes stored IST-aware)
```

**Annualization convention:** Calendar-365 is used throughout (IV uses `T = delta.total_seconds() / (365.25×86400)`, RV is annualized by `×365`). This aligns both sides of the VRP on the same time base and matches the NSE India VIX methodology. Do NOT mix calendar-365 IV with trading-252 RV or the VRP will have a ~3% systematic gap.

---

## Module 2: Ingestion (`pipeline/ingestion.py`)

Loads a single `date=YYYY-MM-DD/{SYMBOL}.csv`, cleans it, and returns a normalized DataFrame ready for metric computation.

### Cleaning Steps
1. Parse `captured_at` as IST-aware datetime (timezone: `Asia/Kolkata`). All datetimes in the pipeline are IST-aware; never mix IST and UTC.
2. Filter rows where `symbol` is not in the expected set (blank rows often appear for strikes that span multiple underlyings)
3. Keep only snapshots within `market_open` to `market_close` (discard 15:30–15:45 closing auction rows)
4. Parse `expiry` to a full IST datetime at 15:30 on the expiry date — not just a `date` object. This is critical for fractional `time_to_expiry` on expiry day (see Module 3).
5. Cast numeric columns; coerce errors to NaN
6. Deduplicate: where multiple rows share the same `(captured_at_min, strike_price, option_type, expiry)`, keep the last
7. Forward-fill `underlying_value` for IV inputs only. The RK spot extraction (Module 4) must use raw, non-forward-filled spot values to avoid injecting zero log-returns from fill artifacts.

### Key Function Signatures

```python
def load_day(date_dir: Path, symbol: str, cfg: Config) -> pd.DataFrame:
    """Load and clean one day's CSV for the given symbol.
    
    Returns normalized DataFrame with added column `time_to_expiry` (calendar-year fraction)
    and `mid_price` ((bid_price + ask_price) / 2). Rows with no valid bid or ask are kept
    but have mid_price = NaN.
    """

def discover_new_dates(data_dir: Path, processed_dates: set[str]) -> list[str]:
    """Scan data_dir for date=YYYY-MM-DD folders and return those not in processed_dates."""
```

---

## Module 3: IV Computation (`pipeline/iv.py`)

Computes implied volatility from bid-ask midpoint for every row in the options chain DataFrame.

### Black-Scholes Pricing (European)

The Black-Scholes formula with continuous dividend yield `q` (default `q = 0` for v1; NIFTY 50 has ~1.2% dividend yield, ignored in v1):

```
d1 = (ln(S/K) + (r − q + σ²/2) × T) / (σ × √T)
d2 = d1 − σ × √T
C  = S·e^{−qT}·N(d1) − K·e^{−rT}·N(d2)
P  = K·e^{−rT}·N(−d2) − S·e^{−qT}·N(−d1)
```

IV is the σ that makes the formula equal to `mid_price`. Solved with `scipy.optimize.brentq` on the interval `(1e-6, 10.0)`.

**Time to expiry:** `T = max(0, (expiry_close_ist - snapshot_ist).total_seconds() / (365.25 × 86400))`, where `expiry_close_ist` is the expiry date at 15:30 IST. Using fractional seconds avoids zeroing out the entire expiry-day session (NSE weekly expiries are Thursday; this is a high-volume, high-information day).

### Row-Level Logic

For each row:
1. Skip (return NaN) if `mid_price ≤ 0` or `bid_price == 0` or `ask_price == 0`
2. Skip if `T ≤ 0` (already expired at snapshot time)
3. Skip if `mid_price < discounted_intrinsic`: for calls `max(S·e^{−qT} − K·e^{−rT}, 0)`; for puts `max(K·e^{−rT} − S·e^{−qT}, 0)`. Raw intrinsic (without discounting) is the wrong bound for European options and causes unnecessary NaNs and Brent failures.
4. Solve via Brent; if `brentq` raises `ValueError` (no root in bracket), return NaN

Log IV convergence rate at three buckets: `iv_nan_zero_quote`, `iv_nan_expired`, `iv_nan_intrinsic`, `iv_nan_no_root`, `iv_converged`.

NSE NIFTY, BANKNIFTY, and FINNIFTY options are European-style cash-settled on the index value. Standard Black-Scholes (no early exercise premium) is correct.

### Key Function Signatures

```python
def bs_price(S: float, K: float, T: float, r: float, sigma: float, option_type: str, q: float = 0.0) -> float:
    """Return Black-Scholes European option price with continuous dividend yield q."""

def compute_iv(S: float, K: float, T: float, r: float, mid: float, option_type: str, q: float = 0.0) -> float | None:
    """Return implied volatility in decimal annualized form, or None on failure."""

def add_computed_iv(df: pd.DataFrame, rate: float, cfg: Config) -> pd.DataFrame:
    """Vectorized wrapper: adds `computed_iv` column to the options chain DataFrame.
    
    Applies compute_iv row-by-row. Records convergence rate in logs.
    """
```

---

## Module 4: Realized Volatility (`pipeline/realized_vol.py`)

Computes microstructure-noise-robust daily realized volatility from the intraday `underlying_value` series.

### Spot Price Extraction

The `underlying_value` column contains the index spot price at each snapshot. Extract a time series, resample to 5-minute bars (last value per window), compute log returns:

```
spot_5m = spot.resample('5min').last().between_time(open, close).dropna()
log_rets = log(spot_5m).diff().dropna()
```

For NSE, 09:15–15:30 at 5-minute intervals gives n ≈ 75 bars per day.

### Realized Kernel (Parzen, Non-Flat-Top)

The BNHLS (2008/2009) realized kernel with Parzen weights guarantees non-negativity and is the practitioner standard for microstructure-robust RV:

```
RK = k(0)·γ₀ + 2·Σ_{h=1}^{H} k(h/(H+1))·γ_h
```

where `γ_h = Σ_{j>h} r_j·r_{j-h}` is the h-th sample autocovariance.

Parzen weights: `k(x) = 1 − 6x²(1−x)` for x ≤ 0.5, `k(x) = 2(1−x)³` for x > 0.5.

Optimal bandwidth: `H* = ceil(3.5134 × ξ^(4/5) × n^(3/5))` where, following BNHLS (2009) §3, `ξ² = ω² / √IQ`, so `ξ = ω / IQ^(1/4)`. Noise variance `ω²` is estimated as `max(-0.5 × mean(r_j × r_{j+1}), ε)` (negative first-order autocovariance equals twice the bid-ask noise variance under an iid noise model). Integrated quarticity `IQ` is estimated as `(n/3) × Σ r_j⁴`. Conservative fallback: `H = ceil(0.5 × sqrt(n))`.

### Overnight Return

The realized kernel captures only intraday variance. India VIX prices full 24-hour variance. To make RV comparable to IV (and hence compute a meaningful VRP), add the overnight squared return:

```
rk_full = rk_intraday + (log(today_open / yesterday_close))²
```

### Daily Output

For each trading day, compute:
- `rk_daily_var` — full-day realized variance (intraday RK + overnight²)
- `rk_daily_vol` — `sqrt(rk_daily_var)`, daily standard deviation
- `rk_ann_vol` — `rk_daily_vol × sqrt(365)`, annualized volatility (calendar-365; must match IV convention)
- `rk_5d_ann`, `rk_10d_ann`, `rk_21d_ann` — rolling annualized vol over 5/10/21 trading days

**Rolling RV computation:** Average variance (not vol) over the window, then annualize: `rk_Nd_ann = sqrt(rolling_mean(rk_daily_var, N) × 365)`. Averaging vol directly introduces Jensen's inequality bias (systematically low by a few percent). This matters for VRP comparisons near zero.

### Key Function Signatures

```python
def parzen_weights(H: int) -> np.ndarray:
    """Return Parzen kernel weights k(0/(H+1)) ... k(H/(H+1)), shape (H+1,)."""

def optimal_bandwidth(log_rets: np.ndarray) -> int:
    """Return BNHLS optimal bandwidth H* for the Parzen kernel."""

def realized_kernel(log_rets: np.ndarray, H: int | None = None) -> float:
    """Compute BNHLS realized kernel for one day's intraday log-returns.
    
    Returns non-negative variance estimate. H=None triggers adaptive bandwidth selection.
    """

def compute_daily_rk(df: pd.DataFrame, cfg: Config, prev_close: float | None) -> dict:
    """Extract spot from df, resample to 5min, apply realized kernel + overnight return.
    
    Returns dict: {rk_daily_var, rk_daily_vol, rk_ann_vol, bandwidth_H, n_bars}
    """

def compute_rolling_rv(daily_rk_var: pd.Series, windows: list[int], ann_factor: int) -> pd.DataFrame:
    """Rolling-mean of daily variance, then annualize. Input is variance (not vol)."""
```

---

## Module 5: Variance Risk Premium (`pipeline/vrp.py`)

Computes the daily VRP as the difference between 30-day constant-maturity ATM IV and realized volatility.

### ATM IV Extraction

At each snapshot, identify the ATM strike: round `underlying_value` to the nearest strike increment (50 for NIFTY/FINNIFTY, 100 for BANKNIFTY). Tie-break: round half-up (e.g., at 24,125 on a 50-grid → 24,150). Extract `computed_iv` for the ATM call and put at that snapshot.

**ATM IV value:** Average of call and put IV if both are non-NaN. If only one side has a valid IV, use that side. If both are NaN, the snapshot's ATM IV is NaN (do not fabricate).

### Constant-Maturity (30-Day) Interpolation

For each snapshot, gather the available expiry dates that have a valid ATM IV. Identify the two expiries bracketing 30 calendar days forward from `captured_at`. Apply linear interpolation in time-to-expiry space to produce IV_30d for that snapshot.

If only one expiry is available (no bracketing pair), use that single expiry's IV as a proxy but **flag it**: the output must include an `iv_30d_is_extrapolated` boolean column. When only a short-dated expiry is available (e.g., only 7-day expiry near a weekly rollover), the proxy will overstate true 30-day IV due to the upward-sloping term structure — downstream consumers should filter or highlight these rows.

Take the daily median IV_30d as the representative daily ATM IV (median is preferred over mean to be robust to early/late-day anomalies).

### VRP Computation

```
vrp_variance = iv_30d_decimal² − rk_ann_var        # (iv² − rk_ann_var), calendar-365 throughout
vrp_vol      = iv_30d_decimal  − rk_ann_vol         # (iv − rk_ann_vol), for display
```

Both are stored. `vrp_variance` is the theoretically correct quantity (VRP is defined in variance space); `vrp_vol` is more intuitive for visualization.

**Critical:** Both `iv_30d_decimal` and `rk_ann_var` must use the same annualization convention. Both are on a calendar-365 basis in this pipeline. Mixing calendar-IV with trading-day-RV produces a ~3% systematic gap in the headline metric.

Positive VRP means IV > RV (options market prices more uncertainty than was realized — the typical "variance risk premium" regime). Negative VRP may indicate jump or crisis periods where realized vol spiked above implied.

### Key Function Signatures

```python
def extract_atm_iv(df_day: pd.DataFrame, cfg: Config) -> pd.Series:
    """Return time series of 30-day constant-maturity ATM IV for all snapshots in df_day.
    
    Index: captured_at. Values: IV_30d in decimal annualized form.
    """

def compute_vrp(daily_atm_iv: pd.Series, daily_rv: pd.Series) -> pd.DataFrame:
    """Join daily ATM IV and RK RV and compute vrp_variance and vrp_vol."""
```

---

## Module 6: Order Flow Imbalance (`pipeline/ofi.py`)

Implements the Cont, Kukanov, and Stoikov (2014) best-bid-ask OFI definition.

### Formula (Cont, Kukanov & Stoikov 2014 — three-case definition)

For each contract `k` identified by `(expiry, strike_price, option_type)` between consecutive snapshots at times t and t-1:

**Bid-side contribution:**
- If `bid_price(k,t) > bid_price(k,t-1)`: `e_bid = +bid_qty(k,t)` (full new size enters at improved price)
- If `bid_price(k,t) = bid_price(k,t-1)`: `e_bid = bid_qty(k,t) − bid_qty(k,t-1)` (net change at same level)
- If `bid_price(k,t) < bid_price(k,t-1)`: `e_bid = −bid_qty(k,t-1)` (entire prior level is wiped)

**Ask-side contribution** (mirror with sign reversal):
- If `ask_price(k,t) < ask_price(k,t-1)`: `e_ask = +ask_qty(k,t)` (improvement on ask = selling pressure)
- If `ask_price(k,t) = ask_price(k,t-1)`: `e_ask = ask_qty(k,t) − ask_qty(k,t-1)`
- If `ask_price(k,t) > ask_price(k,t-1)`: `e_ask = −ask_qty(k,t-1)`

```
OFI(k, t) = e_bid(k, t) − e_ask(k, t)
```

The earlier `Δqty × indicator` shorthand is incorrect — when the bid price improves, the correct contribution is the full new size, not the delta. This three-case form is essential for correctly capturing directional quote events.

### Aggregation

**Grouping key must include expiry:** NIFTY has weekly and monthly expiries at the same strike, so `(expiry, strike_price, option_type)` is the minimum key. Omitting expiry causes cross-expiry collisions in the pivot/diff step.

Sum `OFI(k, t)` across all contracts for a chain-level OFI per snapshot:

```
OFI_chain(t) = Σ_k OFI(k, t)
```

Normalize by total outstanding best-quote depth:

```
OFI_norm(t) = OFI_chain(t) / Σ_k (bid_qty(k,t) + ask_qty(k,t))
```

Note: `OFI_norm` is not bounded to [-1, 1] in general. Test for "sign matches numerator" rather than range constraints.

### Implementation Notes

Sort by `(captured_at, expiry, strike_price, option_type)`. OFI for the first snapshot of each day is undefined — set to NaN.

For efficiency: pivot to `(snapshot_time, (expiry, strike, type))` as coordinates, then compute differences along the time axis using `DataFrame.diff()`. Handle NaN gaps from strikes appearing/disappearing intraday: NaN deltas are correct behavior (treat as no observation).

### Output

Store both snapshot-level OFI (for time-of-day analysis) and daily summary (cumulative OFI, mean OFI, OFI std-dev) in `{SYMBOL}_ofi.csv`.

### Key Function Signatures

```python
def compute_ofi(df_day: pd.DataFrame) -> pd.DataFrame:
    """Apply Cont et al. OFI definition to one day's options chain snapshots.
    
    Returns DataFrame indexed by captured_at with columns:
    ofi_raw, ofi_normalized, total_depth.
    """

def daily_ofi_summary(ofi_series: pd.DataFrame) -> dict:
    """Aggregate snapshot-level OFI to daily: cumsum, mean, std."""
```

---

## Module 7: Liquidity Metrics (`pipeline/liquidity.py`)

Computes per-contract and aggregate liquidity measures.

### Per-Contract Metrics (per snapshot)

- `mid_price = (bid_price + ask_price) / 2`
- `relative_spread = (ask_price − bid_price) / mid_price` (set to NaN where `mid_price ≤ 0`)
- `depth = bid_qty + ask_qty`

### Daily Aggregates (per symbol per date)

| Metric | Definition |
|--------|------------|
| `atm_spread_mean` | Mean relative_spread for ATM strikes (within 2% of spot) |
| `chain_spread_mean` | Mean relative_spread across all liquid strikes (where `depth > 0`) |
| `chain_spread_p50` | Median relative_spread (robust to illiquid tails) |
| `total_oi` | Sum of `open_interest` across all contracts |
| `total_volume` | Sum of `total_traded_volume` across all contracts |
| `atm_oi` | Total OI for ATM strikes |
| `put_call_oi_ratio` | Sum PE `open_interest` / Sum CE `open_interest` |

### Key Function Signatures

```python
def compute_liquidity(df_day: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Return daily liquidity aggregate for one symbol/day.
    
    One row per (date, expiry) with all per-expiry metrics plus symbol-level aggregates.
    """
```

---

## Module 8: Rates Fetcher (`pipeline/rates.py`)

Fetches the current short-term risk-free rate for use in Black-Scholes IV computation.

### Fetching Logic

On each weekly pipeline run:
1. Try fetching the 91-day T-bill yield from the RBI Data Warehouse API (`https://rbi.org.in/` statistics section or CCIL API)
2. Parse the latest available date's rate, convert to decimal (e.g., 6.75% → 0.0675)
3. If the fetch fails (network error, parsing error), use the last stored rate from `rates.csv`
4. If `rates.csv` does not exist, fall back to the hardcoded default `0.065` (6.5%)
5. Append the newly fetched (date, rate) to `rates.csv`

### Key Function Signatures

```python
def fetch_current_rate() -> float | None:
    """Attempt to fetch 91-day T-bill rate from RBI. Returns decimal rate or None on failure."""

def get_rate_for_date(date: str, rates_csv: Path) -> float:
    """Return the most recent rate available on or before `date` from rates.csv.
    
    Falls back to DEFAULT_RATE if rates.csv is empty or missing.
    """
```

---

## Module 9: Writer (`pipeline/writer.py`)

Handles safe incremental appending to output CSVs.

### Design

Each metric output file is a flat CSV with a `date` and `symbol` column as the primary composite key. The writer:
1. Checks if the output file exists; if not, writes with header
2. Reads the existing file's `(date, symbol)` pairs
3. Appends only rows for dates/symbols not already present (deduplication guard)

This makes the pipeline fully idempotent — re-running on an already-processed date produces no duplicate rows.

### Options Chain Output

The processed options chain is written as **per-date per-symbol files** (not one monolithic file) to `outputs/options_chain/{SYMBOL}/date=YYYY-MM-DD.csv`. At 562K rows/day × 250 days/year ≈ 140M rows/year, a single appended file is unmanageable for both appending and downstream reads. Per-date files are trivially idempotent (write once, skip if exists), require no scan-on-append, and allow downstream consumers to `glob` by date range.

### Manifest

`processed_dates.json` tracks which `(date, symbol)` pairs have been successfully written — at `(date, symbol)` granularity, not date-only. This allows partial retries: if NIFTY succeeds and BANKNIFTY fails on the same date, the next run reprocesses only BANKNIFTY.

Manifest updates are atomic: write to `processed_dates.json.tmp` first, then `os.replace()` to the final path. On POSIX systems this is atomic at the filesystem level.

Note: `append_to_csv` uses a dedup guard (skip rows whose key_cols already exist) as a second line of defense against duplicates. This guard is load-bearing if the manifest write fails mid-run — document this dependency.

```python
def append_to_csv(df: pd.DataFrame, path: Path, key_cols: list[str]) -> int:
    """Append rows in df to path, skipping rows whose key_cols already exist.
    
    Returns count of rows appended.
    """

def load_manifest(path: Path) -> set[tuple[str, str]]:
    """Load set of (date, symbol) pairs already processed."""

def update_manifest(path: Path, new_entries: list[tuple[str, str]]) -> None:
    """Atomically write updated manifest: write .tmp then os.replace() to final path."""
```

---

## Module 10: Run Pipeline (`run_pipeline.py`)

Top-level orchestration script — this is the entry point run on a weekly schedule.

### Flow

```
1. Load Config
2. Load processed_dates manifest
3. Discover new date folders in NSEI-Data/
4. Fetch current risk-free rate
5. For each new_date in sorted(new_dates):
   For each symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY"]:
     a. load_day() → raw_df
     b. add_computed_iv() → iv_df
     c. compute_daily_rk() → rv_result
     d. extract_atm_iv() + compute_vrp() → vrp_result
     e. compute_ofi() → ofi_df
     f. compute_liquidity() → liquidity_df
     g. Append all outputs via writer
   Update manifest with new_date for all symbols
6. Log summary (dates processed, row counts, IV convergence rates, errors)
```

### Concurrent Run Guard

Acquire an exclusive file lock at the start of `run_pipeline.py` using `filelock.FileLock("pipeline.lock")`. If a lock cannot be acquired, log "pipeline already running" and exit immediately. This prevents two simultaneous runs (cron + manual) from corrupting output CSVs on concurrent append.

### Cron Invocation

```bash
# Run weekly at 08:00 on Mondays (processes the previous week's new daily folders)
0 8 * * 1 cd /path/to/project && python run_pipeline.py >> pipeline.log 2>&1
```

The script exits with code 0 on success, non-zero if any symbol-day failed. It logs the failure and continues to the next symbol-day rather than aborting the entire run.

### Logging

Log at three levels of granularity:
1. Per-pipeline-run summary (dates attempted, succeeded, failed)
2. Per-symbol-day stats: `rows_in`, `rows_after_clean`, `iv_attempted`, `iv_converged`, `iv_nan_zero_quote`, `iv_nan_expired`, `iv_nan_intrinsic`, `iv_nan_no_root`
3. Any exceptions with full tracebacks

Logs go to `pipeline.log` at project root.

---

## Testing Plan

### Test: `test_iv.py`

| Test | What It Checks |
|------|---------------|
| `test_bs_call_put_parity` | `C − P ≈ S·e^{−qT} − K·e^{−rT}` (dividend-adjusted parity) |
| `test_iv_roundtrip_call` | `compute_iv(bs_price(σ=0.20, call)) ≈ 0.20` to 1e-6 tolerance |
| `test_iv_roundtrip_put` | Same for put option |
| `test_iv_zero_bid` | Returns NaN when `bid_price = 0` |
| `test_iv_expired` | Returns NaN when `time_to_expiry ≤ 0` |
| `test_iv_expiry_day_fractional_t` | At 09:15 on expiry day, T > 0 and IV is finite |
| `test_iv_deep_otm_near_zero` | Returns NaN when mid_price < discounted intrinsic |

### Test: `test_realized_vol.py`

| Test | What It Checks |
|------|---------------|
| `test_rk_positive` | `realized_kernel(r) ≥ 0` for all valid inputs |
| `test_rk_h_zero_equals_rv` | With `H=0`, `realized_kernel(r, H=0) == np.dot(r, r)` |
| `test_rk_deterministic` | Hand-computed RK for a small (5-element) input vector, verified by formula |
| `test_rk_gbm` | On GBM path with known σ, recovered RK within 5% of σ²×Δt |
| `test_optimal_bandwidth_range` | For n=75, H is in [1, 20] |
| `test_overnight_increases_variance` | Non-zero overnight return strictly increases total RK |
| `test_rolling_rv_in_variance_space` | `rk_5d_ann == sqrt(mean(rk_daily_var[5]) × 365)` (not `mean(rk_ann_vol)`) |
| `test_forward_fill_isolation` | Forward-filled spot values do not appear in RK input (no zero log-returns from fills) |

### Test: `test_ofi.py`

| Test | What It Checks |
|------|---------------|
| `test_ofi_bid_up_three_case` | When `bid_price(t) > bid_price(t-1)`: contribution = `+bid_qty(t)` (not `Δbid_qty`) |
| `test_ofi_bid_same_three_case` | When `bid_price` unchanged: contribution = `bid_qty(t) − bid_qty(t-1)` |
| `test_ofi_bid_down_three_case` | When `bid_price(t) < bid_price(t-1)`: contribution = `−bid_qty(t-1)` |
| `test_ofi_ask_mirror` | Ask-side contributions mirror bid with sign reversal |
| `test_ofi_first_snapshot_nan` | First snapshot of day has OFI = NaN |
| `test_ofi_sign_matches_numerator` | Sign of `ofi_normalized` matches sign of `ofi_raw` |
| `test_ofi_expiry_key` | Contracts at same strike but different expiry are not collapsed |

### Test: `test_vrp.py`

| Test | What It Checks |
|------|---------------|
| `test_vrp_positive_when_iv_gt_rv` | IV=25%, RV=18% → `vrp_vol > 0`, `vrp_variance > 0` |
| `test_vrp_zero_when_equal` | IV = RK ann vol exactly → both VRP measures = 0 |
| `test_vrp_unit_consistency` | `vrp_variance` and `vrp_vol` have matching signs |
| `test_atm_strike_selection_nifty` | For NIFTY at 24100, ATM = 24100 (nearest 50) |
| `test_atm_tie_break` | Spot exactly between two strikes → rounds half-up consistently |
| `test_atm_iv_one_side_nan` | Call IV NaN, put IV valid → ATM IV = put IV (not NaN) |
| `test_constant_maturity_interpolation` | 30-day IV is between T1 and T2 values |
| `test_constant_maturity_extrapolation_flag` | Single-expiry case → `iv_30d_is_extrapolated = True` |

### Test: `test_pipeline_idempotency.py`

| Test | What It Checks |
|------|---------------|
| `test_rerun_same_date` | Running pipeline twice on same input → identical output CSVs (byte-for-byte) |
| `test_append_new_date` | Running on date 2 after date 1 → date 1 rows unchanged, date 2 appended |
| `test_partial_failure_retry` | NIFTY succeeds + BANKNIFTY raises → next run retries BANKNIFTY, no NIFTY duplicates |
| `test_rate_fetcher_fallback` | Mock RBI network failure → pipeline uses last rate from `rates.csv` |
| `test_rate_fetcher_no_csv` | No `rates.csv` → pipeline uses `DEFAULT_RATE` |

---

## Key Implementation Decisions

1. **Always recompute IV from bid-ask midpoint** — NSE-provided IV is 43% zero for illiquid strikes. Recomputing from midpoint ensures full coverage and consistency across the chain, at the cost of extra computation (~300K Brent's method calls per symbol-day). Each call is fast (<1ms) so total is acceptable.

2. **Realized kernel over simple 5-min RV** — The improvement in RMSE vs. simple 5-min RV is modest (5–10%) but the kernel provides a positivity guarantee and is the standard in academic VRP work. We store both for validation.

3. **Overnight return added to intraday RK** — Without this, RV systematically underestimates full-day variance relative to ATM IV (which prices overnight risk), creating an artificial positive VRP bias.

4. **Cont et al. best-bid-ask OFI over chain-level aggregate** — More microstructure-theoretic; captures per-strike directional pressure at the best quotes, not just aggregate volume imbalance. The `bid_qty` and `ask_qty` columns in the data support this definition directly.

5. **ATM IV via nearest-strike rounding** — Simpler and faster than delta-neutral ATM; appropriate for a daily dashboard where per-snapshot delta computation would be expensive (requires IV to be already computed).

6. **Daily median (not mean) for IV_30d** — Resistant to anomalous values at market open and close when spreads are wide and IV estimates are unreliable.

7. **Idempotent CSV appending via manifest** — `(date, symbol)` granularity manifest tracks individual symbol-day pairs to allow partial retries. Atomic manifest writes (`os.replace`) prevent corrupt state on mid-run crash.

8. **Calendar-365 annualization throughout** — IV uses `T = seconds / (365.25×86400)` and RV is annualized by `×365`. This keeps both sides of the VRP on the same time base. Mixing calendar-IV with trading-252-RV introduces a ~3% systematic error that would create a persistent VRP bias.

9. **Per-date options chain files** — The processed chain is written to `outputs/options_chain/{SYMBOL}/date=YYYY-MM-DD.csv` rather than one appended monolith. A year of NIFTY data at 562K rows/day × 250 days ≈ 140M rows in a single file is unmanageable for both appending (full dedup scan per run) and downstream reads (Split 02 vol surface fitting). Per-date files are trivially idempotent and naturally partitioned.

10. **Concurrent-run lock** — `filelock.FileLock("pipeline.lock")` prevents two simultaneous runs from corrupting output CSVs on concurrent append.
