import pytest
from pathlib import Path
from pipeline.config import Config, default_config


def test_config_loads_all_required_fields(tmp_path):
    """Config construction with all required fields raises no errors."""
    out = tmp_path / "outputs"
    cfg = default_config(data_dir=tmp_path, output_dir=out)
    assert cfg.data_dir == tmp_path
    assert cfg.output_dir == out
    assert cfg.symbols
    assert cfg.market_open
    assert cfg.market_close
    assert cfg.resample_freq
    assert cfg.rolling_windows
    assert cfg.ann_factor
    assert cfg.brentq_bounds
    assert cfg.default_rate
    assert cfg.atm_increments
    assert cfg.timezone


def test_config_atm_increments_has_all_symbols(tmp_path):
    """atm_increments contains entries for NIFTY, BANKNIFTY, and FINNIFTY."""
    cfg = default_config(data_dir=tmp_path, output_dir=tmp_path / "out")
    assert "NIFTY" in cfg.atm_increments
    assert "BANKNIFTY" in cfg.atm_increments
    assert "FINNIFTY" in cfg.atm_increments
    assert cfg.atm_increments["NIFTY"] == 50
    assert cfg.atm_increments["BANKNIFTY"] == 100
    assert cfg.atm_increments["FINNIFTY"] == 50


def test_config_ann_factor_is_365(tmp_path):
    """ann_factor must be 365 (calendar-day convention, NOT 252)."""
    cfg = default_config(data_dir=tmp_path, output_dir=tmp_path / "out")
    assert cfg.ann_factor == 365


def test_config_timezone_is_ist(tmp_path):
    """timezone must be 'Asia/Kolkata'."""
    cfg = default_config(data_dir=tmp_path, output_dir=tmp_path / "out")
    assert cfg.timezone == "Asia/Kolkata"


def test_config_raises_on_missing_data_dir(tmp_path):
    """Config raises FileNotFoundError when data_dir does not exist."""
    missing = tmp_path / "does_not_exist"
    with pytest.raises(FileNotFoundError, match=str(missing)):
        Config(
            data_dir=missing,
            output_dir=tmp_path / "out",
            symbols=["NIFTY"],
            market_open="09:15",
            market_close="15:30",
            resample_freq="5min",
            rolling_windows=[5, 10, 21],
            ann_factor=365,
            brentq_bounds=(1e-6, 10.0),
            default_rate=0.065,
            dividend_yield=0.0,
            atm_increments={"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50},
            timezone="Asia/Kolkata",
        )
