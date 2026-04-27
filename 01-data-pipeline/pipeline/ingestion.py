"""CSV ingestion and cleaning for NSE options chain data."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd
import pytz

from pipeline.config import Config

logger = logging.getLogger(__name__)

_IST = pytz.timezone("Asia/Kolkata")
_DATE_FOLDER_RE = re.compile(r"^date=(\d{4}-\d{2}-\d{2})$")

_NUMERIC_COLS = [
    "strike_price", "bid_price", "ask_price", "bid_qty", "ask_qty",
    "open_interest", "total_traded_volume", "underlying_value",
]


def load_day(date_dir: Path, symbol: str, cfg: Config) -> pd.DataFrame:
    """Load and clean one day's CSV for the given symbol."""
    csv_path = date_dir / f"{symbol}.csv"
    df = pd.read_csv(csv_path, low_memory=False)
    logger.info("Loaded %s: %d rows", csv_path, len(df))

    # Step 1: parse captured_at as IST-aware datetime
    df["captured_at"] = pd.to_datetime(df["captured_at"], format="ISO8601").dt.tz_localize(_IST)

    # Step 2: filter by the requested symbol (removes blank/NaN rows)
    before = len(df)
    df = df[df["symbol"].notna() & (df["symbol"].astype(str).str.strip() != "")]
    df = df[df["symbol"] == symbol]
    logger.info("Symbol filter: %d → %d rows", before, len(df))

    # Step 3: filter by market hours
    tz = pytz.timezone(cfg.timezone)
    date_part = df["captured_at"].iloc[0].date() if len(df) else None
    if date_part is not None:
        open_dt = tz.localize(pd.Timestamp(f"{date_part} {cfg.market_open}").to_pydatetime())
        close_dt = tz.localize(pd.Timestamp(f"{date_part} {cfg.market_close}").to_pydatetime())
        before = len(df)
        df = df[(df["captured_at"] >= open_dt) & (df["captured_at"] <= close_dt)]
        logger.info("Market hours filter: %d → %d rows", before, len(df))

    # Step 4: parse expiry to IST datetime at 15:30 (vectorized)
    expiry_naive = pd.to_datetime(df["expiry"], format="%d-%m-%Y") + pd.Timedelta(hours=15, minutes=30)
    df["expiry"] = expiry_naive.dt.tz_localize(_IST)

    # Step 5: cast numeric columns
    for col in _NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Step 6: sort by captured_at then deduplicate by (captured_at_min, strike_price, option_type, expiry)
    df = df.sort_values("captured_at")
    df["captured_at_min"] = df["captured_at"].dt.floor("min")
    before = len(df)
    df = df.drop_duplicates(
        subset=["captured_at_min", "strike_price", "option_type", "expiry"],
        keep="last",
    )
    df = df.drop(columns=["captured_at_min"])
    logger.info("Dedup: %d → %d rows", before, len(df))

    # Step 7: forward-fill underlying_value for IV inputs only (sort must precede this)
    df["underlying_value_ffill"] = df["underlying_value"].ffill()

    # Step 8: derived columns (vectorized)
    df["mid_price"] = (df["bid_price"] + df["ask_price"]) / 2
    invalid_mid = (
        df["bid_price"].isna() | df["ask_price"].isna() |
        (df["bid_price"] <= 0) | (df["ask_price"] <= 0)
    )
    df.loc[invalid_mid, "mid_price"] = float("nan")

    delta_seconds = (df["expiry"] - df["captured_at"]).dt.total_seconds()
    df["time_to_expiry"] = (delta_seconds / (365.25 * 86400)).clip(lower=0.0)

    df = df.reset_index(drop=True)
    return df


def discover_new_dates(data_dir: Path, processed_dates: set[str]) -> list[str]:
    """Return sorted list of date strings in data_dir not in processed_dates."""
    processed = set(processed_dates)
    dates = []
    for entry in data_dir.iterdir():
        m = _DATE_FOLDER_RE.match(entry.name)
        if m and entry.is_dir():
            date_str = m.group(1)
            if date_str not in processed:
                dates.append(date_str)
    return sorted(dates)
