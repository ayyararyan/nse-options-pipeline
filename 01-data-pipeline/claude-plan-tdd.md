# TDD Plan: NSE Options Data Pipeline & Core Metrics

**Testing framework:** `pytest` with fixtures (new project)
**Test directory:** `tests/`
**Run command:** `pytest tests/ -v`

This document maps each implementation section to tests that should be written and passing BEFORE the corresponding implementation is complete.

---

## Module 1: Configuration (`pipeline/config.py`)

### Tests to write first (`tests/test_config.py`)

```python
# Test: Config loads with all required fields (no missing keys)
# Test: Config.atm_increments has entries for all three symbols (NIFTY, BANKNIFTY, FINNIFTY)
# Test: Config.ann_factor is 365 (not 252 — calendar-day convention)
# Test: Config.timezone is "Asia/Kolkata"
# Test: invalid data_dir raises a useful error at load time
```

---

## Module 2: Ingestion (`pipeline/ingestion.py`)

### Tests to write first (`tests/test_ingestion.py`)

```python
# Test: load_day returns DataFrame with expected columns
# Test: load_day filters out rows with blank symbol
# Test: load_day drops rows outside market hours (09:15–15:30)
# Test: load_day parses captured_at as IST-aware datetime (tzinfo = Asia/Kolkata)
# Test: load_day parses expiry as an IST datetime at 15:30 on expiry date (not just a date)
# Test: load_day deduplicates: multiple rows at same (minute, strike, expiry, type) → one row
# Test: discover_new_dates returns only folders not in processed_dates
# Test: forward-fill of underlying_value only affects null values (non-null values unchanged)
# Test: RK spot extraction uses raw (non-forward-filled) underlying_value column
```

---

## Module 3: IV Computation (`pipeline/iv.py`)

### Tests to write first (`tests/test_iv.py`)

```python
# Test: bs_price call + put parity: C − P ≈ S·e^{−qT} − K·e^{−rT}
# Test: iv_roundtrip_call: compute_iv(bs_price(sigma=0.20, CE)) ≈ 0.20 to 1e-6
# Test: iv_roundtrip_put: compute_iv(bs_price(sigma=0.20, PE)) ≈ 0.20 to 1e-6
# Test: returns NaN when bid_price = 0
# Test: returns NaN when ask_price = 0
# Test: returns NaN when T ≤ 0 (contract expired)
# Test: returns finite IV at 09:15 on expiry day (T > 0 from fractional seconds formula)
# Test: returns NaN when mid_price < discounted_intrinsic (call: S·e^{−qT} − K·e^{−rT})
# Test: returns NaN when mid_price < discounted_intrinsic (put: K·e^{−rT} − S·e^{−qT})
# Test: IV convergence rate logged correctly (all four NaN buckets + converged)
```

---

## Module 4: Realized Volatility (`pipeline/realized_vol.py`)

### Tests to write first (`tests/test_realized_vol.py`)

```python
# Test: parzen_weights(H) has w[0] = 1.0 and w[H] ≈ 0 (monotone decreasing)
# Test: realized_kernel(r) ≥ 0 for all valid inputs (positivity guarantee)
# Test: realized_kernel(r, H=0) == np.dot(r, r)  [reduces to simple RV at H=0]
# Test: deterministic: small hand-verified input vector matches formula output to 1e-10
# Test: GBM path with known σ: recovered RK within 5% of σ²×Δt (statistical, many seeds)
# Test: optimal_bandwidth for n=75 returns H in [1, 20]
# Test: adding non-zero overnight return strictly increases total rk_daily_var
# Test: rolling RV computed in variance space: rk_5d_ann == sqrt(mean(rk_daily_var) × 365)
# Test: rk_ann_vol uses factor sqrt(365), NOT sqrt(252)
# Test: forward-filled spot values are NOT in the RK input series (no synthetic zero returns)
```

---

## Module 5: VRP (`pipeline/vrp.py`)

### Tests to write first (`tests/test_vrp.py`)

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

---

## Module 6: OFI (`pipeline/ofi.py`)

### Tests to write first (`tests/test_ofi.py`)

```python
# Test three-case bid formula:
#   Case 1: bid_price(t) > bid_price(t-1) → e_bid = +bid_qty(t)   [NOT Δbid_qty]
#   Case 2: bid_price(t) = bid_price(t-1) → e_bid = bid_qty(t) − bid_qty(t-1)
#   Case 3: bid_price(t) < bid_price(t-1) → e_bid = −bid_qty(t-1)

# Test three-case ask formula (mirror with sign):
#   Case 1: ask_price(t) < ask_price(t-1) → e_ask = +ask_qty(t)
#   Case 2: ask_price(t) = ask_price(t-1) → e_ask = ask_qty(t) − ask_qty(t-1)
#   Case 3: ask_price(t) > ask_price(t-1) → e_ask = −ask_qty(t-1)

# Test: OFI_chain = e_bid − e_ask summed across contracts
# Test: first snapshot of day has OFI = NaN (no prior snapshot to diff against)
# Test: sign of ofi_normalized matches sign of ofi_raw (normalization does not flip sign)
# Test: two contracts at same (strike, type) but different expiry are treated separately
#       (grouping key must include expiry — no cross-expiry collision)
# Test: NaN depth entries from appearing/disappearing strikes produce NaN OFI for that contract
#       (not zero, and not propagated to chain-level sum as zero)
```

---

## Module 7: Liquidity (`pipeline/liquidity.py`)

### Tests to write first (`tests/test_liquidity.py`)

```python
# Test: relative_spread = (ask − bid) / mid_price for valid quotes
# Test: relative_spread = NaN when mid_price ≤ 0
# Test: put_call_oi_ratio = NaN (or inf) when CE OI sum is zero
# Test: atm_spread_mean uses only strikes within 2% of spot
# Test: chain_spread_mean excludes zero-depth rows (bid_qty + ask_qty = 0)
# Test: all output columns present in liquidity CSV for any valid symbol-day input
```

---

## Module 8: Rates Fetcher (`pipeline/rates.py`)

### Tests to write first (`tests/test_rates.py`)

```python
# Test: fetch_current_rate returns a float between 0.01 and 0.20 on success
# Test: get_rate_for_date returns latest rate on or before given date
# Test: get_rate_for_date returns DEFAULT_RATE when rates.csv is empty
# Test: get_rate_for_date returns DEFAULT_RATE when rates.csv does not exist
# Test: network failure in fetch_current_rate → returns None (does not raise)
# Test: parsing error in RBI response → returns None (does not raise)
```

---

## Module 9: Writer (`pipeline/writer.py`)

### Tests to write first (`tests/test_writer.py`)

```python
# Test: append_to_csv creates file with header when path does not exist
# Test: append_to_csv appends rows not already present (key check)
# Test: append_to_csv skips rows whose key_cols already exist in file
# Test: update_manifest writes atomically (via .tmp + os.replace)
# Test: load_manifest reads back what update_manifest wrote
# Test: update_manifest on missing file creates it with the new entries
```

---

## Module 10: Pipeline Integration (`tests/test_pipeline_idempotency.py`)

```python
# Test: running pipeline twice on same date → output CSV byte-identical (idempotent)
# Test: running pipeline on date 2 after date 1 → date 1 rows unchanged, date 2 appended
# Test: NIFTY succeeds, BANKNIFTY raises → manifest records NIFTY success only →
#       next run reprocesses BANKNIFTY and does NOT duplicate NIFTY rows
# Test: concurrent lock prevents second process from running (mock or skip if OS-dependent)
# Test: RBI fetch fails → pipeline uses last rate from rates.csv and continues
# Test: rates.csv missing → pipeline uses DEFAULT_RATE and continues
```

---

## Test Fixture Strategy

```python
# conftest.py — shared fixtures

# tiny_day_df: minimal 3-snapshot options chain DataFrame with realistic column values
#   (use NIFTY, 2 strikes, 2 expiries, CE + PE, 2026-04-24)

# synthetic_spot_5m: pd.Series of 75 log-normal 5-min prices with known σ
#   (use fixed random seed for reproducibility)

# tmp_data_dir: temporary directory with NSEI-Data/date=2026-04-24/NIFTY.csv
#   (uses tiny_day_df written to disk)

# tmp_output_dir: empty temporary output directory
```
