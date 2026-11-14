"""Actual account performance: time-weighted return from IBKR's daily NAV.

Unlike the holdings backcast in `history.py` / `analysis.py`, this uses what the
account really was worth each day (cash included) and removes external deposits
and withdrawals, so it is comparable with IBKR's "Time-weighted return".

Daily return:  r(t) = NAV(t) / (NAV(t-1) + flow(t)) - 1
A deposit is treated as available from the start of its day, which also gives
a sensible first day when the account is funded and invested the same day.
Returns are chained:  level(t) = level(t-1) * (1 + r(t)).

With a start date, the base is the last NAV *before* that date, so the start
day's own move counts, as in an IBKR report for a period beginning that day.

`twr_levels` is pure; `inception_series` reads the stored Flex history.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from datetime import date, timedelta

import pandas as pd

from backend.providers.flex import read_history
from config import cfg

INCEPTION = 'Since inception'
_FUNDING_DAYS = 7      # flows this close before the first NAV day are the initial funding


def twr_levels(nav: dict[str, float], flows: list[dict], start: str | None = None) -> pd.Series:
    """Growth index (1.0 at the start) from {ISO day: NAV} and [{'day', 'amount'}] flows.

    `start` (ISO day) counts the return from that day: the index begins at the
    last positive NAV before it. Without a prior NAV the whole history is used.
    `attrs['start_value']` is the capital the index starts from (the base NAV, the
    initial funding, or the first NAV of an already-funded account).
    Raises ValueError rather than guessing when the data cannot support a return.
    """
    days = sorted(day for day, value in nav.items() if math.isfinite(value))
    base_day = None
    if start:
        before = [day for day in days if day < start and nav[day] > 0]
        if before:
            base_day = before[-1]
            days = [base_day] + [day for day in days if day >= start]
    if base_day is None:
        first = next((i for i, day in enumerate(days) if nav[day] > 0), None)
        if first is None:
            raise ValueError('Account NAV is zero throughout the available history')
        days = days[first:]
    first_day, last = days[0], days[-1]

    # A flow on a day without a NAV row (weekend) joins the next NAV day.
    flow_on = dict.fromkeys(days, 0.0)
    for flow in flows:
        if base_day is not None:
            counted = first_day < flow['day'] <= last      # the base day's NAV already includes its flows
        else:
            counted = (date.fromisoformat(first_day) - timedelta(days=_FUNDING_DAYS)).isoformat() <= flow['day'] <= last
        if counted:
            flow_on[days[bisect_left(days, flow['day'])]] += flow['amount']

    funded = base_day is None and flow_on[first_day] > 0
    if funded:     # money arrived on the first day: that day's move is a return too
        anchor = (date.fromisoformat(first_day) - timedelta(days=1)).isoformat()
        rows, prev, pending, start_value = [(anchor, 1.0)], 0.0, days, flow_on[first_day]
    else:
        rows, prev, pending, start_value = [(first_day, 1.0)], nav[first_day], days[1:], nav[first_day]

    level = 1.0
    for day in pending:
        base = prev + flow_on[day]
        if base > 0:
            level *= nav[day] / base
        elif nav[day] != 0:
            raise ValueError(f'Cannot compute a return on {day}: no capital at the start of the day')
        rows.append((day, level))
        prev = nav[day]
    if not math.isfinite(level) or len(rows) < 2:
        raise ValueError('Not enough NAV history to compute a return')

    series = pd.Series([v for _, v in rows], index=pd.to_datetime([d for d, _ in rows]), dtype='float64')
    series.attrs['start_value'] = start_value
    series.attrs['counted_from'] = start if base_day is not None else None
    return series


def _configured_start() -> str | None:
    raw = (cfg.get('performance') or {}).get('start_date')
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10]).isoformat()
    except ValueError:
        raise ValueError(f'performance.start_date must be a date like 2026-11-02, not {raw!r}') from None


def inception_series(account_id: str | None, start: str | None = None) -> pd.Series:
    """Time-weighted growth index for the account, from the stored Flex NAV history.

    `start` defaults to the configured `performance.start_date` (if any).
    """
    state = (read_history()['accounts'].get(account_id) or {}) if account_id else {}
    if not state.get('nav'):
        raise ValueError('No daily NAV from Flex yet. Add "Net Asset Value (NAV) in Base" '
                         'to the Flex query and wait for the next sync.')
    if 'flows' not in state:
        raise ValueError('No deposit history from Flex yet. Add "Cash Transactions" (Detail) '
                         'to the Flex query and wait for the next sync.')
    return twr_levels(state['nav'], state['flows'], start or _configured_start())
