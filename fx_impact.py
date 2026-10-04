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
