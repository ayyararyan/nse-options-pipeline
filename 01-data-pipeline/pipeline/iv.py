"""Black-Scholes IV computation for NSE European options."""

from __future__ import annotations

import logging
import math
from typing import Optional

import pandas as pd
from scipy.optimize import brentq
from scipy.stats import norm

from pipeline.config import Config

logger = logging.getLogger(__name__)

BRENTQ_LOWER = 1e-6
BRENTQ_UPPER = 10.0


def bs_price(S: float, K: float, T: float, r: float, sigma: float,
             option_type: str, q: float = 0.0) -> float:
    """Black-Scholes European option price with continuous dividend yield q.

    d1 = (ln(S/K) + (r − q + σ²/2) × T) / (σ × √T)
    d2 = d1 − σ × √T
    C  = S·e^{−qT}·N(d1) − K·e^{−rT}·N(d2)
    P  = K·e^{−rT}·N(−d2) − S·e^{−qT}·N(−d1)
    """
    if option_type not in ("CE", "PE"):
        raise ValueError(f"option_type must be 'CE' or 'PE', got {option_type!r}")
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    if option_type == "CE":
        return S * math.exp(-q * T) * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * math.exp(-q * T) * norm.cdf(-d1)


def compute_iv(S: float, K: float, T: float, r: float, mid: float,
               option_type: str, q: float = 0.0) -> Optional[float]:
    """Return IV in decimal annualised form, or None on failure.

    Returns None when:
      1. mid <= 0 (zero or negative quote)
      2. T <= 0 (expired contract)
      3. mid < discounted intrinsic (no-arbitrage violation)
      4. brentq finds no root in (BRENTQ_LOWER, BRENTQ_UPPER)
    """
    if mid <= 0 or T <= 0:
        return None

    fwd_S = S * math.exp(-q * T)
    disc_K = K * math.exp(-r * T)
    if option_type == "CE":
        intrinsic = max(fwd_S - disc_K, 0.0)
    else:
        intrinsic = max(disc_K - fwd_S, 0.0)

    if mid < intrinsic:
        return None

    try:
        return brentq(
            lambda sigma: bs_price(S, K, T, r, sigma, option_type, q) - mid,
            BRENTQ_LOWER,
            BRENTQ_UPPER,
        )
    except ValueError:
        return None


def add_computed_iv(df: pd.DataFrame, rate: float, cfg: Config) -> pd.DataFrame:
    """Add a `computed_iv` column to the options chain DataFrame.

    Uses underlying_value_ffill as S (not raw underlying_value) per the
    forward-fill contract established in section-03.

    Convergence buckets logged at INFO:
      iv_nan_zero_quote, iv_nan_expired, iv_nan_intrinsic, iv_nan_no_root, iv_converged
    """
    q = cfg.dividend_yield
    counts = {
        "iv_nan_zero_quote": 0,
        "iv_nan_expired": 0,
        "iv_nan_intrinsic": 0,
        "iv_nan_no_root": 0,
        "iv_converged": 0,
    }

    ivs: list[float] = []
    for row in df.itertuples():
        mid = row.mid_price
        T = row.time_to_expiry
        S = row.underlying_value_ffill
        K = row.strike_price
        otype = row.option_type
        bid = row.bid_price
        ask = row.ask_price

        bid_zero = (not pd.isna(bid)) and bid == 0
        ask_zero = (not pd.isna(ask)) and ask == 0
        if pd.isna(mid) or mid <= 0 or bid_zero or ask_zero or pd.isna(S) or pd.isna(K):
            counts["iv_nan_zero_quote"] += 1
            ivs.append(float("nan"))
            continue

        if pd.isna(T) or T <= 0:
            counts["iv_nan_expired"] += 1
            ivs.append(float("nan"))
            continue

        iv = compute_iv(S, K, T, rate, mid, otype, q)
        if iv is not None:
            counts["iv_converged"] += 1
            ivs.append(iv)
        else:
            # Distinguish intrinsic violation vs brentq no-root for telemetry
            fwd_S = S * math.exp(-q * T)
            disc_K = K * math.exp(-rate * T)
            intrinsic = max(fwd_S - disc_K, 0.0) if otype == "CE" else max(disc_K - fwd_S, 0.0)
            if mid < intrinsic:
                counts["iv_nan_intrinsic"] += 1
            else:
                counts["iv_nan_no_root"] += 1
            ivs.append(float("nan"))

    logger.info(
        "IV convergence: iv_converged=%d iv_nan_zero_quote=%d iv_nan_expired=%d "
        "iv_nan_intrinsic=%d iv_nan_no_root=%d",
        counts["iv_converged"], counts["iv_nan_zero_quote"],
        counts["iv_nan_expired"], counts["iv_nan_intrinsic"], counts["iv_nan_no_root"],
    )

    result = df.copy()
    result["computed_iv"] = ivs
    return result
