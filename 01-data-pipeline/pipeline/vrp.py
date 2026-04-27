"""Variance Risk Premium computation.

Implemented in section-06-vrp.
"""

from __future__ import annotations

import math

import pandas as pd

from pipeline.config import Config

_T_TARGET = 30 / 365.25  # 30 calendar days in fractional-year units


def _nearest_strike(spot: float, increment: int) -> int:
    """Return nearest strike to spot, rounding half-up on ties.

    Uses floor((spot + increment/2) / increment) * increment to avoid
    Python's default banker's rounding.
    """
    return int(math.floor((spot + increment / 2) / increment) * increment)


def extract_atm_iv(df_day: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Return time series of 30-day constant-maturity ATM IV for all snapshots in df_day.

    Parameters
    ----------
    df_day : pd.DataFrame
        Options chain for one symbol/day with a ``computed_iv`` column.
        Required columns: captured_at, symbol, strike_price, option_type,
        underlying_value, expiry, computed_iv.
    cfg : Config
        Pipeline configuration (uses cfg.atm_increments).

    Returns
    -------
    pd.DataFrame
        Index: captured_at.
        Columns: iv_30d, iv_30d_is_extrapolated.
    """
    symbol = df_day["symbol"].iloc[0]
    increment = cfg.atm_increments[symbol]

    records = []
    for ts, snap in df_day.groupby("captured_at", sort=True):
        underlying = float(snap["underlying_value"].iloc[0])
        atm_strike = _nearest_strike(underlying, increment)

        atm_rows = snap[snap["strike_price"] == atm_strike]

        # Build (T_expiry, atm_iv) pairs from valid expiries
        expiry_T_iv: list[tuple[float, float]] = []
        for expiry, exp_rows in atm_rows.groupby("expiry"):
            ce_series = exp_rows.loc[exp_rows["option_type"] == "CE", "computed_iv"]
            pe_series = exp_rows.loc[exp_rows["option_type"] == "PE", "computed_iv"]

            ce_ok = len(ce_series) > 0 and not pd.isna(ce_series.iloc[0])
            pe_ok = len(pe_series) > 0 and not pd.isna(pe_series.iloc[0])

            if ce_ok and pe_ok:
                atm_iv = (float(ce_series.iloc[0]) + float(pe_series.iloc[0])) / 2
            elif ce_ok:
                atm_iv = float(ce_series.iloc[0])
            elif pe_ok:
                atm_iv = float(pe_series.iloc[0])
            else:
                continue  # both sides NaN — skip this expiry

            T_exp = (expiry - ts).total_seconds() / (365.25 * 86400)
            expiry_T_iv.append((T_exp, atm_iv))

        if not expiry_T_iv:
            records.append({"captured_at": ts, "iv_30d": float("nan"), "iv_30d_is_extrapolated": True})
            continue

        expiry_T_iv.sort(key=lambda x: x[0])

        lower = [(T, iv) for T, iv in expiry_T_iv if T < _T_TARGET]
        upper = [(T, iv) for T, iv in expiry_T_iv if T >= _T_TARGET]

        if lower and upper:
            T1, iv1 = lower[-1]
            T2, iv2 = upper[0]
            iv_30d = iv1 + (iv2 - iv1) * (_T_TARGET - T1) / (T2 - T1)
            is_extrapolated = False
        else:
            # Use closest available expiry as proxy
            iv_30d = upper[0][1] if upper else lower[-1][1]
            is_extrapolated = True

        records.append({"captured_at": ts, "iv_30d": iv_30d, "iv_30d_is_extrapolated": is_extrapolated})

    return pd.DataFrame(records).set_index("captured_at")


def compute_vrp(daily_atm_iv: pd.Series, daily_rv: pd.Series) -> pd.DataFrame:
    """Join daily ATM IV and RK RV and compute vrp_variance and vrp_vol.

    Parameters
    ----------
    daily_atm_iv : pd.Series
        Daily median IV_30d in decimal annualized form, indexed by date.
    daily_rv : pd.Series
        rk_ann_vol in decimal annualized form, indexed by date.

    Returns
    -------
    pd.DataFrame
        Columns: date, iv_30d, rk_ann_vol, rk_ann_var, vrp_variance, vrp_vol.
        vrp_variance = iv_30d² − rk_ann_var
        vrp_vol      = iv_30d  − rk_ann_vol
        Rows are NaN where either input is missing.
    """
    iv, rv = daily_atm_iv.align(daily_rv, join="outer")

    rk_ann_var = rv ** 2
    vrp_variance = iv ** 2 - rk_ann_var
    vrp_vol = iv - rv

    return pd.DataFrame(
        {
            "iv_30d": iv.values,
            "rk_ann_vol": rv.values,
            "rk_ann_var": rk_ann_var.values,
            "vrp_variance": vrp_variance.values,
            "vrp_vol": vrp_vol.values,
        },
        index=iv.index,
    )
