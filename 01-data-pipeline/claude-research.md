# Research Findings: Data Pipeline & Core Metrics

## Topic: Realized Kernel Volatility Estimation (NSE / 5-minute data)

---

## 1. Realized Kernel — Formula and Standard Choice

The BNHLS (Barndorff-Nielsen, Hansen, Lunde, Shephard 2008/2009) realized kernel:

```
RK = sum_{h=-H}^{H} k(|h|/H) * gamma_h
   = k(0)*gamma_0 + 2 * sum_{h=1}^{H} k(h/H) * gamma_h   (by symmetry)
```

Where `gamma_h = sum_{j=h+1}^n r_j * r_{j-h}` is the h-th order sample autocovariance and `r_j = log(p_j/p_{j-1})` are intraday log-returns.

**Standard kernel: Parzen (non-flat-top)**
- Guarantees non-negative estimates (critical — negative variance is inadmissible)
- Flat-top kernels have better asymptotic efficiency but can go negative in small samples
- Parzen weights:
  - `k(x) = 1 - 6x²(1-x)` for `0 ≤ x ≤ 0.5`
  - `k(x) = 2(1-x)³` for `0.5 < x ≤ 1`

---

## 2. Optimal Bandwidth

```
H* = 3.5134 × ξ^(4/5) × n^(3/5)
```

Where:
- `n` = intraday return observations per day
- `ξ = ω / IQ^(1/4)` = noise-to-signal ratio; `ω²` estimated as `-0.5 × mean(r_j × r_{j+1})`

**For NSE NIFTY at 5-min intervals:** Trading day 09:15–15:30 = 375 min → `n = 75` bars/day. Expected `H ≈ 3–8` lags.

**Conservative fallback:** `H = ceil(0.5 × sqrt(n))` — robust to dependent noise, `H ≈ 4` for 75 bars.

---

## 3. Sampling Frequency: Is 5 Minutes Right for NSE?

- **Liu, Patton & Sheppard (2015):** Across multiple markets, 5-minute RV is "difficult to improve upon" — no estimator consistently beats it at standard significance.
- **NSE-specific (Srinivasan 2012):** 30-minute sampling was optimal for GARCH-based forecasts on NIFTY, but this applies to parametric models; for the realized kernel itself, 5 minutes is standard.
- **Recommendation:** Use 5-minute bars for NSE. The RK handles residual noise at this frequency through its autocovariance weighting. If tick data is available, 1-minute bars with adaptive H is preferred.

---

## 4. Microstructure Noise Handling

### Bid-Ask Bounce
At 5-minute frequency this is largely attenuated. The RK corrects for it via down-weighting of negative first-order autocovariance (the Roll spread effect). No additional treatment needed.

### Overnight Gaps (Critical for VRP)
RK captures **open-to-close intraday variance only**. India VIX prices full-day variance including overnight. **Always add overnight squared return:**

```python
rk_total = rk_intraday + (log(open_today / close_yesterday))**2
```

Without this, RV will systematically underestimate full-day variance vs. India VIX → artificially positive VRP bias.

### Jumps
For VRP, implement Bipower Variation as a complement:

```python
BV = (pi/2) * (n/(n-1)) * sum(|r_j| * |r_{j-1}|)
```

Report VRP with both RK and BV-adjusted RV to flag jump-driven distortions (e.g., RBI announcement days).

---

## 5. Python Implementation

**No established library** does the exact BNHLS realized kernel formula out-of-the-box:
- `arch.covariance.kernel.Parzen` has correct weights but is a HAC long-run covariance estimator (different use case). With `center=False` it can be made equivalent.
- Standard practice: **roll your own in NumPy (~50 lines)**

```python
import numpy as np

def parzen_weights(H: int) -> np.ndarray:
    x = np.arange(H + 1, dtype=float) / (H + 1)
    return np.where(x <= 0.5, 1 - 6*x**2*(1-x), 2*(1-x)**3)

def estimate_xi(r: np.ndarray) -> float:
    omega2 = max(-0.5 * np.mean(r[:-1] * r[1:]), 1e-20)
    iq = (len(r) / 3.0) * np.sum(r**4)
    return omega2 / (iq**0.25)

def optimal_h(r: np.ndarray) -> int:
    return max(1, int(np.ceil(3.5134 * estimate_xi(r)**0.8 * len(r)**0.6)))

def realized_kernel(r: np.ndarray, H: int = None) -> float:
    n = len(r)
    if H is None:
        H = min(optimal_h(r), n - 1)
    w = parzen_weights(H)
    rk = w[0] * np.dot(r, r)
    for h in range(1, H + 1):
        rk += 2 * w[h] * np.dot(r[h:], r[:n-h])
    return float(rk)
```

---

## 6. Annualization — NSE Convention

- **252 trading days** (NSE standard)
- `RK_annual_var = RK_daily_var × 252`
- `RK_annual_vol = sqrt(RK_daily_var) × sqrt(252)`
- India VIX is already annualized in % → convert: `IV_decimal = VIX / 100`

**VRP computation:**
```python
# In variance space (recommended):
VRP = (IV_decimal)**2 - RK_annual_var

# In vol space (more intuitive for display):
VRP_vol = IV_decimal - sqrt(RK_annual_var)
```

---

## 7. Realized Kernel vs. Simpler Estimators

| Estimator | Daily Vol RMSE (relative) | Notes |
|-----------|--------------------------|-------|
| Close-to-close | 1.00 (baseline) | Very noisy, ignores intraday |
| Parkinson (H-L) | ~0.60–0.65 | Better but biased under jumps |
| Simple 5-min RV | ~0.30–0.40 | Large improvement from intraday |
| **Realized Kernel** | **~0.25–0.35** | Marginal gain over 5-min RV |

**Key insight (Liu et al. 2015):** The biggest gain is switching from close-to-close to any intraday estimator. The RK vs. 5-min RV improvement is modest (5–10% RMSE reduction). **Implement both in parallel** — divergence >10% on a day signals data quality issues.

---

## 8. Testing Recommendations (New Project)

Since this is a new project with no existing test setup:

- **Framework:** `pytest` (standard Python)
- **Key things to test:**
  - RK output is non-negative (Parzen kernel guarantee)
  - RK equals simple RV when H=0
  - Bandwidth H is within reasonable range for given n
  - IV extraction converges and returns NaN (not error) for edge cases
  - VRP computation handles aligned/misaligned date indices
  - Weekly update script is idempotent (re-running produces same output)
- **Test data:** Use synthetic price paths (GBM) with known variance as ground truth for RK; use analytically known IV for BS inversion tests.

---

## Sources

- Barndorff-Nielsen et al. (2009) "Realized Kernels in Practice" — *Econometrics Journal*
- Barndorff-Nielsen et al. (2008) "Designing Realized Kernels" — *Econometrica*
- Liu, Patton & Sheppard (2015) "Does Anything Beat 5-Minute RV?" — SSRN
- Srinivasan (2012) "Optimal Sampling Frequency for NSE NIFTY" — Academia.edu
- NSE Working Paper No. 9 — India VIX Methodology
- arch library (bashtage) — Parzen kernel source
- Zerodha Varsity — NSE 252-day annualization convention
