# Section 09: Rates Fetcher + Writer (`pipeline/rates.py` + `pipeline/writer.py`)

## Overview

This section covers two modules that are simple individually but critical for pipeline correctness:

- **`pipeline/rates.py`** — fetches the current 91-day T-bill risk-free rate from the RBI Data Warehouse API with a three-tier fallback chain
- **`pipeline/writer.py`** — idempotent CSV appender with key-dedup guard and atomic manifest write

Both are called by `run_pipeline.py` (section-10). Neither depends on any other pipeline metric module.

---

## Part A: `pipeline/rates.py`

### Dependencies

- **Consumed by:** `run_pipeline.py` (Module 10) — calls `get_rate_for_date()` once per pipeline run
- **Rate passed to:** `add_computed_iv()` in `pipeline/iv.py` (Module 3)
- **References:** `Config.default_rate` (Module 1) — the hardcoded fallback value
- **Output file:** `outputs/rates.csv` — appended on each successful fetch; columns: `date`, `rate`

### Tests First

**File to create:** `tests/test_rates.py`

```python
"""Tests for pipeline/rates.py — Rates Fetcher.

Run with: pytest tests/test_rates.py -v
"""

import pytest
import pandas as pd
from pathlib import Path
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_rates_csv(tmp_path):
    """A minimal rates.csv with two entries."""
    path = tmp_path / "rates.csv"
    df = pd.DataFrame({
        "date": ["2026-04-14", "2026-04-21"],
        "rate": [0.068, 0.0675],
    })
    df.to_csv(path, index=False)
    return path


@pytest.fixture
def empty_rates_csv(tmp_path):
    """An empty rates.csv (header only, no data rows)."""
    path = tmp_path / "rates.csv"
    pd.DataFrame(columns=["date", "rate"]).to_csv(path, index=False)
    return path


# ---------------------------------------------------------------------------
# fetch_current_rate
# ---------------------------------------------------------------------------

def test_fetch_current_rate_returns_float_in_valid_range():
    """fetch_current_rate returns a float between 0.01 and 0.20 on success."""
    from pipeline.rates import fetch_current_rate

    with patch("pipeline.rates.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"rate": 6.75}
        mock_get.return_value = mock_resp

        rate = fetch_current_rate()

    assert rate is not None
    assert isinstance(rate, float)
    assert 0.01 <= rate <= 0.20, f"Rate {rate} outside expected range [0.01, 0.20]"


def test_fetch_current_rate_network_failure_returns_none():
    """Network error in fetch_current_rate → returns None (does not raise)."""
    import requests
    from pipeline.rates import fetch_current_rate

    with patch("pipeline.rates.requests.get", side_effect=requests.exceptions.ConnectionError):
        result = fetch_current_rate()

    assert result is None


def test_fetch_current_rate_parsing_error_returns_none():
    """Malformed RBI response → fetch_current_rate returns None (does not raise)."""
    from pipeline.rates import fetch_current_rate

    with patch("pipeline.rates.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("No JSON object could be decoded")
        mock_get.return_value = mock_resp

        result = fetch_current_rate()

    assert result is None


def test_fetch_current_rate_http_error_returns_none():
    """Non-200 HTTP status → fetch_current_rate returns None (does not raise)."""
    from pipeline.rates import fetch_current_rate

    with patch("pipeline.rates.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_get.return_value = mock_resp

        result = fetch_current_rate()

    assert result is None


# ---------------------------------------------------------------------------
# get_rate_for_date
# ---------------------------------------------------------------------------

def test_get_rate_for_date_returns_latest_on_or_before_date(tmp_rates_csv):
    """get_rate_for_date returns the most recent rate on or before the given date."""
    from pipeline.rates import get_rate_for_date

    rate = get_rate_for_date("2026-04-21", tmp_rates_csv)
    assert rate == pytest.approx(0.0675)


def test_get_rate_for_date_uses_earlier_rate_when_date_between_entries(tmp_rates_csv):
    """get_rate_for_date uses the most recent prior entry when no exact match exists."""
    from pipeline.rates import get_rate_for_date

    rate = get_rate_for_date("2026-04-16", tmp_rates_csv)
    assert rate == pytest.approx(0.068)


def test_get_rate_for_date_empty_csv_returns_default(empty_rates_csv):
    """get_rate_for_date returns DEFAULT_RATE when rates.csv is empty."""
    from pipeline.rates import get_rate_for_date, DEFAULT_RATE

    rate = get_rate_for_date("2026-04-21", empty_rates_csv)
    assert rate == pytest.approx(DEFAULT_RATE)


def test_get_rate_for_date_missing_csv_returns_default(tmp_path):
    """get_rate_for_date returns DEFAULT_RATE when rates.csv does not exist."""
    from pipeline.rates import get_rate_for_date, DEFAULT_RATE

    missing_path = tmp_path / "rates.csv"  # does not exist
    rate = get_rate_for_date("2026-04-21", missing_path)
    assert rate == pytest.approx(DEFAULT_RATE)


def test_get_rate_for_date_no_prior_entry_returns_default(tmp_rates_csv):
    """get_rate_for_date returns DEFAULT_RATE when all CSV dates are after the query date."""
    from pipeline.rates import get_rate_for_date, DEFAULT_RATE

    rate = get_rate_for_date("2026-01-01", tmp_rates_csv)
    assert rate == pytest.approx(DEFAULT_RATE)


def test_default_rate_is_0065():
    """DEFAULT_RATE constant is 0.065 (6.5%)."""
    from pipeline.rates import DEFAULT_RATE
    assert DEFAULT_RATE == pytest.approx(0.065)
```

### Implementation

**File:** `pipeline/rates.py`

```python
"""pipeline/rates.py — Risk-free rate fetcher for Black-Scholes IV computation.

Fetching hierarchy (most-to-least preferred):
  1. Live RBI Data Warehouse API
  2. Last stored rate in outputs/rates.csv
  3. Hardcoded DEFAULT_RATE = 0.065
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

DEFAULT_RATE: float = 0.065

# Update these constants if the RBI endpoint changes.
_RBI_API_URL: str = "https://api.rbi.org.in/api/v1/tbill/91d/latest"  # placeholder
_RATE_KEY: str = "rate"  # key in JSON response that holds the percentage rate


def fetch_current_rate() -> float | None:
    """Fetch 91-day T-bill rate from RBI. Returns decimal rate or None on any failure."""
    ...


def get_rate_for_date(date: str, rates_csv: Path) -> float:
    """Return most recent rate on or before `date` from rates_csv, else DEFAULT_RATE."""
    ...


def append_rate(date: str, rate: float, rates_csv: Path) -> None:
    """Idempotently append (date, rate) to rates_csv, creating it with header if absent."""
    ...
```

**Implementation notes:**
- Use `requests` for HTTP. Wrap the entire fetch in a broad `try/except Exception` to guarantee `fetch_current_rate` never raises.
- For `get_rate_for_date`: read the CSV with `pd.read_csv`, parse the `date` column, filter to rows `<= date`, and return the rate of the latest matching row. If `FileNotFoundError` is raised or the filtered frame is empty, return `DEFAULT_RATE`.
- The RBI API endpoint is subject to change; encapsulate the URL in module-level constants so they are easy to update.
- Log every fetch attempt (success with rate value, or failure with exception class and message) at INFO level.
- `append_rate` skips the append if `date` already in the CSV (idempotent).

---

## Part B: `pipeline/writer.py`

### Tests First

**File to create:** `tests/test_writer.py`

```python
# tests/test_writer.py

import pytest
import pandas as pd
from pathlib import Path
from pipeline.writer import append_to_csv, update_manifest, load_manifest


def test_append_creates_file_with_header(tmp_path):
    """append_to_csv creates file with header when path does not exist."""
    path = tmp_path / "out.csv"
    df = pd.DataFrame({"date": ["2026-04-21"], "symbol": ["NIFTY"], "value": [0.2]})
    append_to_csv(df, path, key_cols=["date", "symbol"])
    assert path.exists()
    result = pd.read_csv(path)
    assert list(result.columns) == ["date", "symbol", "value"]
    assert len(result) == 1


def test_append_appends_new_rows(tmp_path):
    """append_to_csv appends rows not already present by key."""
    path = tmp_path / "out.csv"
    df1 = pd.DataFrame({"date": ["2026-04-21"], "symbol": ["NIFTY"], "value": [0.2]})
    df2 = pd.DataFrame({"date": ["2026-04-22"], "symbol": ["NIFTY"], "value": [0.21]})
    append_to_csv(df1, path, key_cols=["date", "symbol"])
    append_to_csv(df2, path, key_cols=["date", "symbol"])
    result = pd.read_csv(path)
    assert len(result) == 2


def test_append_skips_duplicate_keys(tmp_path):
    """append_to_csv skips rows whose key_cols already exist in file."""
    path = tmp_path / "out.csv"
    df = pd.DataFrame({"date": ["2026-04-21"], "symbol": ["NIFTY"], "value": [0.2]})
    append_to_csv(df, path, key_cols=["date", "symbol"])
    append_to_csv(df, path, key_cols=["date", "symbol"])  # second write same key
    result = pd.read_csv(path)
    assert len(result) == 1  # no duplicates


def test_update_manifest_writes_atomically(tmp_path):
    """update_manifest writes via .tmp + os.replace — no partial writes visible."""
    path = tmp_path / "processed_dates.json"
    update_manifest(path, [("2026-04-21", "NIFTY")])
    assert path.exists()
    assert not (tmp_path / "processed_dates.json.tmp").exists()


def test_load_manifest_roundtrip(tmp_path):
    """load_manifest reads back exactly what update_manifest wrote."""
    path = tmp_path / "processed_dates.json"
    entries = [("2026-04-21", "NIFTY"), ("2026-04-21", "BANKNIFTY")]
    update_manifest(path, entries)
    result = load_manifest(path)
    assert set(result) == set(entries)


def test_update_manifest_creates_file_when_missing(tmp_path):
    """update_manifest on missing file creates it with the new entries."""
    path = tmp_path / "processed_dates.json"
    assert not path.exists()
    update_manifest(path, [("2026-04-21", "NIFTY")])
    assert path.exists()
    result = load_manifest(path)
    assert ("2026-04-21", "NIFTY") in result
```

### Implementation

**File:** `pipeline/writer.py`

```python
"""pipeline/writer.py — Idempotent CSV appender and atomic manifest writer."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd


def append_to_csv(df: pd.DataFrame, path: Path, key_cols: list[str]) -> int:
    """Append rows from df to path that are not already present by key_cols.

    Creates the file with header if it does not exist.
    Returns the number of rows actually written (0 if all were duplicates).
    """
    ...


def load_manifest(path: Path) -> set[tuple[str, str]]:
    """Load processed (date, symbol) pairs from the manifest JSON.

    Returns an empty set if the file does not exist.
    """
    ...


def update_manifest(path: Path, new_entries: list[tuple[str, str]]) -> None:
    """Add new_entries to the manifest and write atomically via .tmp + os.replace.

    Reads existing entries, merges with new_entries, writes to {path}.tmp,
    then calls os.replace({path}.tmp, path).
    """
    ...


def write_chain_file(df: pd.DataFrame, output_dir: Path, symbol: str, date_str: str) -> None:
    """Write per-date options chain CSV to outputs/options_chain/{symbol}/date={date_str}.csv.

    Creates parent directories if they do not exist. Overwrites any existing file
    for the same (symbol, date) — per-date chain files are write-once idempotent.
    """
    ...
```

**Implementation notes:**

- `append_to_csv`: read existing file (if present) into a DataFrame, compute the set of existing key tuples, filter df to rows whose keys are not in the existing set, then write `mode='a'` with `header=False`. If the file does not exist, write with `header=True`.
- `update_manifest`: write to `{path}.tmp` first, then call `os.replace(str(tmp_path), str(path))`. This is atomic on POSIX — no partial writes are visible to concurrent readers.
- `write_chain_file`: file path is `output_dir / "options_chain" / symbol / f"date={date_str}.csv"`. Create parent with `mkdir(parents=True, exist_ok=True)`.
- Never call `os.remove` on `.tmp` files — `os.replace` handles cleanup.
