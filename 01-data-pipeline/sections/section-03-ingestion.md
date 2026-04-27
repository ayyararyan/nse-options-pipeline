# section-03-ingestion

## Overview

This section implements `pipeline/ingestion.py`, which loads a single day's raw NSE options chain CSV for one symbol, cleans and normalizes it, and returns a DataFrame that all downstream metric modules consume. It also implements `discover_new_dates()` for the pipeline orchestration loop.

**Dependencies:** Requires `section-01-project-setup` (directory structure, `conftest.py` fixtures) and `section-02-config` (`Config` dataclass). All downstream sections (04 through 10) depend on the contract established here.

---

## Files to Create

- `/Users/aryanayyar/Liquidity Metrics/01-data-pipeline/pipeline/ingestion.py`
- `/Users/aryanayyar/Liquidity Metrics/01-data-pipeline/tests/test_ingestion.py`

---

## Tests First

Write all tests in `tests/test_ingestion.py` before implementing:

```python
# Test: load_day returns DataFrame with expected columns
#   Columns must include: captured_at, symbol, expiry, strike_price, option_type,
#   bid_price, ask_price, bid_qty, ask_qty, open_interest, total_traded_volume,
#   underlying_value, underlying_value_ffill, time_to_expiry, mid_price

# Test: load_day filters out rows with blank symbol
#   Input: rows with symbol="" or symbol=NaN mixed with valid NIFTY rows
#   Expected: only NIFTY rows remain

# Test: load_day drops rows outside market hours (09:15–15:30)
#   Input: rows at 09:00, 09:15, 15:30, 15:45 IST
#   Expected: rows at 09:00 and 15:45 are removed; 09:15 and 15:30 are kept

# Test: load_day parses captured_at as IST-aware datetime
#   Expected: df["captured_at"].dt.tzinfo is not None and resolves to "Asia/Kolkata"

# Test: load_day parses expiry as an IST datetime at 15:30 on expiry date (not just a date)
#   Input: expiry column value "24-04-2026"
#   Expected: expiry field == pd.Timestamp("2026-04-24 15:30:00", tz="Asia/Kolkata")

# Test: load_day deduplicates: multiple rows at same (minute, strike_price, option_type, expiry) → one row
#   Input: 3 rows at identical (captured_at_min, strike_price, option_type, expiry)
#   Expected: only the last row is kept (1 row in output)

# Test: discover_new_dates returns only folders not in processed_dates
#   Input: data_dir has date=2026-04-21 and date=2026-04-22; processed_dates = {"2026-04-21"}
#   Expected: returns ["2026-04-22"] only

# Test: forward-fill of underlying_value only affects null values (non-null values unchanged)
#   Input: non-null values in underlying_value column
#   Expected: underlying_value_ffill matches underlying_value for those rows

# Test: RK spot extraction uses raw (non-forward-filled) underlying_value column
#   Input: a row with underlying_value = NaN (followed by a valid row)
#   Expected: underlying_value remains NaN in that row; only underlying_value_ffill is filled
```

These tests use the `tiny_day_df` and `tmp_data_dir` fixtures from `conftest.py` (defined in section-01).

---

## Background and Context

### Data Source

Raw data lives at `{data_dir}/date=YYYY-MM-DD/{SYMBOL}.csv`. Each file contains the full NSE options chain at approximately 1-minute intervals during the trading session (09:15–15:30 IST), yielding ~280 snapshots and ~2,000 rows per snapshot. A NIFTY day file runs to ~562K rows.

### Columns Ingestion Touches

The full schema has more columns, but ingestion only reads and transforms these:

| Column | Type | Notes |
|---|---|---|
| `captured_at` | string → IST datetime | ISO timestamp of snapshot |
| `symbol` | string | NIFTY, BANKNIFTY, FINNIFTY — some rows are blank |
| `expiry` | string `DD-MM-YYYY` → IST datetime | Contract expiry date |
| `strike_price` | numeric | |
| `option_type` | string | CE or PE |
| `bid_price`, `ask_price` | numeric | Best bid/ask quote |
| `bid_qty`, `ask_qty` | numeric | Best bid/ask sizes |
| `open_interest` | numeric | |
| `total_traded_volume` | numeric | |
| `underlying_value` | numeric | Index spot price at snapshot time |

The column `implied_volatility` is present in the raw file but is **never used** — 43% of its values are zero for illiquid strikes. IV is always recomputed in Module 4 from `mid_price`.

### Config Fields Used

From the `Config` dataclass (section-02), ingestion uses:
- `cfg.timezone` — `"Asia/Kolkata"` for all datetime parsing
- `cfg.market_open` — `"09:15"` (inclusive lower bound for snapshot filter)
- `cfg.market_close` — `"15:30"` (inclusive upper bound; 15:30–15:45 closing auction rows are discarded)
- `cfg.symbols` — valid symbol set for row filtering
- `cfg.data_dir` — root of the `NSEI-Data/` tree

---

## Cleaning Steps (in order)

These steps must be applied in sequence:

1. **Parse `captured_at`** as an IST-aware datetime. Use `pd.to_datetime(...).dt.tz_localize("Asia/Kolkata")` or equivalent. All datetimes in the pipeline are IST-aware; never mix IST and UTC.

2. **Filter by symbol.** Keep only rows where `symbol` is in `cfg.symbols`. Blank rows (empty string or NaN) are removed here. NSE chain files contain rows for strikes that span multiple underlyings; rows with symbol not in the expected set must be dropped before any further processing.

3. **Filter by market hours.** Keep only snapshots with `captured_at` between `market_open` (09:15) and `market_close` (15:30), inclusive. The 15:30–15:45 closing-auction window generates anomalous spreads; discard those rows.

4. **Parse `expiry` to a full IST datetime at 15:30.** The expiry column is formatted as `DD-MM-YYYY`. Parse it and attach time 15:30 in IST. The result must be a timezone-aware `pd.Timestamp`, not a bare `date` object. This is critical for computing fractional `time_to_expiry` during the expiry-day session: if expiry is stored as a date, T collapses to zero for all of expiry-day morning, losing the entire high-information Thursday session for weekly NIFTY expiries.

5. **Cast numeric columns.** Apply `pd.to_numeric(..., errors='coerce')` to `strike_price`, `bid_price`, `ask_price`, `bid_qty`, `ask_qty`, `open_interest`, `total_traded_volume`, `underlying_value`. Invalid values become NaN.

6. **Deduplicate.** Where multiple rows share the same `(captured_at_min, strike_price, option_type, expiry)` — where `captured_at_min` is `captured_at` floored to the minute — keep only the last. This handles rare duplicate entries in source files.

7. **Forward-fill underlying_value (for IV inputs only).** Add a new column `underlying_value_ffill` that is a forward-filled copy of `underlying_value`. The original `underlying_value` column is left raw (NaN values preserved). Downstream modules choose which column to use:
   - **IV computation (Module 4):** use `underlying_value_ffill` as spot price S
   - **Realized kernel (Module 5):** use raw `underlying_value` (non-forward-filled) to avoid injecting zero log-returns from fill artifacts

8. **Add derived columns.**
   - `mid_price = (bid_price + ask_price) / 2`. Set to NaN where either leg is NaN or non-positive. Rows with NaN `mid_price` are retained (not dropped) — Module 4 will skip them during IV solving.
   - `time_to_expiry`: calendar-year fraction computed as `max(0, (expiry_ist - captured_at).total_seconds() / (365.25 × 86400))`. A value of 0 means the contract has expired at snapshot time; Module 4 will return NaN IV for those rows.

---

## Forward-Fill Contract (Critical Interface Decision)

`load_day` returns a DataFrame with **two spot columns**:

- `underlying_value` — raw, NaN values preserved exactly as they appear in the source CSV
- `underlying_value_ffill` — forward-filled copy, for use only in IV computation

Downstream modules **must** read from the correct column:
- Module 4 (`iv.py`) reads `underlying_value_ffill` as S
- Module 5 (`realized_vol.py`) reads raw `underlying_value` — never `underlying_value_ffill`

This contract is enforced by the test "RK spot extraction uses raw (non-forward-filled) underlying_value column." A single-column approach (forward-filling in place) would make it impossible to maintain this isolation.

---

## Function Signatures

File: `pipeline/ingestion.py`

```python
def load_day(date_dir: Path, symbol: str, cfg: Config) -> pd.DataFrame:
    """Load and clean one day's CSV for the given symbol.

    Reads {date_dir}/{symbol}.csv, applies all cleaning steps in order:
    timezone-aware captured_at parsing, symbol filter, market-hours filter,
    expiry-to-IST-datetime parsing, numeric coercion, deduplication,
    forward-fill isolation for underlying_value.

    Returns normalized DataFrame with added columns:
    - time_to_expiry: calendar-year fraction (max 0, can be 0 on expiry day post-close)
    - mid_price: (bid_price + ask_price) / 2, NaN where either leg is absent
    - underlying_value_ffill: forward-filled underlying_value for IV use only
    - underlying_value: raw spot, NaN preserved, for RK use only

    Rows with no valid bid or ask are retained but have mid_price = NaN.
    Logs row counts before and after each filtering step.
    """


def discover_new_dates(data_dir: Path, processed_dates: set[str]) -> list[str]:
    """Scan data_dir for date=YYYY-MM-DD folders and return those not in processed_dates.

    Returns a sorted list of date strings (YYYY-MM-DD format) whose corresponding
    folders exist in data_dir but are not present in processed_dates.
    Only folders matching the pattern 'date=YYYY-MM-DD' are considered.
    """
```

---

## Output Contract

Any downstream module receiving a DataFrame from `load_day` may assume the following columns are present and typed correctly:

| Column | Type |
|---|---|
| `captured_at` | `pd.Timestamp` (tz-aware, Asia/Kolkata) |
| `symbol` | `str` (always in cfg.symbols) |
| `expiry` | `pd.Timestamp` (tz-aware, Asia/Kolkata, time=15:30) |
| `strike_price` | `float` |
| `option_type` | `str` ("CE" or "PE") |
| `bid_price`, `ask_price` | `float` (may be NaN) |
| `bid_qty`, `ask_qty` | `float` (may be NaN) |
| `open_interest`, `total_traded_volume` | `float` (may be NaN) |
| `underlying_value` | `float` (raw; may be NaN) |
| `underlying_value_ffill` | `float` (forward-filled; fewer NaN than raw) |
| `time_to_expiry` | `float` (≥ 0, calendar-year fraction) |
| `mid_price` | `float` (may be NaN) |

Rows are not dropped based on NaN in `bid_price`, `ask_price`, or `mid_price` — those are handled per-module.

---

## Implementation Notes (Actual Build)

- Symbol filter uses the `symbol` argument (`df["symbol"] == symbol`), not `cfg.symbols` — more precise and avoids silently accepting wrong-symbol rows in edge cases.
- `df.sort_values("captured_at")` added before dedup and ffill — guards against out-of-order rows in source CSVs.
- Expiry parsing vectorized: `pd.to_datetime(...) + pd.Timedelta(hours=15, minutes=30)` + `.dt.tz_localize(_IST)` — no row-by-row lambda; handles NaT natively.
- `time_to_expiry` vectorized: `.dt.total_seconds().clip(lower=0.0)` — NaN propagates automatically.
- `captured_at_min` helper column dropped from DataFrame before returning.
- `pd.to_datetime(captured_at, format="ISO8601")` — explicit format for ~10x parse speedup on large files.
- **Files created:** `pipeline/ingestion.py`, `tests/test_ingestion.py`.
- **Tests:** 9 passed (test_ingestion.py) + 7 prior = 16 total.

## Implementation Notes (Original Plan)

- Do not use `pd.read_csv` with `parse_dates` for `captured_at` — parse it manually after loading to ensure the IST timezone is attached, not just parsed as naive UTC.
- The deduplication step floors `captured_at` to the minute (`captured_at.dt.floor("min")`) to generate the `captured_at_min` key column for the groupby. This column may be retained as a convenience for downstream snapshot-level indexing.
- For `expiry` parsing: `pd.to_datetime(df["expiry"], format="%d-%m-%Y")` gives a naive date; attach time and timezone explicitly. Do not rely on `infer_datetime_format`.
- `time_to_expiry` must be computed after expiry is a full IST datetime. Using only the date component (instead of datetime at 15:30) would make T = 0 for all snapshots on expiry day, discarding the entire trading session.
- Log at INFO level: file path loaded, initial row count, row count after each filter step (symbol filter, market hours filter, dedup).
