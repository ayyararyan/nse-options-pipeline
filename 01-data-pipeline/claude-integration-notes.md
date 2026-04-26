# Integration Notes: Opus Review Feedback

## What I'm Integrating and Why

### A1. OFI formula — INTEGRATING
The reviewer is correct that the plan described a simplified `Δqty × indicator` which collapses the price-improvement case. The Cont et al. three-case definition is materially different when `bid_price(t) > bid_price(t-1)`: the full new size enters, not just the delta. This would produce systematically wrong OFI numbers on active days. Rewriting to the three-case formula in the plan.

### A2. OFI grouping key missing `expiry` — INTEGRATING
Confirmed: NIFTY weekly and monthly options share the same strikes. Without `expiry` as part of the pivot key, contracts from different expiries will collide. Adding `expiry` to the key.

### A3. `time_to_expiry` zeros out expiry day — INTEGRATING
Integer `.days` will indeed NaN every IV for the weekly expiry Thursday session, which is the highest-volume day. Switching to fractional seconds from snapshot datetime to 15:30 on expiry date.

### A4. Forward-discounted intrinsic bound — INTEGRATING
The Black-Scholes lower bound for European options uses the discounted strike `K·e^{−rT}`. Using raw intrinsic causes some ITM options to be silently skipped where they should converge. Minor fix, applying it.

### A5. Rolling RV in variance-space — INTEGRATING
Jensen's inequality creates a systematic downward bias when averaging vol instead of variance. The fix is mechanical: average daily variance, then sqrt × sqrt(252). Updating the module description and the test.

### A6. VRP unit mismatch (calendar vs trading-day) — INTEGRATING
This is a real systematic bug. Choosing to standardize on **calendar-365 convention** throughout (IV already uses calendar days via `T = delta.days/365`; RV will be annualized as `× 365` instead of `× 252`). This aligns with how NSE VIX is computed (calendar days) and makes the VRP apples-to-apples. Documenting this choice explicitly in the plan.

### A7. ATM IV NaN handling — INTEGRATING
Simple guard: use both if available, one if the other is NaN, NaN only if both are. Adding to the ATM IV extraction description.

### B1. Monolithic options-chain CSV — INTEGRATING
The reviewer's concern is correct: 140M rows/year single file is unmanageable for Split 02 downstream. Adopting per-date chain files: `outputs/options_chain/{SYMBOL}/date=YYYY-MM-DD.csv`. The daily metric files (RV, VRP, OFI, liquidity) remain as single flat files per symbol — they are tiny (one row per day).

### B2-B4. Manifest granularity, timezone, partial failure — INTEGRATING
Adding explicit `(date, symbol)` granularity, choosing IST-aware datetime consistently, and documenting the atomic manifest update pattern.

### B5. Concurrent-run guard — INTEGRATING
Adding a `fcntl.flock` or `filelock` library lock to `run_pipeline.py` to prevent simultaneous runs.

### C1. Dividend yield — INTEGRATING (as documented assumption)
Adding `q` parameter (default 0.0) to `bs_price` and documenting the ~1.2% dividend yield of NIFTY 50 as an ignored factor in v1. Correct fix would be to use dividend-adjusted BS formula; marking as future improvement.

### C2. ξ definition — INTEGRATING
Anchoring the exact formula to BNHLS (2009) §3 in the plan to prevent ambiguity at implementation time.

### C5. Single-expiry VRP fallback — INTEGRATING
Adding `iv_30d_is_extrapolated` flag column to the VRP output so downstream consumers can filter or highlight these rows.

### C6. Forward-fill isolation for RK — INTEGRATING
Adding explicit note that forward-fill applies to IV inputs only; RK spot series must use raw observed values.

### E. Testing gaps — INTEGRATING ALL
All suggested test additions are valid and cheap to add. Including the full expanded test list in the plan.

---

## What I'm NOT Integrating and Why

### C3. Overnight return scaling (Hansen-Lunde variance ratio)
The reviewer notes this as a future improvement. The simple overnight squared return is the standard v1 approach and sufficient for the weekly dashboard use case. Noting as a future improvement only.

### C4. ATM IV interpolation between bracketing strikes
The nearest-strike approach with an explicit tie-break rule is sufficient for a dashboard. Delta-interpolated ATM IV is a v2 enhancement. Adding the tie-break rule but not the interpolation.

### D1. Brent vectorization (Jaeckel/py_vollib)
Out of scope for v1. Timing is ~15 min/week which is acceptable. Noted in comments for future speedup if needed.

### F. `total_buy_quantity`/`total_sell_quantity` sanity check
Adding these as unused fields noted in the schema but not implementing a comparison module. Scope creep for v1.

---

## Changes Made to `claude-plan.md`

1. OFI Module: corrected to three-case Cont et al. definition; added `expiry` to grouping key
2. IV Module: changed intrinsic bound to discounted; added `q` dividend-yield parameter; changed `time_to_expiry` to fractional seconds
3. RV Module: changed rolling windows to average variance then sqrt; changed annualization from `×252` to `×365` (calendar convention); added ξ formula anchor to BNHLS §3
4. VRP Module: explicitly documented calendar-365 convention throughout; added ATM NaN guard; added `iv_30d_is_extrapolated` flag
5. Architecture: replaced monolithic `{SYMBOL}_options_chain.csv` with per-date files; updated manifest to `(date, symbol)` granularity; added timezone choice (IST-aware); added concurrent-run lock
6. Testing: expanded test list per reviewer's gaps
