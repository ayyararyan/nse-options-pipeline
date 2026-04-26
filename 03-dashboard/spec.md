# Split 03: Streamlit Analytics Dashboard

## Purpose

Build a multi-page interactive Streamlit application that reads all pre-computed parquet files from splits 01 and 02 and renders a polished, interactive analytics dashboard for NSE options data.

## Context

- **Project:** NSE Options Analytics Dashboard
- **Requirements:** `/Users/aryanayyar/Liquidity Metrics/requirements.md`
- **Interview transcript:** `/Users/aryanayyar/Liquidity Metrics/deep_project_interview.md`

## Inputs

All parquet files produced by splits 01 and 02:
- `options_chain.parquet` — IV by strike/expiry/date
- `realized_vol.parquet` — RV time series
- `vrp.parquet` — Variance Risk Premium time series
- `order_flow_imbalance.parquet` — OFI time series
- `liquidity_metrics.parquet` — bid-ask, OI, volume
- `essvi_surface.parquet` — eSSVI IV grid
- `sabr_surface.parquet` — SABR IV grid
- `essvi_params.parquet` — eSSVI fitted parameters
- `sabr_params.parquet` — SABR fitted parameters

## Outputs

- Running Streamlit web application (local, `streamlit run app.py`)

## Dashboard Structure

### Page 1: Overview — VRP & Realized Volatility
- **VRP time series:** Signed VRP (IV − RV) with zero-line, shaded positive/negative regions
- **RV time series:** Overlaid rolling windows (5d, 10d, 21d)
- **IV (ATM) time series:** ATM IV overlaid with RV for visual comparison
- **Controls:** Underlying selector (NIFTY / BANKNIFTY), date range picker, rolling window toggle

### Page 2: IV Structure — Smile, Skew & Term Structure
- **IV smile by strike:** IV vs. log-moneyness for a selected expiry and date (line chart with put/call markers)
- **IV term structure:** ATM IV across expiries for a selected date
- **Skew metric time series:** 25-delta risk reversal (IV_25P − IV_25C) or slope of smile
- **Controls:** Date picker, expiry selector, underlying selector

### Page 3: Vol Surfaces — eSSVI & SABR 3D
- **3D surface plot (eSSVI):** `plotly.graph_objects.Surface` — axes: log-moneyness, maturity, IV
- **3D surface plot (SABR):** Same axes, side-by-side or toggled
- **Time slider / animation:** Step through weekly fitted surfaces over the historical date range
- **Surface comparison toggle:** Show eSSVI and SABR on same 3D axes with transparency
- **Controls:** Date range slider, underlying selector, color scale picker

### Page 4: Liquidity & Order Flow
- **OFI time series:** Line chart of order flow imbalance, with zero-line
- **Bid-ask spread heatmap:** Strike × date heatmap of relative bid-ask spread
- **OI/Volume bar charts:** By strike for a selected expiry and date
- **Composite liquidity score time series:** Aggregate measure over time
- **Controls:** Underlying, expiry, date range selectors

## Key Components to Plan

### 1. App Skeleton & Navigation
- Streamlit multi-page setup (`pages/` directory or `st.navigation`)
- Shared sidebar with global controls (underlying selector, date range)
- Shared data loading layer with `@st.cache_data` for parquet reads
- Config file for parquet directory path

### 2. Data Loading Layer
- Centralized `data_loader.py` module — one function per parquet file
- `@st.cache_data` with TTL (e.g., 1 hour or on-demand refresh)
- Graceful handling of missing parquet files (show placeholder message)
- Filter helpers: by date range, underlying, expiry

### 3. Plotly Chart Components
- Reusable chart builder functions (one per chart type)
- Consistent color palette and theme across all pages
- All charts rendered via `st.plotly_chart(use_container_width=True)`
- Responsive layout (wide mode enabled)

### 4. 3D Surface Visualization (Page 3)
- `plotly.graph_objects.Surface` for eSSVI and SABR grids
- Animation frames for date-step time series (Plotly `frames` + slider)
- Colorscale: diverging for comparison, sequential for single surface
- Camera angle controls (azimuth, elevation)

### 5. Sidebar & Controls
- Global: underlying (NIFTY / BANKNIFTY), date range
- Page-local: expiry, rolling window, model toggle
- State persistence via `st.session_state`

## Technical Decisions (from interview)

| Decision | Value |
|----------|-------|
| Framework | Streamlit (multi-page) |
| Charts | Plotly (go.Figure, go.Surface, px.*) |
| Data reads | Parquet via pandas |
| Caching | `@st.cache_data` |
| Layout | Wide mode, sidebar controls |

## Uncertainty Flags for Deep-Plan to Resolve

1. **Streamlit multi-page approach** — `pages/` directory vs. `st.navigation` API (newer, more control)
2. **3D animation performance** — large surface grids may slow Plotly animation; may need to downsample or limit date range in slider
3. **Deployment target** — local only (`localhost`) vs. hosted (Streamlit Cloud, EC2)? Affects caching strategy and auth
4. **Refresh mechanism** — should the dashboard auto-detect new parquet files (file watcher) or require manual restart?
5. **Color theme** — dark vs. light mode; whether to use a custom CSS theme

## Dependencies

- **Requires from 01:** All core metric parquet files (schema must be finalized before page implementation)
- **Requires from 02:** Surface grid parquet files (schema must be finalized before page 3 implementation)
- **Can be partially developed in parallel** with 02 if parquet schemas are agreed upfront (pages 1, 2, 4 don't need surface data)
