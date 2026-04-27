import pytest
import pandas as pd
import numpy as np
from pathlib import Path
import pytz

from pipeline.ingestion import load_day, discover_new_dates
from pipeline.config import Config, default_config

_IST = pytz.timezone("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RAW_COLS = [
    "captured_at", "exchange_timestamp", "symbol", "expiry",
    "strike_price", "option_type", "open_interest", "change_in_oi",
    "pchange_in_oi", "total_traded_volume", "implied_volatility",
    "last_price", "change", "pchange", "bid_qty", "bid_price",
    "ask_qty", "ask_price", "total_buy_quantity", "total_sell_quantity",
    "underlying_value",
]


def _make_raw_csv(tmp_path: Path, rows: list[dict], date_str: str = "2026-04-24", symbol: str = "NIFTY") -> Path:
    """Write rows in raw NSE format to tmp_path/date={date_str}/{symbol}.csv."""
    date_dir = tmp_path / f"date={date_str}"
    date_dir.mkdir(parents=True, exist_ok=True)
    defaults = {
        "exchange_timestamp": "",
        "change_in_oi": 0.0,
        "pchange_in_oi": 0.0,
        "implied_volatility": 0.0,
        "last_price": 100.0,
        "change": 0.0,
        "pchange": 0.0,
        "total_buy_quantity": 600,
        "total_sell_quantity": 600,
    }
    filled = [{**defaults, **r} for r in rows]
    df = pd.DataFrame(filled, columns=_RAW_COLS)
    df.to_csv(date_dir / f"{symbol}.csv", index=False)
    return tmp_path


def _base_row(captured_at: str, strike: int = 24100, expiry: str = "28-04-2026",
              otype: str = "CE", symbol: str = "NIFTY") -> dict:
    return {
        "captured_at": captured_at,
        "symbol": symbol,
        "expiry": expiry,
        "strike_price": strike,
        "option_type": otype,
        "bid_price": 95.0,
        "ask_price": 100.0,
        "bid_qty": 150,
        "ask_qty": 150,
        "open_interest": 10000,
        "total_traded_volume": 500,
        "underlying_value": 24125.0,
    }


# ---------------------------------------------------------------------------
# load_day: output columns
# ---------------------------------------------------------------------------

def test_load_day_expected_columns(tmp_data_dir, default_config):
    """load_day returns DataFrame with all required output columns."""
    date_dir = tmp_data_dir / "date=2026-04-24"
    df = load_day(date_dir, "NIFTY", default_config)
    required = {
        "captured_at", "symbol", "expiry", "strike_price", "option_type",
        "bid_price", "ask_price", "bid_qty", "ask_qty",
        "open_interest", "total_traded_volume", "underlying_value",
        "underlying_value_ffill", "time_to_expiry", "mid_price",
    }
    assert required.issubset(set(df.columns)), f"Missing: {required - set(df.columns)}"


# ---------------------------------------------------------------------------
# load_day: symbol filter
# ---------------------------------------------------------------------------

def test_load_day_filters_blank_symbol(tmp_path, default_config):
    """load_day drops rows with blank or NaN symbol."""
    rows = [
        _base_row("2026-04-24T09:30:00", symbol="NIFTY"),
        _base_row("2026-04-24T09:30:00", symbol=""),          # blank
        {**_base_row("2026-04-24T09:30:00"), "symbol": None},  # None/NaN
    ]
    data_dir = _make_raw_csv(tmp_path, rows)
    df = load_day(data_dir / "date=2026-04-24", "NIFTY", default_config)
    assert (df["symbol"] == "NIFTY").all()
    assert len(df) == 1


# ---------------------------------------------------------------------------
# load_day: market hours filter
# ---------------------------------------------------------------------------

def test_load_day_filters_outside_market_hours(tmp_path, default_config):
    """load_day keeps 09:15 and 15:30; drops 09:00 and 15:45."""
    rows = [
        _base_row("2026-04-24T09:00:00"),   # before open → drop
        _base_row("2026-04-24T09:15:00", strike=24100),  # at open → keep
        _base_row("2026-04-24T15:30:00", strike=24150),  # at close → keep
        _base_row("2026-04-24T15:45:00"),   # after close → drop
    ]
    data_dir = _make_raw_csv(tmp_path, rows)
    df = load_day(data_dir / "date=2026-04-24", "NIFTY", default_config)
    times = df["captured_at"].dt.strftime("%H:%M").tolist()
    assert "09:00" not in times
    assert "15:45" not in times
    assert "09:15" in times
    assert "15:30" in times


# ---------------------------------------------------------------------------
# load_day: captured_at timezone
# ---------------------------------------------------------------------------

def test_load_day_captured_at_is_ist_aware(tmp_data_dir, default_config):
    """captured_at is tz-aware and resolves to Asia/Kolkata."""
    date_dir = tmp_data_dir / "date=2026-04-24"
    df = load_day(date_dir, "NIFTY", default_config)
    assert df["captured_at"].dt.tz is not None
    tz_name = str(df["captured_at"].dt.tz)
    assert "Kolkata" in tz_name or "Asia/Kolkata" in tz_name


# ---------------------------------------------------------------------------
# load_day: expiry parsing
# ---------------------------------------------------------------------------

def test_load_day_parses_expiry_as_ist_datetime_at_1530(tmp_path, default_config):
    """expiry is parsed as IST datetime at 15:30, not just a date."""
    rows = [_base_row("2026-04-24T09:30:00", expiry="24-04-2026")]
    data_dir = _make_raw_csv(tmp_path, rows)
    df = load_day(data_dir / "date=2026-04-24", "NIFTY", default_config)
    expected = pd.Timestamp("2026-04-24 15:30:00", tz=_IST)
    assert df["expiry"].iloc[0] == expected


# ---------------------------------------------------------------------------
# load_day: deduplication
# ---------------------------------------------------------------------------

def test_load_day_deduplicates_same_minute_contract(tmp_path, default_config):
    """Three rows at the same (minute, strike, option_type, expiry) → one row kept."""
    rows = [
        _base_row("2026-04-24T09:30:00", strike=24100, otype="CE"),
        _base_row("2026-04-24T09:30:15", strike=24100, otype="CE"),  # same minute
        _base_row("2026-04-24T09:30:45", strike=24100, otype="CE"),  # same minute
    ]
    data_dir = _make_raw_csv(tmp_path, rows)
    df = load_day(data_dir / "date=2026-04-24", "NIFTY", default_config)
    same_contract = df[
        (df["strike_price"] == 24100) &
        (df["option_type"] == "CE") &
        (df["captured_at"].dt.floor("min") == pd.Timestamp("2026-04-24 09:30:00", tz=_IST))
    ]
    assert len(same_contract) == 1


# ---------------------------------------------------------------------------
# discover_new_dates
# ---------------------------------------------------------------------------

def test_discover_new_dates_excludes_processed(tmp_path):
    """discover_new_dates returns only folders not already in processed_dates."""
    (tmp_path / "date=2026-04-21").mkdir()
    (tmp_path / "date=2026-04-22").mkdir()
    (tmp_path / "not-a-date-folder").mkdir()  # should be ignored

    result = discover_new_dates(tmp_path, processed_dates={"2026-04-21"})
    assert result == ["2026-04-22"]


# ---------------------------------------------------------------------------
# Forward-fill contract
# ---------------------------------------------------------------------------

def test_ffill_does_not_alter_non_null_underlying(tmp_path, default_config):
    """underlying_value_ffill equals underlying_value where underlying_value is not NaN."""
    rows = [
        {**_base_row("2026-04-24T09:30:00"), "underlying_value": 24000.0},
        {**_base_row("2026-04-24T10:30:00"), "underlying_value": 24100.0},
    ]
    data_dir = _make_raw_csv(tmp_path, rows)
    df = load_day(data_dir / "date=2026-04-24", "NIFTY", default_config)
    non_null = df[df["underlying_value"].notna()]
    pd.testing.assert_series_equal(
        non_null["underlying_value"].reset_index(drop=True),
        non_null["underlying_value_ffill"].reset_index(drop=True),
        check_names=False,
    )


def test_raw_underlying_value_preserves_nan(tmp_path, default_config):
    """underlying_value stays NaN where source is NaN; only underlying_value_ffill is filled."""
    rows = [
        {**_base_row("2026-04-24T09:30:00"), "underlying_value": 24000.0},
        {**_base_row("2026-04-24T09:31:00"), "underlying_value": None},  # NaN in source
        {**_base_row("2026-04-24T09:32:00"), "underlying_value": 24050.0},
    ]
    data_dir = _make_raw_csv(tmp_path, rows)
    df = load_day(data_dir / "date=2026-04-24", "NIFTY", default_config)
    null_rows = df[df["underlying_value"].isna()]
    assert len(null_rows) >= 1, "Expected at least one NaN in raw underlying_value"
    # ffill should have filled it
    assert not df["underlying_value_ffill"].isna().any()
