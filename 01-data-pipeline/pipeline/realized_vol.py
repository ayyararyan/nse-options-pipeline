"""Parzen BNHLS realized kernel and rolling realized volatility.

References: Barndorff-Nielsen, Hansen, Lunde & Shephard (2008/2009).
"""

from __future__ import annotations

import logging
import math
from typing import Optional

import numpy as np
import pandas as pd

from pipeline.config import Config

logger = logging.getLogger(__name__)


def parzen_weights(H: int) -> np.ndarray:
    """Return Parzen kernel weights k(0/(H+1)) ... k(H/(H+1)), shape (H+1,).

    h=0 gives weight 1.0. Weights decrease monotonically to near-zero at h=H.

    k(x) = 1 − 6x²(1−x)   for x ≤ 0.5
    k(x) = 2(1−x)³         for x > 0.5
    """
    h = np.arange(H + 1)
    x = h / (H + 1)
    return np.where(x <= 0.5, 1.0 - 6.0 * x**2 * (1.0 - x), 2.0 * (1.0 - x)**3)


def optimal_bandwidth(log_rets: np.ndarray) -> int:
    """Return BNHLS optimal bandwidth H* for the Parzen kernel.

    H* = ceil(3.5134 × ξ^{4/5} × n^{3/5})  (BNHLS 2009 §3)
    Falls back to ceil(0.5 × sqrt(n)) on numerical edge cases.
    """
    n = len(log_rets)
    eps = 1e-10

    # Noise variance estimator: ω² = max(-0.5 × mean(r_j × r_{j+1}), ε)
    lag1_cov = np.dot(log_rets[1:], log_rets[:-1]) / max(n - 1, 1)
    omega_sq = max(-0.5 * lag1_cov, eps)

    # Integrated quarticity: IQ = (n/3) × Σ r_j⁴
    IQ = max((n / 3.0) * np.sum(log_rets**4), eps)

    omega = math.sqrt(omega_sq)  # ξ = ω/IQ^{1/4}, not ω²/IQ^{1/4}
    xi = omega / (IQ**0.25)
    H = math.ceil(3.5134 * xi**(4.0 / 5.0) * n**(3.0 / 5.0))

    fallback = math.ceil(0.5 * math.sqrt(n))
    if H < 1 or H > n // 2:
        H = fallback

    return H


def realized_kernel(log_rets: np.ndarray, H: Optional[int] = None) -> float:
    """Compute BNHLS realized kernel for one day's intraday log-returns.

    RK = k(0)·γ₀ + 2·Σ_{h=1}^{H} k(h/(H+1))·γ_h
    where γ_h = Σ_{j>h} r_j · r_{j-h}  (not divided by n).

    H=None triggers adaptive bandwidth via optimal_bandwidth().
    H=0 returns simple realized variance (sum of squared returns).
    """
    if H is None:
        H = optimal_bandwidth(log_rets)

    weights = parzen_weights(H)
    rk = weights[0] * np.dot(log_rets, log_rets)
    for h in range(1, H + 1):
        gamma_h = np.dot(log_rets[h:], log_rets[:-h])
        rk += 2.0 * weights[h] * gamma_h

    return float(max(rk, 0.0))


def compute_daily_rk(df: pd.DataFrame, cfg: Config, prev_close: Optional[float] = None) -> dict:
    """Extract spot from df, resample to 5min, apply RK + optional overnight return.

    Uses raw underlying_value (not underlying_value_ffill) — forward-filled values
    would inject synthetic zero log-returns that contaminate the RK estimate.

    Returns dict with keys: rk_daily_var, rk_daily_vol, rk_ann_vol, bandwidth_H, n_bars.
    """
    # Extract raw spot: deduplicate timestamps, use underlying_value (not ffill).
    # keep='first': all rows at same timestamp share the same underlying_value in NSE data.
    spot = (
        df.drop_duplicates(subset=["captured_at"], keep="first")
        .set_index("captured_at")
        .sort_index()["underlying_value"]
    )

    # Resample to 5-min bars and clip to market hours
    spot_5m = (
        spot.resample(cfg.resample_freq)
        .last()
        .between_time(cfg.market_open, cfg.market_close)
        .dropna()
    )

    log_rets = np.log(spot_5m).diff().dropna().values
    n_bars = len(log_rets)

    if n_bars < 2:
        logger.warning("Too few bars for RK estimation (n_bars=%d); returning NaN", n_bars)
        return {"rk_daily_var": float("nan"), "rk_daily_vol": float("nan"),
                "rk_ann_vol": float("nan"), "bandwidth_H": 0, "n_bars": n_bars}

    H = optimal_bandwidth(log_rets)
    rk_intraday = realized_kernel(log_rets, H=H)

    # Overnight squared return
    overnight_sq = 0.0
    if prev_close is not None and not np.isnan(prev_close) and len(spot_5m) > 0:
        today_open = float(spot_5m.iloc[0])
        overnight_sq = math.log(today_open / prev_close) ** 2

    rk_daily_var = max(rk_intraday + overnight_sq, 0.0)
    rk_daily_vol = math.sqrt(rk_daily_var)
    rk_ann_vol = rk_daily_vol * math.sqrt(cfg.ann_factor)

    logger.info(
        "RK: n_bars=%d H=%d rk_intraday=%.6f overnight_sq=%.6f rk_ann_vol=%.4f",
        n_bars, H, rk_intraday, overnight_sq, rk_ann_vol,
    )

    return {
        "rk_daily_var": rk_daily_var,
        "rk_daily_vol": rk_daily_vol,
        "rk_ann_vol": rk_ann_vol,
        "bandwidth_H": H,
        "n_bars": n_bars,
    }


def compute_rolling_rv(daily_rk_var: pd.Series, windows: list[int], ann_factor: int) -> pd.DataFrame:
    """Rolling-mean of daily variance, then annualize by sqrt(ann_factor).

    Computed in variance space to avoid Jensen's inequality bias from averaging vols.
    rk_Nd_ann = sqrt(rolling_mean(rk_daily_var, N) × ann_factor)

    Returns DataFrame with columns rk_{N}d_ann for each N in windows.
    NaN until min_periods = window size is satisfied.
    """
    result = pd.DataFrame(index=daily_rk_var.index)
    for w in windows:
        result[f"rk_{w}d_ann"] = np.sqrt(
            daily_rk_var.rolling(w, min_periods=w).mean() * ann_factor
        )
    return result
