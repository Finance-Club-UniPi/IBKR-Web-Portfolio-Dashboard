"""Prepare portfolio-report data: period returns and benchmark comparisons.

fetch_performance reuses the account-equity calculation (and price cache) of the dashboard."""

from __future__ import annotations

import pandas as pd

from backend.portfolio.history import compare_frame

BENCHMARK_NAME = 'S&P 500'
CHART_DAYS = 7               # calendar days shown in the weekly chart
_START_TOLERANCE = pd.Timedelta(days=5)   # a '1y' download may start a few days late

_SECTOR_SHORT = {
    'Communication Services': 'Comm. Services',
    'Consumer Cyclical': 'Cons. Cyclical',
    'Consumer Defensive': 'Cons. Defensive',
    'Financial Services': 'Financials',
    'Basic Materials': 'Materials',
}


def short_sector(name: str | None) -> str:
    return _SECTOR_SHORT.get(name or '', name or '—')


# ── Sector & geography ───────────────────────────────────────────────────────

# ── Performance versus the S&P 500 ───────────────────────────────────────────

def _value_at(series: pd.Series, when: pd.Timestamp) -> float | None:
    """Last value on or before `when`; None if the series starts well after it."""
    if when < series.index[0] - _START_TOLERANCE:
        return None
    if when < series.index[0]:
        return float(series.iloc[0])
    return float(series.asof(when))


def window_return(series: pd.Series | None, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    if series is None or series.empty:
        return None
    first, last = _value_at(series, start), _value_at(series, end)
    if not first or last is None:
        return None
    return (last / first - 1) * 100


def period_returns(value: pd.Series, bench: pd.Series | None):
    """[(label, portfolio %, S&P 500 % | None)] for 1W/1M/3M/YTD/1Y ending at the last close.

    A period the history does not cover comes back as None for that side.
    """
    end = value.index[-1]
    starts = [
        ('1W', end - pd.Timedelta(days=7)),
        ('1M', end - pd.DateOffset(months=1)),
        ('3M', end - pd.DateOffset(months=3)),
        ('YTD', pd.Timestamp(end.year - 1, 12, 31)),
        ('1Y', end - pd.DateOffset(years=1)),
    ]
    return [(label, window_return(value, start, end), window_return(bench, start, end))
            for label, start in starts]


def weekly_frame(value: pd.Series, bench: pd.Series | None, days: int = CHART_DAYS) -> pd.DataFrame:
    """Cumulative % return of the portfolio (and S&P 500) over the last `days`.

    Starts from the last close on or before the window so day one shows the
    first move; empty when fewer than two common dates exist.
    """
    start = value.index[-1] - pd.Timedelta(days=days)
    before = value.index[value.index <= start]
    window = value.loc[before[-1]:] if len(before) else value
    others = {BENCHMARK_NAME: bench} if bench is not None else {}
    return compare_frame(window, others, 'pct')


def fetch_performance(positions: list[dict], account: dict | None):
    """(account-equity series, S&P 500 series | None, excluded), or None when unavailable.

    The same account-equity scenarios the Performance page shows; no positive
    account equity (or any missing input) means no result, never a holdings-only substitute.
    """
    from backend.portfolio.analysis import account_history
    try:
        # exposure_returns rejects non-positive equity, the same rule the Performance page uses.
        return account_history({'positions': positions, 'account': account or {}}, '1Y')
    except ValueError:
        return None


# ── Chart ────────────────────────────────────────────────────────────────────
