# Opus Plan Review: NSE Options Data Pipeline

## Summary of Key Findings

The plan's overall shape is sound. There are several **methodology bugs that will produce wrong numbers**, one **architectural decision (monolithic options-chain CSV) that will break under a year of data**, and a **unit mismatch in the headline VRP metric**. Fix the items in Section A before any code is written.

---

## A. Correctness Bugs (Must Fix)

### A1. OFI formula is an oversimplification of Cont/Kukanov/Stoikov (2014)

The plan's `Δqty × indicator` collapses the price-improvement case incorrectly — when the bid moves up, the correct contribution is the new size, not the change in size.

Correct three-case definition:
- If `bid_price(t) > bid_price(t-1)`: `e_bid = +bid_qty(t)` (full new size enters at the new better price)
- If `bid_price(t) = bid_price(t-1)`: `e_bid = bid_qty(t) − bid_qty(t-1)`
- If `bid_price(t) < bid_price(t-1)`: `e_bid = −bid_qty(t-1)` (entire prior level wiped)

Mirror for the ask side with sign reversal. Update `test_ofi.py` accordingly.

### A2. OFI grouping key is missing `expiry`

Contracts are keyed by `(strike, option_type)`, but NIFTY has weekly + monthly expiries co-existing at the same strike. Without `expiry`, multiple contracts collide on the same row in the pivot/diff. Key must be `(expiry, strike_price, option_type)`.

### A3. `time_to_expiry = (expiry − snapshot).days / 365` zeroes out expiry day

Integer `.days` truncates the entire expiry-day session to `T = 0`, NaN'ing every IV on expiry Thursday (a high-volume, high-information day). Use fractional seconds:

```
T = max(0, (expiry_close_dt - snapshot_dt).total_seconds() / (365.25 * 86400))
```

### A4. No-arbitrage lower bound uses raw intrinsic, not forward-discounted

The BS lower bound for a European call is `max(S − K·e^{−rT}, 0)`, not `max(S−K, 0)`. Using raw intrinsic lets through prices where Brent cannot bracket a root. Use discounted intrinsic for both calls and puts.

### A5. Rolling RV averaging is in vol-space, not variance-space

Volatility is not additive; variance is. Average daily variance first, then take sqrt and annualize. Otherwise Jensen's inequality biases the rolling number low.

### A6. VRP unit mismatch: 365-calendar IV vs 252-trading RV

- IV uses `T = (expiry − snapshot).days / 365` (calendar-365)
- RV is annualized via `× 252` (trading-day convention)

These are on different time bases. Pick one convention (calendar-365 or trading-252) and apply consistently across IV and RV.

### A7. ATM IV averaging with NaN

If one of (call, put) IV is NaN, averaging produces NaN and silently strips ATM observations. Rule: average if both available, else use the available one, else NaN.

---

## B. Architectural Problem

### B1. `{SYMBOL}_options_chain.csv` will not scale

NIFTY at 562K rows/day × ~250 trading days/year ≈ 140M rows/year (~50 GB single file). The dedup writer does a full file scan on every weekly run against this monolith.

Recommended fix: **Per-date chain files** — `outputs/options_chain/{SYMBOL}/date=YYYY-MM-DD.csv`. Trivially idempotent (overwrite or skip), no scan-on-append, downstream readers `glob` the directory. The small per-symbol metric CSVs (RV, VRP, OFI, liquidity) stay as single flat files — those are tiny and manageable.

### B2. Manifest granularity inconsistency

The manifest must track at `(date, symbol)` granularity (not date-only), so a NIFTY success + BANKNIFTY failure means next run retries BANKNIFTY only.

### B3. Manifest update timing under partial failure

If the loop crashes mid-date after some output rows are already appended but before manifest is updated, next run re-appends. The dedup guard in `append_to_csv` saves correctness here, but document this dependency explicitly.

### B4. Time-zone specified two different ways

Plan says "IST-aware datetime"; spec says "UTC-aware datetime (IST = UTC+5:30)". Pick one; lock it in `config.py`.

### B5. No concurrent-run guard

Two simultaneous runs (cron + manual) will corrupt output CSVs on append. Add a `flock`-style file lock at the start of `run_pipeline.py`.

---

## C. Methodology Notes (Should Address)

### C1. Dividend yield set to zero
NIFTY 50 has ~1.2% dividend yield. Document `q = 0` as an explicit assumption and accept a `q` parameter (default 0) so it is swappable.

### C2. ξ (noise-to-signal) definition is ambiguous
BNHLS define `ξ² = ω² / √IQ`, so `ξ = ω / IQ^(1/4)`. Anchor definition to BNHLS (2009) §3 and unit-test against a deterministic input.

### C3. Overnight squared return is a noisy single-observation estimate
Flag it as an approximation. Note the Hansen-Lunde (2005) variance-ratio scaling approach as a future improvement.

### C4. ATM IV via nearest-strike rounding — specify tie-break
If spot is exactly between two strikes (e.g., 24,125 on a 50-grid), specify tie-break rule (e.g., half-up). Alternatively, interpolate between the two bracketing strikes for a more robust ATM IV.

### C5. Single-expiry VRP fallback silently biases VRP
When only one expiry is available, using it as a 30d proxy can produce large apparent VRP spikes. Either set NaN or add an `iv_30d_is_extrapolated` flag column.

### C6. Forward-fill `underlying_value` must not contaminate RK
Forward-fill is for IV inputs only. Realized kernel computation must use only observed (non-filled) spot values. Spell this out explicitly.

---

## D. Performance Notes

### D1. Brent per row — acceptable but state the numbers
~562K rows × skip ~250K zero/NaN rows = ~310K Brent calls × ~1ms each ≈ 5 min/symbol-day. ~15 min for NIFTY+BANKNIFTY+FINNIFTY. ~1.25 hrs/week at 5 days. Acceptable for v1. Skip early (filter NaN/zero bids before calling Brent).

### D2. OFI pivot is fine
~280 snapshots × ~600 strike-expiry-type combinations = ~170K cells. Fine for pandas.

---

## E. Testing Gaps

| Gap | Fix |
|-----|-----|
| Put IV roundtrip | Add put case to `test_iv_roundtrip` |
| Expiry-day fractional T | Test T > 0 at 09:15 on expiry day → finite IV |
| OFI three-case coverage | After fixing A1, add tests for bid up / bid same / bid down |
| Rolling RV in variance space | After fixing A5, assert `rk_5d_ann == sqrt(mean(rk_daily_var) × 252)` |
| VRP units | After fixing A6, test `vrp == 0` when `iv == rk_ann_vol` exactly |
| Constant-maturity edge cases | Only-one-expiry / only-far-expiry / extrapolation cases |
| Rate fetcher fallback chain | Mock RBI fail → reads rates.csv; rates.csv missing → default |
| Manifest partial failure | NIFTY success + BANKNIFTY fail → next run retries BANKNIFTY only, no NIFTY duplicates |
| Deterministic RK test | Hand-computed RK on a small input vector (not just GBM statistical bound) |
| Forward-fill isolation | Verify forward-filled values do not produce zero log-returns in RK input |
| `test_ofi_normalization` | Replace with "sign matches numerator" property test; OFI_norm ∈ [-1,1] is not guaranteed |

---

## F. Smaller Items

- `total_buy_quantity`/`total_sell_quantity` listed in schema but unused. Either document why or add a whole-book OFI sanity check using them.
- `bid_qty` unit: lots vs contracts — verify and document.
- `processed_dates.json` update: use atomic write (`write to .tmp`, then `os.replace()`).
- `pipeline.log` location: plan (project root) vs spec (inside `outputs/`) — pick one.
- Log buckets should include: `rows_in`, `rows_after_clean`, `iv_attempted`, `iv_converged`, `iv_nan_intrinsic`, `iv_nan_zero_quote`, `iv_nan_no_root`.

---

## G. What's Solid (Do Not Change)

- BNHLS Parzen kernel choice and 5-min sampling
- Always recomputing IV from midpoint (not NSE's IV column)
- Variance-space VRP as primary metric, vol-space as secondary
- Idempotent CSV writer + manifest concept
- Brent's method on `[1e-6, 10]`
- Daily-median for IV_30d (robust to open/close noise)

---

## Priority for Fixing

**Before any code is written:** A1, A2, A6, B1, C2

**Before merging the first working version:** A3, A4, A5, A7, B2, B3, B4, C5, C6

**Before declaring v1 done:** All items in Section E (testing gaps)
