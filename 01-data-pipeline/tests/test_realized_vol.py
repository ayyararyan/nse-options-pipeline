"""Tests for pipeline/realized_vol.py — BNHLS realized kernel."""

import math

import numpy as np
import pandas as pd
import pytest
import pytz

from pipeline.realized_vol import (
    compute_daily_rk,
    compute_rolling_rv,
    optimal_bandwidth,
    parzen_weights,
    realized_kernel,
)

_IST = pytz.timezone("Asia/Kolkata")


# ── parzen_weights ────────────────────────────────────────────────────────────

def test_parzen_weights_first_is_one():
    """parzen_weights(H)[0] == 1.0 for any H."""
    for H in [0, 5, 20]:
        w = parzen_weights(H)
        assert w[0] == 1.0, f"H={H}: w[0]={w[0]}"


def test_parzen_weights_last_approaches_zero():
    """parzen_weights(H)[-1] is close to 0 for H >= 10."""
    w = parzen_weights(20)
    assert w[-1] < 0.01, f"Last weight {w[-1]} too large"


# ── realized_kernel ───────────────────────────────────────────────────────────

def test_rk_positive():
    """realized_kernel returns a non-negative value."""
    rng = np.random.default_rng(99)
    r = rng.normal(0, 0.01, 50)
    assert realized_kernel(r) >= 0


def test_rk_h_zero_equals_rv():
    """realized_kernel(r, H=0) == np.dot(r, r)  (reduces to simple RV)."""
    r = np.array([0.01, -0.02, 0.015, -0.005, 0.008])
    assert abs(realized_kernel(r, H=0) - np.dot(r, r)) < 1e-14


def test_rk_deterministic():
    """Hand-computed RK for a 5-element vector matches formula to 1e-10."""
    r = np.array([0.1, -0.2, 0.15, -0.1, 0.05])
    H = 2

    gamma_0 = np.dot(r, r)               # 0.085
    gamma_1 = np.dot(r[1:], r[:-1])      # -0.07
    gamma_2 = np.dot(r[2:], r[:-2])      # 0.0425

    w = parzen_weights(H)                 # [1.0, 5/9, 2/27]
    expected = w[0] * gamma_0 + 2 * (w[1] * gamma_1 + w[2] * gamma_2)
    # = 0.085 + 2*(5/9*(-0.07) + 2/27*0.0425) ≈ 0.01352

    assert abs(realized_kernel(r, H=H) - expected) < 1e-10


def test_rk_gbm():
    """Mean RK across 30 GBM seeds is within 5% of sigma^2 * total_variance."""
    sigma = 0.20
    n = 75
    dt = 5 / (250 * 375)
    expected_var = sigma ** 2 * dt * n

    rks = []
    for seed in range(30):
        rng = np.random.default_rng(seed)
        r = rng.normal(0.0, sigma * math.sqrt(dt), n)
        rks.append(realized_kernel(r))

    mean_rk = np.mean(rks)
    rel_error = abs(mean_rk - expected_var) / expected_var
    assert rel_error < 0.05, f"mean_rk={mean_rk:.6e} expected={expected_var:.6e} err={rel_error:.1%}"


# ── optimal_bandwidth ─────────────────────────────────────────────────────────

def test_optimal_bandwidth_range(synthetic_spot_5m):
    """For n≈75 intraday returns, optimal_bandwidth returns H in [1, 20]."""
    log_rets = np.log(synthetic_spot_5m).diff().dropna().values
    H = optimal_bandwidth(log_rets)
    assert 1 <= H <= 20, f"H={H} out of expected range [1, 20]"


# ── compute_daily_rk ──────────────────────────────────────────────────────────

def test_overnight_increases_variance(tiny_day_df, default_config):
    """A non-zero overnight return strictly increases rk_daily_var."""
    result_no_overnight = compute_daily_rk(tiny_day_df, default_config, prev_close=None)
    # 2% overnight gap from first spot value
    first_price = tiny_day_df["underlying_value"].dropna().iloc[0]
    prev_close = first_price * 1.02
    result_with_overnight = compute_daily_rk(tiny_day_df, default_config, prev_close=prev_close)
    assert result_with_overnight["rk_daily_var"] > result_no_overnight["rk_daily_var"]


def test_rk_ann_vol_uses_365(synthetic_spot_5m, default_config):
    """rk_ann_vol = sqrt(rk_daily_var) * sqrt(365), not sqrt(252)."""
    df = pd.DataFrame({
        "captured_at": synthetic_spot_5m.index,
        "underlying_value": synthetic_spot_5m.values,
        "underlying_value_ffill": synthetic_spot_5m.values,
    })
    result = compute_daily_rk(df, default_config, prev_close=None)

    expected_365 = math.sqrt(result["rk_daily_var"]) * math.sqrt(365)
    wrong_252 = math.sqrt(result["rk_daily_var"]) * math.sqrt(252)

    assert abs(result["rk_ann_vol"] - expected_365) < 1e-12
    assert abs(result["rk_ann_vol"] - wrong_252) > 1e-10


def test_forward_fill_isolation(tiny_day_df, default_config):
    """compute_daily_rk uses raw underlying_value; NaN rows are dropped, not filled."""
    # Baseline n_bars with all underlying_value present
    baseline = compute_daily_rk(tiny_day_df, default_config, prev_close=None)

    # Null out underlying_value at the 10:30 snapshot; leave underlying_value_ffill intact
    df_nan = tiny_day_df.copy()
    snap_mask = df_nan["captured_at"] == pd.Timestamp("2026-04-24 10:30:00", tz=_IST)
    df_nan.loc[snap_mask, "underlying_value"] = np.nan
    df_nan.loc[snap_mask, "underlying_value_ffill"] = 24125.0  # would be filled

    result_nan = compute_daily_rk(df_nan, default_config, prev_close=None)
    # The 10:30 bar is dropped (raw NaN, not filled), so fewer bars than baseline
    assert result_nan["n_bars"] < baseline["n_bars"]
    # With only 1 log return remaining, RK returns NaN (too few bars); that's correct
    assert math.isnan(result_nan["rk_daily_var"]) or result_nan["rk_daily_var"] >= 0


def test_compute_daily_rk_too_few_bars(default_config):
    """Returns NaN for rk_daily_var when fewer than 2 bars are available."""
    df = pd.DataFrame([{
        "captured_at": pd.Timestamp("2026-04-24 09:15:00", tz=_IST),
        "underlying_value": 24000.0,
        "underlying_value_ffill": 24000.0,
    }])
    result = compute_daily_rk(df, default_config, prev_close=None)
    assert math.isnan(result["rk_daily_var"])


# ── compute_rolling_rv ────────────────────────────────────────────────────────

def test_rolling_rv_in_variance_space():
    """Rolling RV = sqrt(rolling_mean(rk_daily_var, N) * 365), not mean(rk_ann_vol)."""
    daily_var = pd.Series(
        [0.0001, 0.0002, 0.0003, 0.0002, 0.0001, 0.0003, 0.0002, 0.0001, 0.0002, 0.0003, 0.0002]
    )
    rolling_df = compute_rolling_rv(daily_var, windows=[5], ann_factor=365)

    expected = (daily_var.rolling(5, min_periods=5).mean() * 365).apply(math.sqrt)

    valid = rolling_df["rk_5d_ann"].dropna().values
    expected_valid = expected.dropna().values
    np.testing.assert_allclose(valid, expected_valid, rtol=1e-10)
