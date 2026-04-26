# Split 01: Data Pipeline & Core Metrics

## Purpose

Compute all derived analytics metrics from existing NSE options chain CSV files (NIFTY and BANKNIFTY). Data already exists as CSV — no format conversion to parquet is required. Outputs are also stored as CSV. This is the foundational layer — all other splits consume its outputs.

## Context

- **Project:** NSE Options Analytics Dashboard
- **Requirements:** `/Users/aryanayyar/Liquidity Metrics/requirements.md`
- **Interview transcript:** `/Users/aryanayyar/Liquidity Metrics/deep_project_interview.md`

## Inputs

- Raw NSE options chain CSV files (NIFTY + BANKNIFTY underlyings)
- 5-minute intraday return series (embedded in CSV or separate file — to be determined during deep-plan based on actual data schema)

## Outputs (CSV files, consumed by splits 02 and 03)

Data is already stored as CSV — no parquet conversion is needed. Derived metric outputs are also written as CSV files.

| File | Contents |
|------|----------|
| `options_chain_processed.csv` | Cleaned options chain rows with IV extracted per contract |
| `realized_vol.csv` | Realized kernel RV time series by underlying and rolling window |
| `vrp.csv` | Variance Risk Premium (IV − RV) time series |
| `order_flow_imbalance.csv` | OFI time series (buy/sell imbalance) |
| `liquidity_metrics.csv` | Bid-ask spread, open interest, volume by strike/expiry/date |

## Key Components to Plan

### 1. CSV Ingestion & Schema Normalization
- Discover actual column names/types in the NSE CSV files
- Handle multiple files (daily snapshots, weekly batches, etc.)
- Normalize column names, parse dates, cast types
- Handle missing data, duplicate rows, and expired contracts

### 2. Implied Volatility Extraction
- Black-Scholes inversion via numerical root-finder (Brent's method or Newton-Raphson)
- Requires: underlying spot price, strike, time to expiry, risk-free rate, option price
- Edge cases: deep ITM/OTM options, zero bid prices, very short maturities, negative IV
- Output IV for both calls and puts; use put-call parity to cross-validate

### 3. Realized Volatility — Realized Kernel
- **Estimator chosen:** Realized kernel (microstructure-robust) using 5-minute return series
- **Fallback:** If 5-min granularity is unavailable in data, implement Parkinson estimator (uses daily high-low) as secondary option
- Rolling windows: 5-day, 10-day, 21-day (configurable)
- Annualized output

### 4. Variance Risk Premium (VRP)
- VRP = ATM IV (30-day constant maturity) − Realized Volatility (matched window)
- Requires interpolation for constant-maturity IV
- Output as signed time series per underlying

### 5. Order Flow Imbalance (OFI)
- Definition depends on CSV data granularity:
  - If quote-level: Cont et al. definition (change in best bid/ask size)
  - If aggregated: volume-weighted buy/sell imbalance proxy
- Deep-plan should determine which definition applies to the actual data
- Normalize by total volume for comparability

### 6. Liquidity Metrics
- Relative bid-ask spread = (ask − bid) / midpoint
- Open interest by strike and expiry
- Volume by strike and expiry
- Aggregate liquidity score (optional composite)

### 7. Weekly Update Script
- Cron-compatible Python script (no interactive dependencies)
- Idempotent: safe to re-run on same week's data
- Incremental: only processes new CSVs, appends to existing output CSVs
- Logging to file for monitoring

## Technical Decisions (from interview)

| Decision | Value |
|----------|-------|
| Data format | CSV (NSE options chain) |
| Underlyings | NIFTY + BANKNIFTY |
| RV estimator | Realized kernel / 5-min returns |
| Storage | CSV files (data already in CSV; no format conversion needed) |
| Update mechanism | Weekly scheduled script (cron) |

## Uncertainty Flags for Deep-Plan to Resolve

1. **Actual CSV schema** — column names, available fields (high/low/5-min data?), file naming convention
2. **5-min data availability** — if not present, which fallback RV estimator to use
3. **OFI granularity** — quote-level vs. aggregated volume definition
4. **Risk-free rate source** — hardcoded MIBOR/OIS rate or fetched from external source?
5. **Calendar/expiry convention** — NSE weekly vs. monthly expiries, holiday calendar

## Dependencies

- **Requires:** Raw CSV data (pre-existing, local — no ingestion conversion needed)
- **Provides to 02:** `options_chain_processed.csv` — IV by strike/expiry/date
- **Provides to 03:** All output CSV files listed above
