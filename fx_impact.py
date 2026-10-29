"""Daily currency translation estimate for a position's current exposure.

Rates are base-currency units per one unit of the instrument currency.  The
entire price/FX cross-term is assigned to FX by valuing at the current price.
This is not IBKR's daily P&L decomposition or since-purchase attribution.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def valid_positive(value) -> bool:
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError):
        return False


def quote_to_base(now, previous, *, inverted=False):
    """Normalize a direct or inverse FX pair to base per instrument unit."""
    if not valid_positive(now) or not valid_positive(previous):
        return None
    if inverted:
        return {'now': 1 / float(now), 'previous': 1 / float(previous)}
    return {'now': float(now), 'previous': float(previous)}


def previous_daily_close(bars, today: date):
    """Use the latest completed daily bar dated before the account's FX day."""
    candidates = []
    for bar in bars:
        raw = bar.date
        try:
            if isinstance(raw, datetime):
                bar_date = raw.date()
            elif isinstance(raw, date):
                bar_date = raw
            else:
                label = str(raw)
                bar_date = (datetime.fromisoformat(label).date() if '-' in label
                            else datetime.strptime(label[:8], '%Y%m%d').date())
        except (ValueError, TypeError):
            continue
        if today - timedelta(days=5) <= bar_date < today and valid_positive(bar.close):
            candidates.append((bar_date, float(bar.close)))
    return max(candidates)[1] if candidates else None


def fresh_quote_time(value, now=None, max_age=timedelta(minutes=10)) -> bool:
    if not isinstance(value, datetime):
        return False
    now = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        return False
    age = now - value.astimezone(timezone.utc)
    return -timedelta(minutes=1) <= age <= max_age


def fx_session_open(now=None) -> bool:
    """Reject weekend and daily New York rollover quotes that can be frozen."""
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo('America/New_York'))
    weekday = local.weekday()
    minute = local.hour * 60 + local.minute
    if weekday == 5 or (weekday == 6 and minute < 17 * 60):
        return False
    if weekday == 4 and minute >= 17 * 60:
        return False
    return not (17 * 60 <= minute < 17 * 60 + 15)


def total_fx_impact(position: dict, base_currency: str, rates: dict | None = None) -> tuple[float | None, str]:
    """Since-purchase currency translation effect in base currency.

    Needs the position's base-currency market value and unrealized P&L (IBKR
    per-position P&L, which embeds the FX rate at purchase).  With
    ``cost_base = value_base - unrealized_base`` and the native price/cost ratio
    ``r = price / unit_cost``, the price move at the purchase-time rate is
    ``(r - 1) * cost_base``, so the rest is FX::

        fx = value_base - r * cost_base

    As in the daily estimate, the price/FX cross-term is assigned to FX.  When a
    fresh FX rate is available the base value is cross-checked against
    ``quantity * multiplier * native_price * rate`` to catch unit mismatches.
    """
    currency = str(position.get('currency') or '').upper()
    base = str(base_currency or '').upper()
    if not currency or not base:
        return None, 'Instrument or account currency is unavailable'
    if currency == base:
        return 0.0, 'No currency conversion'
    native_price = position.get('fx_native_price')
    if position.get('fx_price_stale') or not valid_positive(native_price):
        return None, 'Current position price is missing or stale'
    if not position.get('price_unit_verified', False):
        return None, 'Native price units are unverified for this contract'
    multiplier = position.get('multiplier')
    if not valid_positive(multiplier):
        return None, 'Contract multiplier is unavailable'
    avg_cost = position.get('avg_cost')
    if not valid_positive(avg_cost):
        return None, 'Average cost is unavailable'
    try:
        value_base = float(position['base_market_value'])
        unreal_base = float(position['base_unrealized_pnl'])
        quantity = float(position['quantity'])
    except (KeyError, TypeError, ValueError):
        return None, 'Base-currency position value or P&L is unavailable'
    if not all(math.isfinite(v) for v in (value_base, unreal_base, quantity)) or not quantity:
        return None, 'Base-currency position value or P&L is unavailable'
    rate = (rates or {}).get(currency)
    if rate and valid_positive(rate.get('now')):
        expected = quantity * float(multiplier) * float(native_price) * float(rate['now'])
        if not expected or abs(value_base / expected - 1) > 0.03:
            return None, 'Base-currency value does not match the native price and FX rate'
    cost_base = value_base - unreal_base
    if not cost_base:
        return None, 'Base-currency cost is unavailable'
    ratio = float(native_price) / (float(avg_cost) / float(multiplier))
    value = value_base - ratio * cost_base
    return (round(value, 2), '') if math.isfinite(value) else (None, 'Invalid FX estimate')


def _trade_date(trade) -> date | None:
    try:
        return date.fromisoformat(str(trade.get('time'))[:10])
    except (TypeError, ValueError):
        return None


def merge_trades(*sources) -> list:
    """Union of trade lists, collapsing the same fill reported by two sources.

    Live executions and uploaded CSV rows format their timestamps differently, so
    fills are matched on (ticker, side, shares, price, date).  Within a key the
    source holding the most fills wins, which keeps genuine repeated same-day
    fills while dropping the overlap between the 7-day live window and a CSV.
    """
    best: dict = {}
    for source in sources:
        groups: dict = {}
        for t in source or []:
            day = _trade_date(t)
            if day is None or t.get('side') not in ('BUY', 'SELL'):
                continue
            try:
                key = (t.get('ticker'), t['side'], round(float(t['shares']), 4),
                       round(float(t.get('price') or 0), 2), day)
            except (KeyError, TypeError, ValueError):
                continue
            groups.setdefault(key, []).append(t)
        for key, rows in groups.items():
            if len(rows) > len(best.get(key, ())):
                best[key] = rows
    return sorted((t for rows in best.values() for t in rows), key=lambda t: str(t.get('time')))


def open_lots(trades, ticker: str) -> list[tuple[date, float]]:
    """FIFO open lots (purchase date, signed quantity) rebuilt from a ticker's trades."""
    lots: list[list] = []
    for t in sorted((t for t in trades if t.get('ticker') == ticker), key=lambda t: str(t.get('time'))):
        day = _trade_date(t)
        try:
            qty = float(t['shares']) * (1 if t['side'] == 'BUY' else -1)
        except (KeyError, TypeError, ValueError):
            continue
        if day is None or not qty:
            continue
        while qty and lots and lots[0][1] * qty < 0:
            matched = min(abs(qty), abs(lots[0][1]))
            lots[0][1] -= math.copysign(matched, lots[0][1])
            qty -= math.copysign(matched, qty)
            if abs(lots[0][1]) < 1e-9:
                lots.pop(0)
        if abs(qty) > 1e-9:
            lots.append([day, qty])
    return [(day, qty) for day, qty in lots]


def total_fx_from_trades(position: dict, base_currency: str, trades, rate_on, rate_now) -> tuple[float | None, str]:
    """Since-purchase currency effect rebuilt from trade history.

    Fallback for when TWS cannot supply base-currency cost.  Open FIFO lots are
    rebuilt from ``trades``; each lot contributes ``qty * price_now * (rate_now -
    rate_at_purchase)`` (same convention as the other estimates: the price/FX
    cross-term goes to FX).  ``rate_on(date)`` and ``rate_now`` are base-per-unit
    rates; purchase-date rates are daily closes, so this is approximate.
    """
    currency = str(position.get('currency') or '').upper()
    base = str(base_currency or '').upper()
    if not currency or not base:
        return None, 'Instrument or account currency is unavailable'
    if currency == base:
        return 0.0, 'No currency conversion'
    native_price = position.get('fx_native_price')
    if position.get('fx_price_stale') or not valid_positive(native_price):
        return None, 'Current position price is missing or stale'
    if not position.get('price_unit_verified', False):
        return None, 'Native price units are unverified for this contract'
    multiplier = position.get('multiplier')
    if not valid_positive(multiplier):
        return None, 'Contract multiplier is unavailable'
    if not valid_positive(rate_now):
        return None, 'Current FX rate is unavailable'
    try:
        quantity = float(position['quantity'])
    except (KeyError, TypeError, ValueError):
        return None, 'Position quantity is unavailable'
    if not math.isfinite(quantity) or not quantity:
        return None, 'Position quantity is unavailable'
    lots = open_lots(trades, position.get('ticker'))
    if not lots:
        return None, 'No trade history for this ticker — upload a transaction CSV'
    covered = sum(qty for _, qty in lots)
    if abs(covered - quantity) > max(1e-6, 1e-4 * abs(quantity)):
        return None, (f'Trade history covers {covered:g} of {quantity:g} shares — '
                      'upload the full transaction history')
    value = 0.0
    for day, qty in lots:
        rate = rate_on(day)
        if not valid_positive(rate):
            return None, f'Historical FX rate for {day.isoformat()} is unavailable'
        value += qty * float(multiplier) * float(native_price) * (float(rate_now) - float(rate))
    return (round(value, 2), '') if math.isfinite(value) else (None, 'Invalid FX estimate')


def daily_fx_impact(position: dict, base_currency: str, rates: dict) -> tuple[float | None, str]:
    """Return (base-currency estimate, reason when unavailable)."""
    currency = str(position.get('currency') or '').upper()
    base = str(base_currency or '').upper()
    if not currency or not base:
        return None, 'Instrument or account currency is unavailable'
    if currency == base:
        return 0.0, 'No currency conversion'
    native_price = position.get('fx_native_price', position.get('current_price'))
    if (position.get('fx_price_stale') or
            (position.get('fx_native_price') is None and position.get('price_stale')) or
            not valid_positive(native_price)):
        return None, 'Current position price is missing or stale'
    if not position.get('price_unit_verified', False):
        return None, 'Native price units are unverified for this contract'
    multiplier = position.get('multiplier')
    if not valid_positive(multiplier):
        return None, 'Contract multiplier is unavailable'
    rate = rates.get(currency)
    if not rate or not valid_positive(rate.get('now')) or not valid_positive(rate.get('previous')):
        return None, 'Current or previous FX rate is missing or stale'
    try:
        quantity = float(position['quantity'])
    except (KeyError, TypeError, ValueError):
        return None, 'Position quantity is unavailable'
    if not math.isfinite(quantity):
        return None, 'Position quantity is unavailable'
    value = quantity * float(multiplier) * float(native_price) * (
        float(rate['now']) - float(rate['previous'])
    )
    return (round(value, 2), '') if math.isfinite(value) else (None, 'Invalid FX estimate')
