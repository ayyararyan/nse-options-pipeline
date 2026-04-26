# Section 01: Project Setup

## Overview

This section creates the skeleton that every downstream section depends on. The work is purely structural: directory layout, dependency manifest, shared test fixtures, and package inits. No metric logic lives here.

**Depends on:** Nothing
**Blocks:** All other sections (02–10)

---

## Project Root

The project root for all file paths in this document and every downstream section is:

```
/Users/aryanayyar/Liquidity Metrics/01-data-pipeline/
```

All relative paths (e.g., `pipeline/config.py`, `tests/conftest.py`) resolve from this root.

---

## Tests First

There are no per-module unit tests owned by this section. The test cases for `config`, `ingestion`, `iv`, `realized_vol`, etc. are each owned by their respective sections (02–10).

What belongs here are the **shared fixtures** in `tests/conftest.py` that every downstream test module will import. Write stubs for all four fixtures now so later sections can reference them immediately.

```python
# tests/conftest.py

import pytest
import pandas as pd
import numpy as np
from pathlib import Path


@pytest.fixture
def tiny_day_df() -> pd.DataFrame:
    """Minimal 3-snapshot NIFTY options chain DataFrame.

    Contains 2 strikes (24100, 24150), 2 expiries, CE + PE option types,
    and date 2026-04-24. Column set matches the full pipeline schema:
    captured_at (IST-aware), symbol, expiry (IST datetime at 15:30),
    strike_price, option_type, bid_price, ask_price, bid_qty, ask_qty,
    open_interest, total_traded_volume, total_buy_quantity,
    total_sell_quantity, underlying_value.
    Use realistic numeric values; not all zeros.
    """
    ...


@pytest.fixture
def synthetic_spot_5m() -> pd.Series:
    """pd.Series of 75 log-normal 5-minute spot prices with known annualized vol.

    Use a fixed random seed for full reproducibility across test runs.
    Index: DatetimeTZDtype('Asia/Kolkata') from 09:15 to 15:25 on 2026-04-24.
    """
    ...


@pytest.fixture
def tmp_data_dir(tmp_path: Path, tiny_day_df: pd.DataFrame) -> Path:
    """Temporary directory mirroring NSEI-Data/date=2026-04-24/NIFTY.csv.

    Writes tiny_day_df to disk so ingestion tests can call load_day() against
    a real file path.
    Returns the root data dir (parent of date=... folders), not the date subfolder.
    """
    ...


@pytest.fixture
def tmp_output_dir(tmp_path: Path) -> Path:
    """Empty temporary output directory.

    Returns path to a writable directory; no subdirectories are pre-created.
    The writer module creates subdirectories lazily; tests should not assume
    any subdirectories exist on entry.
    """
    ...
```

A single smoke test is appropriate here to confirm the scaffolding works before any module is implemented:

```python
# tests/test_smoke.py

def test_pipeline_package_importable():
    """pipeline package and all module stubs are importable without error."""
    import pipeline  # noqa: F401


def test_fixtures_load(tiny_day_df, synthetic_spot_5m, tmp_data_dir, tmp_output_dir):
    """All four shared fixtures return non-None values of the expected type."""
    import pandas as pd
    assert isinstance(tiny_day_df, pd.DataFrame)
    assert isinstance(synthetic_spot_5m, pd.Series)
    assert tmp_data_dir.exists()
    assert tmp_output_dir.exists()
```

---

## Directory Structure to Create

Create every file and directory shown below. Files marked `(stub)` should contain only the module docstring and any bare `import` or `pass` needed for the file to be importable.

```
/Users/aryanayyar/Liquidity Metrics/01-data-pipeline/
├── pyproject.toml
├── run_pipeline.py              (stub)
├── pipeline/
│   ├── __init__.py              (empty or one-line docstring)
│   ├── config.py                (stub — owned by section-02)
│   ├── ingestion.py             (stub — owned by section-03)
│   ├── iv.py                    (stub — owned by section-04)
│   ├── realized_vol.py          (stub — owned by section-05)
│   ├── vrp.py                   (stub — owned by section-06)
│   ├── ofi.py                   (stub — owned by section-07)
│   ├── liquidity.py             (stub — owned by section-08)
│   ├── rates.py                 (stub — owned by section-09)
│   └── writer.py                (stub — owned by section-09)
├── tests/
│   ├── __init__.py              (empty)
│   ├── conftest.py              (fixtures — see above)
│   └── test_smoke.py            (smoke tests — see above)
└── outputs/                     (empty directory; subdirs created lazily by writer)
```

**Do NOT create** `NSEI-Data/` here — that directory is pre-existing raw data and must not be scaffolded. Do NOT pre-create `outputs/options_chain/NIFTY/` etc.; the writer module creates those subdirectories lazily at runtime.

The files `pipeline.log`, `processed_dates.json`, and `pipeline.lock` are runtime artifacts — do not create them during setup.

---

## `pyproject.toml`

The project uses **uv** as its runtime and package manager (`python-uv` in the project config). The test command is `uv run pytest`. Do not use poetry, pip-tools, or requirements.txt.

```toml
[project]
name = "nse-options-pipeline"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "pandas>=2.0",
    "numpy>=1.26",
    "scipy>=1.12",
    "requests>=2.31",
    "filelock>=3.13",
    "pytz>=2024.1",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Pin specific minimum versions based on API stability, not exact versions — this pipeline may run for months on a weekly cron.

---

## Recommended `.gitignore` Entries

```
.venv/
__pycache__/
*.pyc
outputs/
pipeline.log
pipeline.lock
processed_dates.json
processed_dates.json.tmp
.DS_Store
```

---

## Stub Module Pattern

Each `pipeline/*.py` stub should follow this pattern so the package is importable without import errors from the start:

```python
# pipeline/config.py  (example stub)
"""Configuration dataclass for the NSE options pipeline.

Implemented in section-02-config.
"""
```

No `pass`, no `def`, no imports needed — just a docstring. This keeps the stubs clean and ensures `import pipeline.config` does not raise at test collection time.

---

## Implementation Notes (Actual Build)

- `pyproject.toml` includes `[tool.hatch.build.targets.wheel] packages = ["pipeline"]` — required because project name (`nse-options-pipeline`) differs from package directory (`pipeline/`).
- `[tool.pytest.ini_options]` includes `pythonpath = ["."]` for portable test discovery.
- `tests/__init__.py` was omitted — making tests/ a package changes conftest discovery; pytest handles it correctly without it.
- `tiny_day_df` uses per-strike bid/ask/OI variation and snapshot-level bid_qty progression for non-degenerate OFI/spread tests.
- `tmp_data_dir` writes tz-naive `captured_at` (matching real NSE CSV format); `load_day()` (section-03) is responsible for localizing to IST.
- `synthetic_spot_5m` uses `dt = 5/(250*375)` (NSE 250-day, 375-min calendar); ~20% vol is approximate.
- Smoke test explicitly imports all 9 pipeline submodule stubs (not just the package root).
- **Files created:** `pyproject.toml`, `run_pipeline.py`, `pipeline/__init__.py`, `pipeline/{config,ingestion,iv,realized_vol,vrp,ofi,liquidity,rates,writer}.py`, `tests/conftest.py`, `tests/test_smoke.py`, `outputs/` (empty dir).
- **Tests:** 2 passed (`test_pipeline_package_importable`, `test_fixtures_load`).

## Definition of Done

This section is complete when:

1. `uv run pytest tests/test_smoke.py` passes (both smoke tests green)
2. All ten `pipeline/*.py` stubs are importable: `python -c "import pipeline.config; import pipeline.iv"` etc. completes without error
3. The four fixtures in `conftest.py` are defined as stubs (they may `raise NotImplementedError` or `return None` — they just must exist so later sections can add implementations without changing the fixture names)
4. `pyproject.toml` is present and `uv sync` completes without error

---

## What This Section Does NOT Own

- The `Config` dataclass body (section-02)
- Any test in `tests/test_config.py`, `tests/test_ingestion.py`, `tests/test_iv.py`, etc. (sections 02–10 each own their test file)
- The body of any `pipeline/*.py` module (sections 02–10)
- `run_pipeline.py` logic (section-10)
- The `outputs/options_chain/` subdirectory tree (created lazily by section-09's writer)
