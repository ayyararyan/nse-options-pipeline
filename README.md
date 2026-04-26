# NSE Options Analytics Pipeline

A weekly-updating analytics pipeline and dashboard for NSE (National Stock Exchange) options chain data. Computes implied volatility, realized volatility, variance risk premium, order flow imbalance, and liquidity metrics for NIFTY, BANKNIFTY, and FINNIFTY.

## Project Structure

```
.
├── 01-data-pipeline/        # Data ingestion and metric computation (planned)
│   ├── spec.md              # Full technical specification
│   ├── claude-plan.md       # Detailed implementation plan
│   └── sections/            # TDD implementation sections (01–10)
├── 02-vol-surface-fitting/  # eSSVI and SABR vol surface fitting (planned)
│   └── spec.md
├── 03-dashboard/            # Streamlit interactive dashboard (planned)
│   └── spec.md
├── requirements.md          # Original project requirements
└── project-manifest.md      # Project-level roadmap
```

## What Gets Built

### Split 01 — Data Pipeline (`01-data-pipeline/`)

A Python pipeline that runs on a weekly cron schedule to process NSE options chain CSVs stored under `NSEI-Data/date=YYYY-MM-DD/{SYMBOL}.csv`.

**Modules:**

| Module | What it does |
|--------|-------------|
| `pipeline/config.py` | `Config` dataclass — paths, symbols, market hours, constants |
| `pipeline/ingestion.py` | Load and clean one day's options chain CSV |
| `pipeline/iv.py` | Black-Scholes implied volatility via Brent solver (calendar-365 T, discounted intrinsic bound) |
| `pipeline/realized_vol.py` | Parzen BNHLS realized kernel, overnight return, rolling RV in variance space |
| `pipeline/vrp.py` | ATM IV extraction, 30-day constant-maturity interpolation, VRP = IV² − RV² |
| `pipeline/ofi.py` | Cont, Kukanov & Stoikov (2014) three-case Order Flow Imbalance |
| `pipeline/liquidity.py` | Bid-ask spread, depth, ATM/chain spread aggregates, put-call OI ratio |
| `pipeline/rates.py` | RBI T-bill rate fetcher with three-tier fallback (API → CSV → default) |
| `pipeline/writer.py` | Idempotent CSV appender with key-dedup guard; atomic manifest via `os.replace` |
| `run_pipeline.py` | Top-level orchestrator with `filelock` concurrent-run guard |

**Key design constraints:**
- **Calendar-365 annualization throughout** — IV uses `T = seconds / (365.25 × 86400)`, RV annualized `× 365`. Mixing with trading-252 creates a ~3% systematic VRP bias.
- **Idempotent by `(date, symbol)`** — manifest + CSV key-dedup ensure safe reruns and retries after partial failure.
- **Fractional T on expiry day** — `T = max(0, (expiry_15:30_IST − snapshot).total_seconds() / ...)` — integer `.days` zeros out expiry-day IV.
- **OFI grouping key includes `expiry`** — NIFTY has weekly and monthly expiries at the same strikes; omitting expiry causes cross-expiry collisions.

**Outputs** (written to `outputs/`):

| File | Content |
|------|---------|
| `{SYMBOL}_realized_vol.csv` | Daily RK realized vol + rolling windows |
| `{SYMBOL}_vrp.csv` | Daily VRP in variance and vol space |
| `{SYMBOL}_ofi.csv` | Snapshot + daily OFI summary |
| `{SYMBOL}_liquidity.csv` | Daily liquidity aggregates per expiry |
| `options_chain/{SYMBOL}/date=YYYY-MM-DD.csv` | Full IV-enriched chain per date |
| `rates.csv` | Historical risk-free rates |
| `processed_dates.json` | Idempotency manifest |

### Split 02 — Vol Surface Fitting (`02-vol-surface-fitting/`)

Fits eSSVI and SABR implied volatility surfaces to the computed IV chains. Produces parameterized surfaces for 3D visualization.

### Split 03 — Dashboard (`03-dashboard/`)

Streamlit dashboard with interactive time series plots (IV, RV, VRP, OFI, liquidity) and 3D volatility surface visualizations. Updates weekly from pipeline outputs.

## Implementation Plan

The pipeline is fully planned in `01-data-pipeline/sections/` as 10 TDD-driven implementation sections. Each section contains tests first, then implementation spec.

To implement:

```bash
# Requires Claude Code with deep-implement plugin
/deep-implement @01-data-pipeline/sections/index.md
```

Or implement manually section by section:

1. `section-01-project-setup` — scaffold, `pyproject.toml`, test fixtures
2. `section-02-config` — `Config` dataclass
3. `section-03-ingestion` — CSV loading and cleaning
4. `section-04-iv-computation`, `section-05-realized-vol`, `section-07-ofi`, `section-08-liquidity`, `section-09-rates-writer` — parallel
5. `section-06-vrp` — requires 04 and 05
6. `section-10-pipeline-orchestration` — requires all above

## Data Format

Input data lives in `NSEI-Data/date=YYYY-MM-DD/{SYMBOL}.csv` (not tracked in git — too large).

Each CSV has per-snapshot options chain rows with columns including: `captured_at`, `symbol`, `expiry`, `strike_price`, `option_type` (CE/PE), `bid_price`, `ask_price`, `bid_qty`, `ask_qty`, `open_interest`, `total_traded_volume`, `underlying_value`.

## Dependencies

```toml
pandas >= 2.0
numpy >= 1.26
scipy >= 1.12
requests >= 2.31
filelock >= 3.13
pytz >= 2024.1
```

Runtime managed with [uv](https://github.com/astral-sh/uv). Tests run with `uv run pytest`.

## References

- Barndorff-Nielsen, Hansen, Lunde & Shephard (2008) — Realized kernels in practice
- Cont, Kukanov & Stoikov (2014) — The price impact of order book events
- Gatheral & Jacquier (2014) — Arbitrage-free SVI volatility surfaces
- Hagan et al. (2002) — Managing smile risk (SABR)
