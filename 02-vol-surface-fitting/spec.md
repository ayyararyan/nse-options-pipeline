# Split 02: Volatility Surface Fitting

## Purpose

Calibrate eSSVI and SABR volatility models to extracted IV data, producing fitted surface parameter files and dense IV grids suitable for 3D visualization in the dashboard.

## Context

- **Project:** NSE Options Analytics Dashboard
- **Requirements:** `/Users/aryanayyar/Liquidity Metrics/requirements.md`
- **Interview transcript:** `/Users/aryanayyar/Liquidity Metrics/deep_project_interview.md`

## Inputs

- `options_chain.parquet` from split 01 — IV by strike/expiry/date

## Outputs (parquet files, consumed by split 03)

| File | Contents |
|------|----------|
| `essvi_params.parquet` | Fitted eSSVI parameters per expiry/date (χ, η, ρ or raw SVI a,b,σ,ρ,m) |
| `sabr_params.parquet` | Fitted SABR parameters (α, β, ρ, ν) per expiry/date |
| `essvi_surface.parquet` | Dense IV grid (log-moneyness × maturity × date) from eSSVI |
| `sabr_surface.parquet` | Dense IV grid (log-moneyness × maturity × date) from SABR |

## Key Components to Plan

### 1. eSSVI (Extended SVI) Calibration
- **Model family:** SVI (Gatheral 2004) extended to full surface (eSSVI) via Gatheral & Jacquier (2014)
- Deep-plan should choose parameterization flavor: raw SVI, natural parameterization, or jump-wings
- **No-arbitrage constraints:** Calendar spread no-arbitrage (total variance must be non-decreasing in maturity) and butterfly no-arbitrage (density must be non-negative)
- Optimization: scipy SLSQP or L-BFGS-B with inequality constraints
- Slice-by-slice calibration (per expiry) followed by joint surface smoothing
- Grid generation: dense log-moneyness grid (e.g., −3σ to +3σ in ~100 steps) per expiry

### 2. SABR Model Calibration
- **SABR parameters:** α (ATM vol level), β (CEV exponent, typically fixed at 0.5 or 1.0), ρ (correlation), ν (vol of vol)
- **Implied vol formula:** Hagan et al. (2002) approximation; consider Obloj (2008) correction for accuracy at extreme strikes
- Fix β upfront (convention: 0.5 for NSE equity options) or let deep-plan determine whether to calibrate it
- Per-expiry calibration using scipy minimize; warm-start with previous date's parameters
- Grid generation: same log-moneyness grid as eSSVI for consistency

### 3. Surface Grid Generation
- For each calibrated expiry and date, evaluate the model on a dense (strike, maturity) grid
- Store as structured parquet for efficient Plotly 3D surface rendering
- Grid resolution: configurable (default 50×50 or 100×100 log-moneyness × maturity)

### 4. Integration with Weekly Pipeline
- Called after `01-data-pipeline` script completes
- Incremental: calibrate only new dates, append to existing parameter parquets
- Handle calibration failures gracefully (log and skip, use previous day's parameters as fallback)

## Technical Decisions (from interview)

| Decision | Value |
|----------|-------|
| Surface models | eSSVI + SABR |
| Fitting approach | Build from scratch in Python |
| Optimization | scipy (SLSQP / L-BFGS-B) |
| Storage | Parquet (same layer as 01) |
| Update cadence | Weekly, incremental |

## Uncertainty Flags for Deep-Plan to Resolve

1. **eSSVI parameterization flavor** — raw SVI vs. natural vs. jump-wings (affects constraint formulation)
2. **β in SABR** — fix at 0.5 (standard for equity options) or calibrate as free parameter?
3. **Short-expiry handling** — SABR approximation breaks down near zero maturity; need strategy for weekly expiries
4. **Data quality gating** — minimum number of valid strikes per expiry to attempt calibration (skip slice if too sparse)
5. **Calendar arbitrage enforcement** — soft penalty vs. hard constraint in optimizer

## Dependencies

- **Requires from 01:** `options_chain.parquet` schema (log-moneyness or strike, IV, expiry date, trade date)
- **Provides to 03:** `essvi_surface.parquet`, `sabr_surface.parquet`, `essvi_params.parquet`, `sabr_params.parquet`
