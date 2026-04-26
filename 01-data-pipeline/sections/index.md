<!-- PROJECT_CONFIG
runtime: python-uv
test_command: uv run pytest
END_PROJECT_CONFIG -->

<!-- SECTION_MANIFEST
section-01-project-setup
section-02-config
section-03-ingestion
section-04-iv-computation
section-05-realized-vol
section-06-vrp
section-07-ofi
section-08-liquidity
section-09-rates-writer
section-10-pipeline-orchestration
END_MANIFEST -->

# Implementation Sections Index
## NSE Options Data Pipeline & Core Metrics

---

## Dependency Graph

| Section | Depends On | Blocks | Parallelizable |
|---------|------------|--------|----------------|
| section-01-project-setup | — | all | No |
| section-02-config | 01 | 03–10 | No |
| section-03-ingestion | 02 | 04–10 | No |
| section-04-iv-computation | 03 | 06, 10 | Yes (with 05, 07, 08) |
| section-05-realized-vol | 03 | 06, 10 | Yes (with 04, 07, 08) |
| section-06-vrp | 04, 05 | 10 | No |
| section-07-ofi | 03 | 10 | Yes (with 04, 05) |
| section-08-liquidity | 03 | 10 | Yes (with 04, 05, 07) |
| section-09-rates-writer | 02 | 10 | Yes (with 04–08) |
| section-10-pipeline-orchestration | 04–09 | — | No |

---

## Execution Order

1. **section-01-project-setup** — directory structure, pyproject.toml, conftest.py, fixtures
2. **section-02-config** — Config dataclass and constants
3. **section-03-ingestion** — CSV loading, cleaning, normalization
4. **section-04-iv-computation, section-05-realized-vol, section-07-ofi, section-08-liquidity, section-09-rates-writer** — parallel after section-03
5. **section-06-vrp** — requires 04 and 05
6. **section-10-pipeline-orchestration** — requires all preceding sections

---

## Section Summaries

### section-01-project-setup
Directory scaffold, `pyproject.toml` with dependencies (pandas, numpy, scipy, requests, filelock, pytest), `conftest.py` with shared test fixtures (tiny_day_df, synthetic_spot_5m, tmp dirs).

### section-02-config
`pipeline/config.py` — `Config` dataclass with all tunable parameters (paths, symbols, market hours, annualization factor=365, ATM increments, dividend yield, timezone). Load from environment or defaults.

### section-03-ingestion
`pipeline/ingestion.py` — `load_day()`, `discover_new_dates()`. CSV parsing, symbol filtering, market-hours clipping, expiry parsing to IST datetime, dedup, forward-fill isolation for RK vs IV use.

### section-04-iv-computation
`pipeline/iv.py` — `bs_price()`, `compute_iv()`, `add_computed_iv()`. Black-Scholes European (with dividend yield q), Brent root-finding, fractional T, discounted intrinsic bound, convergence bucket logging.

### section-05-realized-vol
`pipeline/realized_vol.py` — `parzen_weights()`, `optimal_bandwidth()`, `realized_kernel()`, `compute_daily_rk()`, `compute_rolling_rv()`. Parzen BNHLS kernel, adaptive H, overnight squared return, calendar-365 annualization, rolling in variance-space.

### section-06-vrp
`pipeline/vrp.py` — `extract_atm_iv()`, `compute_vrp()`. ATM nearest-strike (half-up tie-break), ATM NaN guard, 30-day constant-maturity interpolation with `iv_30d_is_extrapolated` flag, VRP in both variance and vol space.

### section-07-ofi
`pipeline/ofi.py` — `compute_ofi()`, `daily_ofi_summary()`. Cont et al. three-case OFI (bid up/same/down), grouping by (expiry, strike, type), pivot-diff, chain-level aggregation, normalization by depth.

### section-08-liquidity
`pipeline/liquidity.py` — `compute_liquidity()`. Relative bid-ask spread, depth, daily aggregates (ATM spread mean, chain spread p50, total OI/volume, put-call OI ratio).

### section-09-rates-writer
`pipeline/rates.py` + `pipeline/writer.py` — RBI rate fetcher with fallback chain; idempotent CSV appender with key-dedup guard; atomic manifest write via `.tmp` + `os.replace`.

### section-10-pipeline-orchestration
`run_pipeline.py` — top-level orchestration: discovery loop, filelock concurrent-run guard, per-symbol-day metric computation calls, output appending, manifest update, structured logging.
