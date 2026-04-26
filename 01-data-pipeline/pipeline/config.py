"""Configuration dataclass for the NSE options pipeline.

All instances should be constructed via default_config(); Config is the single
source of truth for paths, symbols, and numeric constants across all modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytz


@dataclass
class Config:
    data_dir: Path
    output_dir: Path
    symbols: list[str]
    market_open: str
    market_close: str
    resample_freq: str
    rolling_windows: list[int]
    ann_factor: int
    brentq_bounds: tuple[float, float]
    default_rate: float
    dividend_yield: float
    atm_increments: dict[str, int]
    timezone: str

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        self.output_dir = Path(self.output_dir)
        if not self.data_dir.is_dir():
            raise FileNotFoundError(
                f"data_dir does not exist or is not a directory: {self.data_dir.resolve()}"
            )
        if self.ann_factor <= 0:
            raise ValueError(f"ann_factor must be positive, got {self.ann_factor}")
        lo, hi = self.brentq_bounds
        if lo >= hi:
            raise ValueError(f"brentq_bounds lower bound must be < upper bound, got {self.brentq_bounds}")
        # Validate timezone string eagerly so misconfiguration is caught at construction
        pytz.timezone(self.timezone)
        self.output_dir.mkdir(parents=True, exist_ok=True)


def default_config(data_dir: Path, output_dir: Path) -> Config:
    """Return a Config with all pipeline defaults for NIFTY/BANKNIFTY/FINNIFTY."""
    return Config(
        data_dir=data_dir,
        output_dir=output_dir,
        symbols=["NIFTY", "BANKNIFTY", "FINNIFTY"],
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
