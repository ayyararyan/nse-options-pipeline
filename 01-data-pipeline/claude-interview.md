# Deep Plan Interview Transcript
## Split 01: Data Pipeline & Core Metrics

---

### Q1: What format is your NSE options data in, and what underlying(s) does it cover?

**Answer:** CSV files. Data is in `NSEI-Data/date=YYYY-MM-DD/{NIFTY,BANKNIFTY,FINNIFTY}.csv` (date-partitioned folders, one CSV per underlying per day).

**Data exploration findings:**
- Columns: `captured_at`, `exchange_timestamp`, `symbol`, `expiry`, `strike_price`, `option_type`, `open_interest`, `change_in_oi`, `pchange_in_oi`, `total_traded_volume`, `implied_volatility`, `last_price`, `change`, `pchange`, `bid_qty`, `bid_price`, `ask_qty`, `ask_price`, `total_buy_quantity`, `total_sell_quantity`, `underlying_value`
- Snapshot frequency: ~1-minute intraday snapshots (~280 per trading day, 09:15–15:45)
- ~2,000 rows per snapshot (all strikes × expiries × CE/PE)
- Single-day size: ~562K rows for NIFTY, ~368K rows for BANKNIFTY

---

### Q2: For IV computation, which price field will you use as the option price?

**Answer:** Bid-ask midpoint — compute `(bid_price + ask_price) / 2`.

**Note from data exploration:** NSE provides `implied_volatility` in the data (~57% non-zero), but the user chose to always compute from bid-ask midpoint for full consistency.

---

### Q3: For the Variance Risk Premium, which IV source?

**Answer:** ATM IV computed from own options chain data (not India VIX). Interpolate to 30-day constant maturity from the extracted ATM IV time series.

---

### Q4: Do you have 5-minute intraday prices in your data for the realized kernel?

**Answer:** Yes — the `underlying_value` column in the options chain CSV provides intraday spot prices at every snapshot timestamp. These will be extracted and resampled to 5-minute bars for the realized kernel computation.

---

### Q5: NSE already provides `implied_volatility` — should we use it or always compute our own?

**Answer:** Always compute our own IV from bid-ask midpoint. Full consistency across all strikes; avoids the 43% zero-IV problem in NSE's data.

---

### Q6: Which OFI definition?

**Answer:** Best-bid-ask level OFI (Cont et al.):
- Track changes in `bid_qty` and `ask_qty` between consecutive snapshots, per strike
- OFI per strike at time t = `Δbid_qty(t) × 1(bid_unchanged or up) − Δask_qty(t) × 1(ask_unchanged or down)`
- Aggregate across all strikes per snapshot for a single OFI time series

---

### Q7: Risk-free rate for Black-Scholes IV computation?

**Answer:** Fetch from RBI / external source on each weekly run. NSE options are European-style on the cash index (NIFTY 50 index options).

---

### Q8: Include FINNIFTY alongside NIFTY and BANKNIFTY?

**Answer:** Yes — include all three underlyings (NIFTY, BANKNIFTY, FINNIFTY).

---

### Q9: ATM strike definition for VRP computation?

**Answer:** Nearest strike to `underlying_value` (round spot to nearest strike increment: 50 for NIFTY, 100 for BANKNIFTY, to be determined for FINNIFTY).

---

### Q10: Output file structure?

**Answer:** One file per metric per underlying:
- `{SYMBOL}_options_chain.csv` — processed options chain with computed IV
- `{SYMBOL}_realized_vol.csv` — RK realized volatility time series
- `{SYMBOL}_vrp.csv` — variance risk premium time series
- `{SYMBOL}_ofi.csv` — order flow imbalance time series
- `{SYMBOL}_liquidity.csv` — liquidity metrics

---

### Q11: Data arrival pattern for weekly pipeline?

**Answer:** Daily files in date-partitioned folders (existing pattern: `date=YYYY-MM-DD/`). Pipeline should detect new date folders not yet processed and append to existing output CSVs (incremental, idempotent).

---

## Summary of Key Decisions

| Decision | Value |
|----------|-------|
| Data path | `NSEI-Data/date=YYYY-MM-DD/{SYMBOL}.csv` |
| Underlyings | NIFTY, BANKNIFTY, FINNIFTY |
| Snapshot frequency | ~1-min intraday (~280/day) |
| IV computation | Always from bid-ask midpoint via BS Brent root-finding |
| NSE-provided IV | Ignored (always recompute for consistency) |
| RV estimator | Realized kernel (Parzen, adaptive H) + overnight return |
| Intraday price source for RV | `underlying_value` column, resampled to 5-min |
| OFI definition | Cont et al. best-bid-ask: Δbid_qty − Δask_qty per strike, aggregated |
| ATM definition | Nearest strike to underlying_value |
| VRP IV source | Computed ATM IV (30-day constant maturity interpolated) |
| Risk-free rate | Fetched from RBI/external source weekly |
| Output format | CSV, one file per metric per underlying |
| Update mechanism | Incremental: process new date= folders, append to output CSVs |
