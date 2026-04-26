# Deep Project Interview Transcript

## Project: NSE Options Analytics Dashboard

---

## Requirements Summary (from requirements.md)

- NSE options chain data (CSV, NIFTY and BANKNIFTY underlyings)
- Weekly-updating analytics dashboard
- Core metrics: IV-RV Variance Risk Premium, Realized Volatility, Implied Volatility by strike, Order Flow Imbalance
- 3D time series visualizations: eSSVI and SABR implied volatility surfaces
- Liquidity metrics time series

---

## Interview Round 1

**Q: What format is your NSE options data in, and what underlying(s) does it cover?**
A: CSV files covering NIFTY and BANKNIFTY options chains.

**Q: Which dashboard framework do you prefer?**
A: Streamlit (recommended).

**Q: For the volatility surface fitting (eSSVI and SABR), do you have existing fitting code or should this be built from scratch?**
A: Build from scratch.

**Q: What is the weekly update mechanism you have in mind?**
A: A scheduled script (cron/task) — a separate Python script runs weekly to process new data; dashboard reads pre-computed outputs.

---

## Interview Round 2

**Q: For Realized Volatility, which estimator(s) do you want?**
A: Realized kernel / 5-min returns (microstructure-robust estimator using intraday tick/return data).

**Q: How should the dashboard be structured?**
A: Multi-page Streamlit (recommended):
- Page 1: Overview (VRP + RV time series)
- Page 2: IV Structure (skew by strike, term structure)
- Page 3: Vol Surfaces (eSSVI + SABR 3D)
- Page 4: Liquidity & OFI

**Q: Do you want the processed/computed data stored in a lightweight database or as flat files?**
A: Parquet files — fast columnar reads, pandas-native, easy to version by date.

---

## Key Decisions Captured

| Dimension | Decision |
|-----------|----------|
| Data source | NSE options CSV files (NIFTY + BANKNIFTY) |
| Dashboard framework | Streamlit (multi-page) |
| RV estimator | Realized kernel using 5-minute returns |
| Surface models | eSSVI and SABR — both built from scratch in Python |
| Storage layer | Parquet files (pre-computed, read by dashboard) |
| Update mechanism | Weekly cron/scheduled script produces new parquet; dashboard reads static outputs |
| Dashboard structure | 4 pages: Overview, IV Structure, Vol Surfaces, Liquidity & OFI |

---

## Uncertainty Flags

- **eSSVI calibration complexity**: Extended SVI has multiple parameterizations (raw SVI, natural SVI, jump-wings). The exact flavor and constraints (no-arbitrage conditions) need to be specified during /deep-plan.
- **5-min data availability**: Realized kernel requires intraday 5-minute return series. If the CSV data only has end-of-day snapshots, a fallback estimator (e.g., Parkinson or GK) should be considered as a secondary option.
- **OFI definition**: Order Flow Imbalance can be computed at the quote level (Cont et al.) or from aggregated volume data. The exact definition depends on the granularity of the CSV data.
- **IV extraction method**: Computing implied vol from options prices requires a numerical root-finder (Black-Scholes inversion). Need to handle edge cases (deep ITM/OTM, zero prices, expired contracts).

---

## Natural Project Boundaries

Three clear, separable components emerged:

1. **Data Pipeline & Core Metrics** — ingestion, cleaning, RV/VRP/OFI/liquidity computation, output to parquet.
2. **Volatility Surface Fitting** — eSSVI and SABR calibration from IV data, parameter storage.
3. **Streamlit Dashboard** — multi-page visualization app consuming parquet outputs.
