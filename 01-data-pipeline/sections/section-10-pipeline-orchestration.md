# Section 10: Pipeline Orchestration (`run_pipeline.py`)

## Dependencies (Sections 1–9)

This section depends on all prior modules being implemented:

- **Section 1** — `pipeline/config.py`: `Config` dataclass
- **Section 2** — `pipeline/ingestion.py`: `load_day()`, `discover_new_dates()`
- **Section 3** — `pipeline/iv.py`: `add_computed_iv()`
- **Section 4** — `pipeline/realized_vol.py`: `compute_daily_rk()`, `compute_rolling_rv()`
- **Section 5** — `pipeline/vrp.py`: `extract_atm_iv()`, `compute_vrp()`
- **Section 6** — `pipeline/ofi.py`: `compute_ofi()`, `daily_ofi_summary()`
- **Section 7** — `pipeline/liquidity.py`: `compute_liquidity()`
- **Section 8** — `pipeline/rates.py`: `fetch_current_rate()`, `get_rate_for_date()`
- **Section 9** — `pipeline/writer.py`: `append_to_csv()`, `load_manifest()`, `update_manifest()`

---

## Tests First

**File:** `tests/test_pipeline_idempotency.py`

```python
"""
Integration tests for run_pipeline.py orchestration layer.

All tests use a minimal synthetic dataset (2 dates, 3 symbols, small row count)
written into a tmp_data_dir fixture. They invoke run_pipeline logic directly
(not via subprocess) to allow mocking.
"""
import pytest


def test_rerun_same_date(tmp_data_dir, tmp_output_dir, default_cfg):
    """Pipeline is fully idempotent: second run on same date produces no changes."""
    # Arrange: write synthetic NIFTY.csv for one date into tmp_data_dir
    # Act: call run_pipeline(cfg) twice
    # Assert: all output CSV files in tmp_output_dir are byte-for-byte identical after both runs
    ...


def test_append_new_date(tmp_data_dir, tmp_output_dir, default_cfg):
    """New date appended correctly; previously written rows are not modified."""
    # Arrange: tmp_data_dir has two date folders; first run processes date 1 only (manifest records it)
    # Act: second run processes date 2
    # Assert: date 1 rows in output CSVs are unchanged; date 2 rows are present
    ...


def test_partial_failure_retry(tmp_data_dir, tmp_output_dir, default_cfg, monkeypatch):
    """Partial failure is retried on next run without duplicating successful symbol rows.

    NIFTY succeeds, BANKNIFTY raises → manifest records NIFTY success only →
    next run reprocesses BANKNIFTY and does NOT duplicate NIFTY rows.
    """
    # Arrange: patch compute_daily_rk to raise for BANKNIFTY only on first run
    # Act: run pipeline (first run), then remove patch and run again
    # Assert: manifest contains NIFTY entry after first run; BANKNIFTY entry after second run;
    #         NIFTY output CSV has no duplicate rows
    ...


def test_concurrent_lock_prevents_double_run(tmp_data_dir, tmp_output_dir, default_cfg):
    """A held pipeline.lock causes the orchestrator to exit immediately."""
    # Arrange: acquire pipeline.lock manually
    # Act: attempt to run pipeline
    # Assert: pipeline exits immediately without processing any dates
    ...


def test_rate_fetcher_fallback(tmp_data_dir, tmp_output_dir, default_cfg, monkeypatch):
    """Network failure falls back to last stored rate in rates.csv."""
    # Arrange: mock fetch_current_rate to return None; populate rates.csv with a known rate
    # Act: run pipeline
    # Assert: IV computation used the known rate from rates.csv; pipeline completed successfully
    ...


def test_rate_fetcher_no_csv(tmp_data_dir, tmp_output_dir, default_cfg, monkeypatch):
    """Missing rates.csv falls back to DEFAULT_RATE without raising."""
    # Arrange: mock fetch_current_rate to return None; no rates.csv in tmp_output_dir
    # Act: run pipeline
    # Assert: pipeline completed; output files written; no exception raised
    ...
```

---

## Implementation

**File:** `run_pipeline.py` (project root)

```python
"""
Top-level pipeline orchestration script.

Run on a weekly cron schedule to process all new date folders in NSEI-Data/.
Acquires an exclusive file lock to prevent concurrent runs.
Processes each (date, symbol) pair incrementally; partial failures do not abort the run.

Cron entry:
    0 8 * * 1 cd /path/to/project && python run_pipeline.py >> pipeline.log 2>&1
"""

import logging
import sys
from pathlib import Path

import filelock

from pipeline.config import Config, load_default_config
from pipeline.ingestion import discover_new_dates, load_day
from pipeline.iv import add_computed_iv
from pipeline.realized_vol import compute_daily_rk, compute_rolling_rv
from pipeline.vrp import extract_atm_iv, compute_vrp
from pipeline.ofi import compute_ofi, daily_ofi_summary
from pipeline.liquidity import compute_liquidity
from pipeline.rates import fetch_current_rate, get_rate_for_date
from pipeline.writer import (
    append_to_csv,
    load_manifest,
    update_manifest,
    write_chain_file,
)

LOCK_PATH = Path("pipeline.lock")
LOG_PATH = Path("pipeline.log")

logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def run_pipeline(cfg: Config) -> int:
    """
    Execute the full pipeline for all new (date, symbol) pairs.

    Returns exit code: 0 if all pairs succeeded, 1 if any failed.
    Continues processing remaining pairs after a failure (partial-failure semantics).

    Steps:
      1. Load processed_dates manifest (outputs/processed_dates.json).
      2. Discover new date folders not in the manifest.
      3. Fetch current risk-free rate; fall back to rates.csv then DEFAULT_RATE.
      4. For each new_date (sorted ascending):
           For each symbol in cfg.symbols:
             a. load_day() → raw_df
             b. add_computed_iv() → iv_df
             c. compute_daily_rk() → rv_result
             d. extract_atm_iv() + compute_vrp() → vrp_result
             e. compute_ofi() → ofi_df
             f. compute_liquidity() → liquidity_df
             g. Append all outputs via writer
             h. On success: stage (date, symbol) for manifest update
             On exception: log full traceback, set exit_code=1, continue
      5. Update manifest with all successful (date, symbol) pairs.
      6. Log run summary.

    :param cfg: Fully populated Config instance.
    :return: 0 on full success, 1 if any symbol-day failed.
    """
    ...


def main() -> None:
    """Entry point: acquire lock, load config, run pipeline, release lock."""
    lock = filelock.FileLock(str(LOCK_PATH), timeout=0)
    try:
        with lock:
            cfg = load_default_config()
            exit_code = run_pipeline(cfg)
            sys.exit(exit_code)
    except filelock.Timeout:
        logger.warning("pipeline already running — exiting immediately")
        sys.exit(0)


if __name__ == "__main__":
    main()
```

---

## Orchestration Flow Detail

### 1. Manifest and Date Discovery

```
manifest_path = cfg.output_dir / "processed_dates.json"
processed = load_manifest(manifest_path)          # set of (date_str, symbol) tuples
new_dates  = discover_new_dates(cfg.data_dir, {d for d, _ in processed})
```

### 2. Rate Resolution

```
rates_csv = cfg.output_dir / "rates.csv"
rate_raw = fetch_current_rate()    # None on network/parse failure
if rate_raw is not None:
    append_rate(today_str, rate_raw, rates_csv)
rate = get_rate_for_date(today_str, rates_csv)    # falls back to DEFAULT_RATE if file missing
```

### 3. Per-Symbol-Day Processing (pseudocode)

```
successful = []
failed     = []
prev_close = {}    # {symbol: last_underlying_value}

for date_str in sorted(new_dates):
    date_dir = cfg.data_dir / f"date={date_str}"
    for symbol in cfg.symbols:
        if (date_str, symbol) in processed:
            continue                          # already done on a prior run
        try:
            raw_df      = load_day(date_dir, symbol, cfg)
            iv_df       = add_computed_iv(raw_df, rate, cfg)
            rv_result   = compute_daily_rk(raw_df, cfg, prev_close=prev_close.get(symbol))
            atm_iv_df   = extract_atm_iv(iv_df, cfg)
            vrp_df      = compute_vrp(atm_iv_df["iv_30d"], pd.Series({date_str: rv_result["rk_ann_vol"]}))
            ofi_df      = compute_ofi(iv_df)
            ofi_summary = daily_ofi_summary(ofi_df)
            liq_df      = compute_liquidity(iv_df, cfg)

            write_chain_file(iv_df, cfg.output_dir, symbol, date_str)
            append_to_csv(build_rv_row(...),  cfg.output_dir / f"{symbol}_realized_vol.csv",  key_cols=["date", "symbol"])
            append_to_csv(vrp_df,             cfg.output_dir / f"{symbol}_vrp.csv",            key_cols=["date", "symbol"])
            append_to_csv(ofi_df,             cfg.output_dir / f"{symbol}_ofi.csv",            key_cols=["date", "captured_at", "symbol"])
            append_to_csv(liq_df,             cfg.output_dir / f"{symbol}_liquidity.csv",      key_cols=["date", "symbol", "expiry"])

            prev_close[symbol] = raw_df["underlying_value"].dropna().iloc[-1]
            successful.append((date_str, symbol))
            logger.info(f"[{date_str}][{symbol}] OK — rows_in={len(raw_df)} ...")

        except Exception:
            failed.append((date_str, symbol))
            logger.exception(f"[{date_str}][{symbol}] FAILED")

update_manifest(manifest_path, successful)
```

### 4. Partial-Failure Semantics

- Per-symbol-day exceptions are caught individually; the outer loop continues.
- Manifest is updated **only with successful** `(date, symbol)` pairs.
- On the next run, failed pairs are discovered again because they are absent from the manifest.
- NIFTY-success + BANKNIFTY-failure on the same date: next run reprocesses only BANKNIFTY.

### 5. Exit Code

- `exit_code = 0` initially; set to `1` on any caught exception.
- This allows the cron monitor (or CI) to detect partial failures.

---

## Logging Schema

Three levels of detail written to `pipeline.log`:

| Level | Content |
|-------|---------|
| Run summary | Dates attempted, dates succeeded, dates failed, total elapsed time |
| Per-symbol-day | `rows_in`, `rows_after_clean`, `iv_attempted`, `iv_converged`, `iv_nan_zero_quote`, `iv_nan_expired`, `iv_nan_intrinsic`, `iv_nan_no_root` |
| Exceptions | Full traceback via `logger.exception()` |

---

## Cron Configuration

```bash
# Run weekly at 08:00 on Mondays (IST)
# Processes new daily folders accumulated over the prior trading week
0 8 * * 1 cd /path/to/project && python run_pipeline.py >> pipeline.log 2>&1
```

---

## File Paths Summary

| Path | Purpose |
|------|---------|
| `run_pipeline.py` | Entry point — orchestration script (project root) |
| `tests/test_pipeline_idempotency.py` | Integration tests for this section |
| `pipeline.lock` | Exclusive file lock; held for duration of run |
| `pipeline.log` | Rotating log file; all output appended here |
| `outputs/processed_dates.json` | Idempotency manifest; `(date, symbol)` pairs |
| `outputs/{SYMBOL}_realized_vol.csv` | Appended incrementally by writer |
| `outputs/{SYMBOL}_vrp.csv` | Appended incrementally by writer |
| `outputs/{SYMBOL}_ofi.csv` | Appended incrementally by writer |
| `outputs/{SYMBOL}_liquidity.csv` | Appended incrementally by writer |
| `outputs/options_chain/{SYMBOL}/date=YYYY-MM-DD.csv` | Per-date chain files (write-once) |
| `outputs/rates.csv` | Historical risk-free rates; appended each run |

---

## Key Implementation Invariants

**Idempotency guarantee:** The manifest check (`if (date_str, symbol) in processed: continue`) is the primary guard. `append_to_csv`'s key-col dedup check is the secondary guard. Both must be present: if the manifest write succeeds but an output CSV write is interrupted, the secondary guard prevents duplicates on retry.

**Atomic manifest write:** `update_manifest` writes to `processed_dates.json.tmp` then calls `os.replace()` — atomic on POSIX. A crash between the last CSV append and the `os.replace()` call leaves the manifest in the prior state; the next run reprocesses those pairs, and the CSV dedup guard suppresses duplicates.

**Forward-fill isolation:** `load_day()` forward-fills `underlying_value` for IV inputs only. The `raw_df` passed to `compute_daily_rk()` must be the pre-fill version to avoid injecting synthetic zero log-returns at fill boundaries.

**Calendar-365 consistency:** `add_computed_iv()` and `compute_daily_rk()` both use calendar-365 annualization. `compute_vrp()` subtracts them directly — no unit conversion needed. Do not substitute trading-252 RV here; it introduces a ~3% systematic gap.
