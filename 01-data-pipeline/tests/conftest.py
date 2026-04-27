import pytest
import pandas as pd
import numpy as np
from pathlib import Path
import pytz
from pipeline.config import Config, default_config as _default_config

_IST = pytz.timezone("Asia/Kolkata")


@pytest.fixture
def default_config(tmp_path: Path) -> Config:
    """Default pipeline Config pointing at a temporary data and output dir."""
    return _default_config(data_dir=tmp_path, output_dir=tmp_path / "outputs")


@pytest.fixture
def tiny_day_df() -> pd.DataFrame:
    """Minimal 3-snapshot NIFTY options chain DataFrame (post-ingestion cleaned schema).

    3 snapshots × 2 strikes × 2 expiries × 2 option types = 24 rows.
    captured_at is IST-aware; expiry is IST datetime at 15:30.
    bid/ask and OI vary by strike to enable non-degenerate spread and OI tests.
    """
    snapshots = [
        pd.Timestamp("2026-04-24 09:30:00", tz=_IST),
        pd.Timestamp("2026-04-24 10:30:00", tz=_IST),
        pd.Timestamp("2026-04-24 11:30:00", tz=_IST),
    ]
    strikes = [24100, 24150]
    expiries = [
        pd.Timestamp("2026-04-28 15:30:00", tz=_IST),
        pd.Timestamp("2026-05-29 15:30:00", tz=_IST),
    ]
    option_types = ["CE", "PE"]

    # Per-strike bid/ask/OI variation for non-degenerate OFI and spread tests
    _bid = {(24100, "CE"): 95.0, (24100, "PE"): 80.0,
            (24150, "CE"): 75.0, (24150, "PE"): 100.0}
    _ask = {(24100, "CE"): 100.0, (24100, "PE"): 85.0,
            (24150, "CE"): 80.0, (24150, "PE"): 105.0}
    _oi  = {(24100, "CE"): 12000, (24100, "PE"): 8000,
            (24150, "CE"): 9500,  (24150, "PE"): 10500}

    rows = []
    for i, ts in enumerate(snapshots):
        for strike in strikes:
            for expiry in expiries:
                for otype in option_types:
                    # Vary bid_qty slightly across snapshots to give OFI signal
                    bq = 150 + i * 10 if otype == "CE" else 140 - i * 5
                    rows.append({
                        "captured_at": ts,
                        "symbol": "NIFTY",
                        "expiry": expiry,
                        "strike_price": strike,
                        "option_type": otype,
                        "bid_price": _bid[(strike, otype)],
                        "ask_price": _ask[(strike, otype)],
                        "bid_qty": bq,
                        "ask_qty": bq + 20,
                        "open_interest": _oi[(strike, otype)] + i * 100,
                        "total_traded_volume": 500 + i * 50,
                        "total_buy_quantity": 600 + i * 30,
                        "total_sell_quantity": 580 + i * 25,
                        "underlying_value": 24125.0,
                    })

    df = pd.DataFrame(rows)
    # Add derived columns that load_day() computes (section-03 contract)
    df["underlying_value_ffill"] = df["underlying_value"].ffill()
    df["mid_price"] = (df["bid_price"] + df["ask_price"]) / 2
    invalid_mid = (
        df["bid_price"].isna() | df["ask_price"].isna() |
        (df["bid_price"] <= 0) | (df["ask_price"] <= 0)
    )
    df.loc[invalid_mid, "mid_price"] = float("nan")
    df["time_to_expiry"] = (
        (df["expiry"] - df["captured_at"]).dt.total_seconds() / (365.25 * 86400)
    ).clip(lower=0.0)
    return df


@pytest.fixture
def synthetic_spot_5m() -> pd.Series:
    """75 log-normal 5-minute NIFTY spot prices with fixed seed.

    Index: IST-aware DatetimeTZDtype from 09:15 to 15:25 on 2026-04-24.
    Note: dt uses NSE's 375-minute/250-day calendar for internal consistency.
    Vol (~20%) is approximate; do not use for exact magnitude assertions.
    """
    rng = np.random.default_rng(42)
    n = 75
    dt = 5 / (250 * 375)  # 5-min fraction of NSE trading year
    log_returns = rng.normal(0, np.sqrt(0.20**2 * dt), size=n - 1)
    prices = np.empty(n)
    prices[0] = 24000.0
    prices[1:] = prices[0] * np.exp(np.cumsum(log_returns))

    times = pd.date_range(
        start=pd.Timestamp("2026-04-24 09:15:00", tz=_IST),
        periods=n,
        freq="5min",
    )
    return pd.Series(prices, index=times, name="underlying_value")


@pytest.fixture
def tmp_data_dir(tmp_path: Path, tiny_day_df: pd.DataFrame) -> Path:
    """Temporary NSEI-Data directory with date=2026-04-24/NIFTY.csv in raw NSE format.

    The raw CSV is tz-naive (NSE format); load_day() is responsible for
    localizing captured_at to IST on read.
    Returns the root data dir (parent of date=... folders).
    """
    date_dir = tmp_path / "date=2026-04-24"
    date_dir.mkdir(parents=True)

    raw = tiny_day_df.copy()
    # NSE format: captured_at is ISO without timezone
    raw["captured_at"] = raw["captured_at"].dt.strftime("%Y-%m-%dT%H:%M:%S")
    # NSE format: expiry is DD-MM-YYYY
    raw["expiry"] = raw["expiry"].dt.strftime("%d-%m-%Y")

    # Extra columns present in raw NSE CSV
    raw["exchange_timestamp"] = raw["captured_at"]  # simplified; real data has ~1s offset
    raw["implied_volatility"] = 0.0
    raw["last_price"] = (raw["bid_price"] + raw["ask_price"]) / 2
    raw["change"] = 0.0
    raw["pchange"] = 0.0
    raw["change_in_oi"] = 0.0
    raw["pchange_in_oi"] = 0.0

    col_order = [
        "captured_at", "exchange_timestamp", "symbol", "expiry",
        "strike_price", "option_type", "open_interest", "change_in_oi",
        "pchange_in_oi", "total_traded_volume", "implied_volatility",
        "last_price", "change", "pchange", "bid_qty", "bid_price",
        "ask_qty", "ask_price", "total_buy_quantity", "total_sell_quantity",
        "underlying_value",
    ]
    raw[col_order].to_csv(date_dir / "NIFTY.csv", index=False)

    return tmp_path


@pytest.fixture
def tmp_output_dir(tmp_path: Path) -> Path:
    """Empty temporary output directory.

    Returns a writable path; subdirectories are created lazily by the writer.
    """
    out = tmp_path / "outputs"
    out.mkdir()
    return out
