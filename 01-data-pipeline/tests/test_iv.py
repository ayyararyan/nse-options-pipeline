"""Tests for pipeline/iv.py — Black-Scholes pricer and IV solver."""

import math

import pandas as pd
import pytest

from pipeline.iv import add_computed_iv, bs_price, compute_iv

# ── shared parameters ────────────────────────────────────────────────────────
S = 24000.0
K = 24000.0
T = 30 / 365.25
r = 0.065
q = 0.0
sigma = 0.20


# ── bs_price ─────────────────────────────────────────────────────────────────

def test_bs_call_put_parity():
    """C − P ≈ S·e^{−qT} − K·e^{−rT}  (dividend-adjusted put-call parity)."""
    call = bs_price(S, K, T, r, sigma, "CE", q)
    put = bs_price(S, K, T, r, sigma, "PE", q)
    expected = S * math.exp(-q * T) - K * math.exp(-r * T)
    assert abs((call - put) - expected) < 1e-6


# ── compute_iv round-trips ────────────────────────────────────────────────────

def test_iv_roundtrip_call():
    """compute_iv(bs_price(sigma=0.20, CE)) recovers 0.20 to within 1e-6."""
    price = bs_price(S, K, T, r, sigma, "CE", q)
    recovered = compute_iv(S, K, T, r, price, "CE", q)
    assert recovered is not None and not math.isnan(recovered)
    assert abs(recovered - sigma) < 1e-6


def test_iv_roundtrip_put():
    """compute_iv(bs_price(sigma=0.20, PE)) recovers 0.20 to within 1e-6."""
    price = bs_price(S, K, T, r, sigma, "PE", q)
    recovered = compute_iv(S, K, T, r, price, "PE", q)
    assert recovered is not None and not math.isnan(recovered)
    assert abs(recovered - sigma) < 1e-6


# ── NaN sentinel conditions ───────────────────────────────────────────────────

def test_iv_mid_zero():
    """compute_iv returns None when mid = 0 (zero quote — both legs zero)."""
    result = compute_iv(S, K, T, r, 0.0, "CE", q)
    assert result is None


def test_iv_mid_negative():
    """compute_iv returns None when mid < 0 (negative mid, e.g. single-leg zero)."""
    result = compute_iv(S, K, T, r, -1.0, "CE", q)
    assert result is None


def test_iv_expired():
    """Returns None when T = 0 (contract already expired)."""
    result = compute_iv(S, K, 0.0, r, 50.0, "CE", q)
    assert result is None


def test_iv_expiry_day_fractional_t():
    """At 09:15 on expiry day, T > 0 and compute_iv returns a finite positive value."""
    # 6 hours 15 minutes remaining until 15:30 expiry
    t_seconds = (6 * 3600 + 15 * 60)
    fractional_t = t_seconds / (365.25 * 86400)
    assert fractional_t > 0
    price = bs_price(S, K, fractional_t, r, sigma, "CE", q)
    recovered = compute_iv(S, K, fractional_t, r, price, "CE", q)
    assert recovered is not None and not math.isnan(recovered)
    assert recovered > 0


# ── below-intrinsic guards ────────────────────────────────────────────────────

def test_iv_deep_otm_call_below_intrinsic():
    """Returns None when mid_price < discounted intrinsic for a call (ITM case).

    S=25000, K=24000: call is 1000 ITM; discounted intrinsic ≈ 1128.
    Passing mid=intrinsic/2 must return None, not attempt brentq.
    """
    s_itm = 25000.0
    k = 24000.0
    intrinsic = s_itm * math.exp(-q * T) - k * math.exp(-r * T)
    result = compute_iv(s_itm, k, T, r, intrinsic / 2, "CE", q)
    assert result is None


def test_iv_deep_otm_put_below_intrinsic():
    """Returns None when mid_price < discounted intrinsic for a put (ITM case).

    S=24000, K=25000: put is 1000 ITM; discounted intrinsic ≈ 869.
    Passing mid=intrinsic/2 must return None.
    """
    s = 24000.0
    k_itm = 25000.0
    intrinsic = k_itm * math.exp(-r * T) - s * math.exp(-q * T)
    result = compute_iv(s, k_itm, T, r, intrinsic / 2, "PE", q)
    assert result is None


# ── add_computed_iv smoke test ────────────────────────────────────────────────

def test_add_computed_iv_convergence_logging(tiny_day_df, default_config, caplog):
    """add_computed_iv() adds computed_iv column; bucket counts sum to row count."""
    import logging
    import re

    with caplog.at_level(logging.INFO, logger="pipeline.iv"):
        result = add_computed_iv(tiny_day_df, default_config.default_rate, default_config)

    assert "computed_iv" in result.columns
    assert len(result) == len(tiny_day_df)
    # Original DataFrame must not be modified
    assert "computed_iv" not in tiny_day_df.columns
    # At least some rows should have valid IV
    assert result["computed_iv"].notna().any()
    # All five bucket names must appear in the log line
    log_text = caplog.text
    for bucket in ("iv_converged", "iv_nan_zero_quote", "iv_nan_expired",
                   "iv_nan_intrinsic", "iv_nan_no_root"):
        assert bucket in log_text, f"Missing bucket in log: {bucket}"
    # Bucket counts extracted from log must sum to total row count
    counts = [int(x) for x in re.findall(r"=(\d+)", log_text)]
    assert sum(counts) == len(tiny_day_df)
