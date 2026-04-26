# NSE Options Dashboard Requirements

## Project Description

Build a weekly-updating dashboard for NSE (National Stock Exchange) options chain data and its derived metrics. The dashboard should provide rich visualizations for options analytics, volatility analysis, and liquidity monitoring.

## Data

- NSE options chain data is available locally
- Dashboard should update on a weekly cadence

## Core Features Requested

### Time Series Metrics
- **IV-RV Variance Risk Premium (VRP):** Time series visualization of the spread between implied volatility and realized volatility
- **Realized Volatility (RV):** Rolling/historical realized volatility time series
- **Implied Volatility by Strike:** IV term structure and skew across strikes over time
- **Order Flow Imbalance (OFI):** Time series of buy/sell imbalance in the options order book

### 3D Volatility Surface Visualizations
- **eSSVI Surface:** 3D time series of the Extended SVI (eSSVI) implied volatility surface, parameterized and fitted to market data
- **SABR Surface:** 3D time series of the SABR model implied volatility surface

### Liquidity Metrics
- Time series of options liquidity metrics (bid-ask spreads, open interest, volume, etc.)

## Goals

- Build a visually polished, interactive dashboard
- Support exploration of options chain data and derived analytics
- Enable weekly updates with fresh data
- Provide a foundation that can be extended with additional metrics over time

## Constraints / Notes

- Data is present locally (NSE options chain)
- Dashboard should be interactive and visually appealing
- Open to suggestions for additional metrics and visualizations beyond what is listed
