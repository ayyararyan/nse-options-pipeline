<!-- SPLIT_MANIFEST
01-data-pipeline
02-vol-surface-fitting
03-dashboard
END_MANIFEST -->

# Project Manifest: NSE Options Analytics Dashboard

## Overview

The project decomposes into three sequential, bounded splits. Each split has a clear purpose, well-defined inputs/outputs, and a cohesive scope suitable for deep planning.

---

## Split Structure

### `01-data-pipeline` — Data Ingestion & Core Metrics
**Purpose:** Transform raw NSE options CSV files into clean, analysis-ready parquet datasets with all derived metrics pre-computed.

**Inputs:**
- Raw NSE options chain CSVs (NIFTY + BANKNIFTY)
- 5-minute intraday return series (for realized kernel)

**Outputs (parquet files):**
- `options_chain.parquet` — cleaned options chain with IV extracted (BS inversion)
- `realized_vol.parquet` — realized kernel RV time series by underlying
- `vrp.parquet` — variance risk premium (IV − RV) time series
- `order_flow_imbalance.parquet` — OFI time series
- `liquidity_metrics.parquet` — bid-ask spread, OI, volume by strike/expiry

**Key components:**
- CSV ingestion and schema normalization
- Black-Scholes IV extraction (numerical root-finder, edge case handling)
- Realized kernel computation on 5-minute returns
- OFI aggregation (dependent on data granularity)
- Weekly update script (cron-compatible)

**Uncertainty flags:**
- If 5-min data is unavailable in CSVs, fallback to Parkinson/GK estimator
- OFI definition depends on data granularity (quote-level vs. aggregated volume)
- IV extraction edge cases: deep ITM/OTM, zero bids, expired contracts

---

### `02-vol-surface-fitting` — Volatility Surface Calibration
**Purpose:** Fit eSSVI and SABR models to extracted IV data for each expiry and date, storing calibrated parameters and surface grids for visualization.

**Inputs:**
- `options_chain.parquet` from 01 (IV by strike/expiry/date)

**Outputs (parquet files):**
- `essvi_params.parquet` — fitted eSSVI parameters per expiry/date
- `sabr_params.parquet` — fitted SABR parameters (α, β, ρ, ν) per expiry/date
- `essvi_surface.parquet` — dense IV grid (strike × maturity × date) from eSSVI
- `sabr_surface.parquet` — dense IV grid (strike × maturity × date) from SABR

**Key components:**
- eSSVI parameterization (raw SVI / natural parameterization, no-arbitrage enforcement)
- SABR calibration (Hagan approximation + numerical correction)
- Optimization routines (scipy, SLSQP or L-BFGS-B)
- Surface grid generation for 3D visualization
- Integration with weekly pipeline

**Uncertainty flags:**
- eSSVI flavor (raw SVI vs. jump-wings) and arbitrage constraints need explicit specification during deep-plan
- SABR approximation breakdown for short expiries / extreme strikes

---

### `03-dashboard` — Streamlit Analytics Dashboard
**Purpose:** Multi-page interactive Streamlit app that reads pre-computed parquet outputs and renders all analytics visualizations with filtering controls.

**Inputs:**
- All parquet files from 01 and 02

**Outputs:**
- Running Streamlit web application

**Pages:**
1. **Overview** — VRP and RV time series (Plotly line/area charts), underlying selector, rolling window control
2. **IV Structure** — IV by strike (smile/skew), term structure curves, date/expiry selectors
3. **Vol Surfaces** — 3D eSSVI and SABR surface animation over time (Plotly 3D surface + time slider)
4. **Liquidity & OFI** — OFI time series, bid-ask spread heatmap, OI/volume charts

**Key components:**
- Streamlit multi-page app skeleton and shared state
- Plotly chart components for all pages
- 3D surface + time-series animation (Plotly `go.Surface` with slider)
- Data loading layer with caching (`@st.cache_data`)
- Sidebar controls (date range, underlying, expiry pickers)

---

## Dependency Graph

```
01-data-pipeline
    │
    ├── schemas: options_chain.parquet (IV by strike/expiry/date)
    │
    └──► 02-vol-surface-fitting
              │
              ├── schemas: essvi_surface.parquet, sabr_surface.parquet
              │
              └──► 03-dashboard
                        │
                        └── reads all parquet from 01 + 02
```

**Dependency types:**
- `01 → 02`: **schemas** — 02 needs the parquet schema produced by 01
- `01 → 03`: **schemas** — 03 reads liquidity, VRP, RV, IV parquet from 01
- `02 → 03`: **schemas** — 03 reads surface grids from 02

---

## Execution Order

| Order | Split | Can start when |
|-------|-------|----------------|
| 1 | `01-data-pipeline` | Immediately |
| 2 | `02-vol-surface-fitting` | After 01 parquet schema is finalized |
| 3 | `03-dashboard` | After 01 and 02 parquet schemas are finalized |

> Note: 02 and 03 can be **partially parallelized** if parquet interface contracts are agreed upfront — page scaffolding in 03 can begin while 02 is being calibrated.

---

## /deep-plan Commands

Run in order:
```
/deep-plan @01-data-pipeline/spec.md
/deep-plan @02-vol-surface-fitting/spec.md
/deep-plan @03-dashboard/spec.md
```
