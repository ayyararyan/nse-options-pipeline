"""Tests for pipeline/vrp.py — section-06-vrp."""

import math
import pytest
import pandas as pd
import numpy as np
import pytz

from pipeline.vrp import _nearest_strike, extract_atm_iv, compute_vrp

_IST = pytz.timezone("Asia/Kolkata")
_BASE = pd.Timestamp("2026-04-24 10:00:00", tz=_IST)


# ── helpers ───────────────────────────────────────────────────────────────────

def _exp(base_ts, days):
    """Build an expiry timestamp at 15:30 IST, `days` calendar days from base_ts."""
    return (base_ts + pd.Timedelta(days=days)).replace(
        hour=15, minute=30, second=0, microsecond=0
    )


def _make_df(ts_list, underlying, atm_strike, expiries_ce_pe, symbol="NIFTY"):
    """Build an options chain df with CE+PE rows at atm_strike.

    expiries_ce_pe: list of (expiry, ce_iv, pe_iv).
    Each (expiry, ce_iv, pe_iv) tuple is repeated for every ts in ts_list with
    the same IV; for per-snapshot different IVs, call with a single-element ts_list.
    """
    rows = []
    for ts in ts_list:
        for expiry, ce_iv, pe_iv in expiries_ce_pe:
            rows.extend([
                {
                    "captured_at": ts, "symbol": symbol,
                    "strike_price": atm_strike, "option_type": "CE",
                    "underlying_value": underlying, "expiry": expiry,
                    "computed_iv": ce_iv,
                },
                {
                    "captured_at": ts, "symbol": symbol,
                    "strike_price": atm_strike, "option_type": "PE",
                    "underlying_value": underlying, "expiry": expiry,
                    "computed_iv": pe_iv,
                },
            ])
    return pd.DataFrame(rows)


def _sign(x):
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


# ── _nearest_strike ───────────────────────────────────────────────────────────

def test_atm_strike_selection_nifty():
    """NIFTY spot 24100 → ATM strike 24100 (exact multiple of 50)."""
    assert _nearest_strike(24100.0, 50) == 24100


def test_atm_tie_break():
    """NIFTY spot 24125 is equidistant → rounds half-up to 24150."""
    assert _nearest_strike(24125.0, 50) == 24150


# ── ATM side-fallback tests ───────────────────────────────────────────────────

def test_atm_iv_call_nan(default_config):
    """Call IV is NaN, put IV valid → ATM IV = put IV."""
    df = _make_df([_BASE], 24100.0, 24100, [(_exp(_BASE, 30), float("nan"), 0.20)])
    result = extract_atm_iv(df, default_config)
    assert not pd.isna(result.loc[_BASE, "iv_30d"])
    assert abs(result.loc[_BASE, "iv_30d"] - 0.20) < 1e-6


def test_atm_iv_put_nan(default_config):
    """Put IV is NaN, call IV valid → ATM IV = call IV."""
    df = _make_df([_BASE], 24100.0, 24100, [(_exp(_BASE, 30), 0.22, float("nan"))])
    result = extract_atm_iv(df, default_config)
    assert not pd.isna(result.loc[_BASE, "iv_30d"])
    assert abs(result.loc[_BASE, "iv_30d"] - 0.22) < 1e-6


def test_atm_iv_both_nan(default_config):
    """Both call and put IV are NaN → ATM IV = NaN."""
    df = _make_df([_BASE], 24100.0, 24100, [(_exp(_BASE, 30), float("nan"), float("nan"))])
    result = extract_atm_iv(df, default_config)
    assert pd.isna(result.loc[_BASE, "iv_30d"])


# ── 30-day constant-maturity interpolation ────────────────────────────────────

def test_constant_maturity_interpolation(default_config):
    """T1=21d (IV=0.18) and T2=42d (IV=0.22) → IV_30d linearly interpolated."""
    df = _make_df(
        [_BASE], 24100.0, 24100,
        [(_exp(_BASE, 21), 0.18, 0.18), (_exp(_BASE, 42), 0.22, 0.22)],
    )
    result = extract_atm_iv(df, default_config)
    iv = result.loc[_BASE, "iv_30d"]
    assert not pd.isna(iv)
    # Exact value: 0.18 + 0.04 * (30 - 21) / (42 - 21) ≈ 0.19714 (T in fractional days from expiry timestamps)
    assert 0.18 < iv < 0.22
    assert abs(iv - 0.19714) < 5e-3  # allow for timestamp rounding across the ~5.5h offset


def test_constant_maturity_extrapolation_flag(default_config):
    """Only one expiry available → iv_30d_is_extrapolated must be True."""
    df = _make_df([_BASE], 24100.0, 24100, [(_exp(_BASE, 21), 0.20, 0.20)])
    result = extract_atm_iv(df, default_config)
    assert result.loc[_BASE, "iv_30d_is_extrapolated"]


def test_constant_maturity_no_extrapolation_flag(default_config):
    """Two bracketing expiries present → iv_30d_is_extrapolated must be False."""
    df = _make_df(
        [_BASE], 24100.0, 24100,
        [(_exp(_BASE, 21), 0.18, 0.18), (_exp(_BASE, 42), 0.22, 0.22)],
    )
    result = extract_atm_iv(df, default_config)
    assert not result.loc[_BASE, "iv_30d_is_extrapolated"]


def test_extrapolation_both_expiries_shorter_than_30d(default_config):
    """Both expiries < 30d → no bracketing pair → is_extrapolated = True."""
    df = _make_df(
        [_BASE], 24100.0, 24100,
        [(_exp(_BASE, 14), 0.19, 0.19), (_exp(_BASE, 21), 0.20, 0.20)],
    )
    result = extract_atm_iv(df, default_config)
    assert result.loc[_BASE, "iv_30d_is_extrapolated"]


def test_extrapolation_both_expiries_longer_than_30d(default_config):
    """Both expiries > 30d → no bracketing pair → is_extrapolated = True."""
    df = _make_df(
        [_BASE], 24100.0, 24100,
        [(_exp(_BASE, 42), 0.21, 0.21), (_exp(_BASE, 56), 0.22, 0.22)],
    )
    result = extract_atm_iv(df, default_config)
    assert result.loc[_BASE, "iv_30d_is_extrapolated"]


# ── daily median ──────────────────────────────────────────────────────────────

def test_banknifty_atm_strike_uses_100_increment(default_config):
    """BANKNIFTY spot uses increment=100, not 50."""
    assert _nearest_strike(52150.0, 100) == 52200  # rounds half-up to 52200
    assert _nearest_strike(52100.0, 100) == 52100  # exact multiple


def test_extract_atm_iv_returns_per_snapshot_series(default_config):
    """extract_atm_iv returns one row per captured_at; median differs from mean on skewed data."""
    ts1 = _BASE
    ts2 = _BASE + pd.Timedelta(hours=1)
    ts3 = _BASE + pd.Timedelta(hours=2)
    expiry = _exp(_BASE, 21)

    rows = []
    for ts, iv in [(ts1, 0.20), (ts2, 0.25), (ts3, 0.50)]:
        for otype in ("CE", "PE"):
            rows.append({
                "captured_at": ts, "symbol": "NIFTY", "strike_price": 24100,
                "option_type": otype, "underlying_value": 24100.0,
                "expiry": expiry, "computed_iv": iv,
            })
    df = pd.DataFrame(rows)

    result = extract_atm_iv(df, default_config)
    assert len(result) == 3
    daily_median = result["iv_30d"].median()
    daily_mean = result["iv_30d"].mean()
    # median of [0.20, 0.25, 0.50] = 0.25; mean ≈ 0.317
    assert abs(daily_median - 0.25) < 1e-6
    assert abs(daily_mean - daily_median) > 0.05


# ── compute_vrp ───────────────────────────────────────────────────────────────

def test_vrp_positive_when_iv_gt_rv():
    """IV=25%, RV=18% → vrp_vol > 0, vrp_variance > 0."""
    iv = pd.Series([0.25], index=["2026-04-24"])
    rv = pd.Series([0.18], index=["2026-04-24"])
    result = compute_vrp(iv, rv)
    assert result.loc["2026-04-24", "vrp_vol"] > 0
    assert result.loc["2026-04-24", "vrp_variance"] > 0


def test_vrp_negative_when_iv_lt_rv():
    """IV=18%, RV=25% → vrp_vol < 0, vrp_variance < 0."""
    iv = pd.Series([0.18], index=["2026-04-24"])
    rv = pd.Series([0.25], index=["2026-04-24"])
    result = compute_vrp(iv, rv)
    assert result.loc["2026-04-24", "vrp_vol"] < 0
    assert result.loc["2026-04-24", "vrp_variance"] < 0


def test_vrp_zero_when_equal():
    """IV == RV exactly → vrp_variance = 0, vrp_vol = 0."""
    v = 0.20
    iv = pd.Series([v], index=["2026-04-24"])
    rv = pd.Series([v], index=["2026-04-24"])
    result = compute_vrp(iv, rv)
    assert abs(result.loc["2026-04-24", "vrp_vol"]) < 1e-10
    assert abs(result.loc["2026-04-24", "vrp_variance"]) < 1e-10


def test_vrp_unit_consistency():
    """vrp_variance and vrp_vol must always have the same sign."""
    cases = [
        ("2026-04-24", 0.25, 0.18),
        ("2026-04-25", 0.18, 0.25),
        ("2026-04-26", 0.20, 0.20),
    ]
    iv = pd.Series([c[1] for c in cases], index=[c[0] for c in cases])
    rv = pd.Series([c[2] for c in cases], index=[c[0] for c in cases])
    result = compute_vrp(iv, rv)
    for date, _, _ in cases:
        row = result.loc[date]
        assert _sign(float(row["vrp_variance"])) == _sign(float(row["vrp_vol"]))


def test_vrp_nan_propagation_on_missing_dates():
    """Dates present in only one input → NaN VRP on those dates."""
    iv = pd.Series([0.25, 0.22], index=["2026-04-24", "2026-04-25"])
    rv = pd.Series([0.18, 0.19], index=["2026-04-25", "2026-04-26"])
    result = compute_vrp(iv, rv)
    # All three dates appear (outer join)
    assert "2026-04-24" in result.index
    assert "2026-04-26" in result.index
    # Dates with only one input → NaN
    assert pd.isna(result.loc["2026-04-24", "vrp_vol"])
    assert pd.isna(result.loc["2026-04-26", "vrp_vol"])
    # Shared date → non-NaN
    assert not pd.isna(result.loc["2026-04-25", "vrp_vol"])
