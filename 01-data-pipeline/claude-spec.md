# Comprehensive Spec: NSE Options Data Pipeline & Core Metrics

## Project Context

NSE Options Analytics Dashboard — Split 01. This pipeline is the foundational data layer consumed by the volatility surface fitting module (Split 02) and the Streamlit dashboard (Split 03).

---

## Data Source

### Location and Structure

```
NSEI-Data/
  date=2026-04-20/
    NIFTY.csv
    BANKNIFTY.csv
    FINNIFTY.csv
  date=2026-04-21/
    ...
```

New `date=YYYY-MM-DD/` folders arrive daily. The pipeline processes each new folder incrementally and appends to the output CSVs.

### CSV Schema (per file)

| Column | Type | Description |
|--------|------|-------------|
| `captured_at` | ISO timestamp | When snapshot was captured (e.g., `2026-04-24T09:15:03`) |
| `exchange_timestamp` | String | NSE exchange time (e.g., `24-Apr-2026 09:14:03`) |
| `symbol` | String | Underlying (NIFTY, BANKNIFTY, FINNIFTY); may be blank for some rows |
| `expiry` | String | Expiry date (`DD-MM-YYYY`, e.g., `28-04-2026`) |
| `strike_price` | Float | Strike price |
| `option_type` | String | `CE` (call) or `PE` (put) |
| `open_interest` | Float | Open interest (lots) |
| `change_in_oi` | Float | Change in OI from previous session |
| `pchange_in_oi` | Float | % change in OI |
| `total_traded_volume` | Int | Total traded volume (contracts) |
| `implied_volatility` | Float | NSE-provided IV (43% are zero; we recompute from bid-ask) |
| `last_price` | Float | Last traded price |
| `change` | Float | Change from previous close |
| `pchange` | Float | % change |
| `bid_qty` | Int | Best bid quantity |
| `bid_price` | Float | Best bid price |
| `ask_qty` | Int | Best ask price quantity |
| `ask_price` | Float | Best ask price |
| `total_buy_quantity` | Int | Total buy quantity (whole book, order-level aggregate) |
| `total_sell_quantity` | Int | Total sell quantity (whole book) |
| `underlying_value` | Float | Spot price of underlying at snapshot time |

**Snapshot frequency:** ~1 minute throughout the trading day (~280 snapshots per day, 09:15–15:45).
**Rows per snapshot:** ~2,000 (all strikes × all expiries × CE + PE).
**Scale:** ~562K rows/day for NIFTY, ~368K rows/day for BANKNIFTY.

### Data Quality Issues Observed
- Rows with blank `symbol` field (filtering needed: keep only rows where `symbol` ∈ {`NIFTY`, `BANKNIFTY`, `FINNIFTY`})
- `implied_volatility` is 0 for ~43% of rows (illiquid strikes); we recompute IV from bid-ask
- Some rows have `bid_price = 0` and `ask_price = 0` (illiquid options, no market) — skip IV computation for these rows
- Late snapshots after 15:30 (up to 15:45) — likely closing session; handle or exclude from intraday RV

---

## Module 1: Data Ingestion & Normalization

### Responsibilities
- Discover all unprocessed `date=YYYY-MM-DD` folders
- Load and concatenate CSVs for each underlying per day
- Parse `captured_at` as UTC-aware datetime (IST = UTC+5:30)
- Filter out rows with blank symbol, keep only rows within market hours (09:15–15:30)
- Forward-fill missing `underlying_value` (in case a snapshot has NaN spot)
- Deduplicate: if multiple captures within the same minute, take the last

### Output
- In-memory DataFrame per day per underlying, ready for metric computation modules

---

## Module 2: IV Computation (Black-Scholes Inversion)

### Approach
For every row: compute `mid_price = (bid_price + ask_price) / 2`. If `mid_price ≤ 0` or `bid_price == 0`, mark `computed_iv = NaN`.

For valid rows, invert the Black-Scholes formula using **Brent's method** (scipy.optimize.brentq) to solve for σ:

```
BS_price(S, K, T, r, σ, option_type) = mid_price
```

Where:
- `S = underlying_value` (spot price)
- `K = strike_price`
- `T = (expiry_datetime - snapshot_datetime).days / 365` (calendar days, not trading days)
- `r` = risk-free rate (fetched weekly, see Module 6)
- `option_type` = CE → call, PE → put

NSE NIFTY options are **European-style, cash-settled** on the NIFTY 50 index — standard Black-Scholes applies (no early exercise premium needed).

### Edge Cases
- `T ≤ 0` (expired or same-day expiry): skip, mark `computed_iv = NaN`
- `mid_price < intrinsic_value`: IV computation will fail to converge; mark `NaN`
- Deep OTM options with `mid_price < 0.05`: IV highly unstable; optionally mark `NaN` or apply wide tolerance
- Brent's method search range: `[1e-6, 10.0]` (IV between 0.0001% and 1000% annualized)

### Output Columns Added
- `mid_price`: computed bid-ask midpoint
- `computed_iv`: Black-Scholes IV in decimal annualized form (e.g., 0.18 = 18%)
- `time_to_expiry`: T in calendar-year fractions

---

## Module 3: Realized Volatility (Realized Kernel)

### Data Extraction
From the options chain data, extract the underlying spot price series:
```python
spot = df.groupby('captured_at')['underlying_value'].first()
```
Resample to 5-minute bars (take last available value per 5-min window):
```python
spot_5m = spot.resample('5min').last().between_time('09:15', '15:30').dropna()
log_rets = np.log(spot_5m).diff().dropna()
```

### Realized Kernel Formula
Using Parzen (non-flat-top) kernel — guarantees non-negative estimates:

```python
def parzen_weights(H): ...
def optimal_h(r): ...   # H* = 3.5134 × ξ^(4/5) × n^(3/5)
def realized_kernel(r, H=None): ...   # symmetric autocovariance sum
```

**Bandwidth:** Adaptive (BNHLS optimal, H ≈ 3–8 for 75 obs/day). Conservative fallback: `H = ceil(0.5 × sqrt(n))`.

### Overnight Return
Add overnight squared return to intraday RK for full-day variance:
```python
r_overnight = log(open_today / close_yesterday)
rk_total = rk_intraday + r_overnight**2
```

### Annualization
```python
rk_annual_var = rk_daily * 252
rk_annual_vol = sqrt(rk_daily) * sqrt(252)
```

Rolling windows computed: 5-day, 10-day, 21-day (rolling average of daily RK, then annualize).

### Output per symbol: `{SYMBOL}_realized_vol.csv`
Columns: `date`, `rk_daily_var`, `rk_daily_vol`, `rk_ann_vol`, `rk_5d_ann`, `rk_10d_ann`, `rk_21d_ann`, `bandwidth_H`

---

## Module 4: Variance Risk Premium (VRP)

### ATM IV Extraction
For each snapshot and each expiry, identify the ATM strike:
- NIFTY: round `underlying_value` to nearest 50
- BANKNIFTY: round to nearest 100
- FINNIFTY: round to nearest 50 (to be confirmed from data)

Extract `computed_iv` for ATM call and put; use average as ATM IV.

### Constant-Maturity IV (30-day)
Interpolate (or extrapolate) ATM IV to a 30-day constant maturity using the available expiry dates.

At each snapshot timestamp, for the available expiries with T₁ < 30 days ≤ T₂:
```python
IV_30d = IV_T1 + (30 - T1) / (T2 - T1) * (IV_T2 - IV_T1)
```
Take the daily average (or end-of-day snapshot) of IV_30d as the daily ATM IV.

### VRP Computation
```python
vrp_variance = iv_ann_decimal**2 - rk_annual_var   # in variance points
vrp_vol      = iv_ann_decimal - sqrt(rk_annual_var)  # in vol points
```

Both flavors stored: variance-space VRP and vol-space VRP.

### Output per symbol: `{SYMBOL}_vrp.csv`
Columns: `date`, `atm_iv_30d`, `rk_ann_vol`, `vrp_variance`, `vrp_vol`

---

## Module 5: Order Flow Imbalance (OFI — Cont et al.)

### Definition
For each (strike, option_type) at consecutive snapshots t and t-1:

```
OFI(k, t) = Δbid_qty(k, t) × I[bid_price unchanged or up]
           − Δask_qty(k, t) × I[ask_price unchanged or down]
```

Where:
- `Δbid_qty = bid_qty(t) - bid_qty(t-1)`
- `Δask_qty = ask_qty(t) - ask_qty(t-1)`

Aggregate across all strikes and both CE/PE to get snapshot-level OFI:
```python
ofi_t = sum_k OFI(k, t)
```

The Cont et al. intuition: positive OFI = net buying pressure at the best quotes.

### Normalization
Normalize by total outstanding bid+ask quantity for comparability across underlyings:
```python
ofi_normalized = ofi_t / total_liquidity_t
```

### Daily Summary
Aggregate snapshot-level OFI to daily:
- Mean OFI, cumulative OFI, OFI std-dev

### Output per symbol: `{SYMBOL}_ofi.csv`
Columns: `date`, `snapshot_time`, `ofi_raw`, `ofi_normalized`, `daily_ofi_cumsum`, `daily_ofi_mean`

---

## Module 6: Liquidity Metrics

### Metrics per Strike per Snapshot
- `relative_spread = (ask_price - bid_price) / mid_price`
- `open_interest` (from data)
- `total_traded_volume` (from data)
- `depth = bid_qty + ask_qty` (total best-quote depth)

### Daily Aggregates (per symbol)
- `mean_atm_spread`: average relative spread for ATM strikes (within 2% of spot)
- `mean_chain_spread`: average relative spread across all liquid strikes
- `total_oi`: sum of open_interest across all strikes/expiries
- `total_volume`: sum of total_traded_volume

### Output per symbol: `{SYMBOL}_liquidity.csv`
Columns: `date`, `expiry`, `strike_price`, `option_type`, `mean_spread`, `total_oi`, `total_volume`, `atm_spread`

---

## Module 7: Processed Options Chain Output

Store the full options chain with computed IV for use by Split 02 (vol surface fitting).

### Output per symbol per day (appended): `{SYMBOL}_options_chain.csv`
Columns: all original columns + `mid_price`, `computed_iv`, `time_to_expiry`

---

## Module 8: Risk-Free Rate Fetcher

### Source
Fetch the 91-day T-bill / MIBOR rate from the RBI website or CCIL rates API on each weekly pipeline run.

**Fallback:** If the fetch fails, use the last stored rate (persisted in `rates.csv`) or a hardcoded default of `6.5%`.

### Output
- `rates.csv`: running history of `date`, `rate_decimal`

---

## Module 9: Weekly Update Script (`run_pipeline.py`)

### Behavior
1. Discover all `NSEI-Data/date=*` folders
2. Determine which dates are not yet in the output CSVs (via a manifest file or last-modified timestamp)
3. For each new date, for each symbol:
   a. Load and clean CSV (Module 1)
   b. Compute IV (Module 2)
   c. Extract spot series → compute RK RV (Module 3)
   d. Compute VRP (Module 4)
   e. Compute OFI (Module 5)
   f. Compute liquidity metrics (Module 6)
   g. Append all outputs to respective CSVs (Module 7)
4. Refresh risk-free rate (Module 8)
5. Log success/failure to `pipeline.log`

### Idempotency
- Check already-processed dates before re-running
- Appending to CSVs is guarded by a date deduplication check

### Logging
- Structured log: date processed, symbol, row counts, any errors, IV convergence rate

---

## Output Directory Structure

```
outputs/
  NIFTY_options_chain.csv
  NIFTY_realized_vol.csv
  NIFTY_vrp.csv
  NIFTY_ofi.csv
  NIFTY_liquidity.csv
  BANKNIFTY_options_chain.csv
  BANKNIFTY_realized_vol.csv
  BANKNIFTY_vrp.csv
  BANKNIFTY_ofi.csv
  BANKNIFTY_liquidity.csv
  FINNIFTY_options_chain.csv
  FINNIFTY_realized_vol.csv
  FINNIFTY_vrp.csv
  FINNIFTY_ofi.csv
  FINNIFTY_liquidity.csv
  rates.csv
  pipeline.log
  processed_dates.json   ← manifest tracking which dates have been processed
```

---

## Technical Stack

- **Python 3.11+**
- `pandas` — data manipulation, resampling
- `numpy` — realized kernel computation, vectorized operations
- `scipy.optimize.brentq` — Black-Scholes IV inversion
- `requests` — RBI rate fetching
- `pytest` — unit tests

---

## Testing Strategy

- **Realized kernel:** Synthetic GBM path with known variance; verify RK within 5% of true variance
- **RK positivity:** Assert output ≥ 0 for all inputs (Parzen kernel guarantee)
- **IV inversion:** Known BS price → verify recovered IV matches known input within 1e-6
- **IV edge cases:** zero bid/ask → NaN; expired contract → NaN; deep OTM near zero → NaN
- **VRP sign:** Synthetic case where IV > RV → VRP > 0; verify
- **OFI:** Manually crafted two-snapshot sequence → verify OFI formula
- **Pipeline idempotency:** Run twice on same input → output identical (no duplicate rows)
- **Overnight return inclusion:** Day with large gap → RK with overnight > RK without overnight
