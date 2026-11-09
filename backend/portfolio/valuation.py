"""Dated current valuation, independent of yesterday's FX and trade history."""

import math
from datetime import datetime, timedelta, timezone

from backend.portfolio.fx import valid_positive
from backend.storage.cache import _MISS, _cache_read, _cache_write

MAX_AGE = timedelta(days=5)


def recent(stamp, now=None):
    try:
        value = datetime.fromisoformat(stamp)
        return value.tzinfo is not None and timedelta(minutes=-1) <= (
            (now or datetime.now(timezone.utc)) - value) <= MAX_AGE
    except (TypeError, ValueError):
        return False


def position_key(position, account=''):
    return f"{position.get('account') or account}:{position.get('conId') or position['ticker']}"


def value_positions(positions, account, rates):
    """Use dated native prices/current FX; retain only compatible successful values."""
    base = account.get('base_currency', 'EUR')
    for p in positions:
        p['position_id'] = position_key(p, account.get('account_id', ''))
        ccy = p.get('currency') or base
        rate = {'now': 1.0, 'as_of': p.get('valuation_as_of')} if ccy == base else rates.get(ccy, {})
        key = ('position_value_v2', p['position_id'], base, ccy, p.get('quantity'))
        price = p.get('fx_native_price')
        stamp = p.get('valuation_as_of')
        usable = (math.isfinite(float(p.get('quantity') or 0)) and p.get('price_unit_verified') and recent(stamp) and recent(rate.get('as_of'))
                  and valid_positive(price) and valid_positive(rate.get('now'))
                  and valid_positive(p.get('multiplier')) and account.get('account_ready', True))
        if usable:
            value = float(p['quantity']) * float(p['multiplier']) * float(price) * float(rate['now'])
            valuation = {'base_market_value': value, 'valuation_as_of': min(stamp, rate['as_of']),
                         'valuation_source': p.get('valuation_source', 'IBKR account snapshot'),
                         'valuation_price': price, 'valuation_rate': rate['now']}
            _cache_write(key, valuation, MAX_AGE.total_seconds())
        else:
            valuation = _cache_read(key)
            if valuation is _MISS or not recent(valuation.get('valuation_as_of')):
                p['base_market_value'] = None
                p['valuation_reason'] = ('Account update is not ready' if not account.get('account_ready', True)
                                         else 'Native price or current FX rate unavailable')
                continue
            valuation = {**valuation, 'valuation_source': 'Last successful valuation'}
        p.update(valuation)
        p['current_price'] = p['valuation_price']
        p['fx_native_price'] = p['valuation_price']
        p['price_unit_verified'] = True
        p['fx_price_stale'] = False
        p['valuation_reason'] = f"{p['valuation_source']} · {p['valuation_as_of']}"
    return positions


def refresh_valuations(positions, account, rates):
    """Populate the compatible-value cache with dated Yahoo prices/FX off-thread."""
    import copy

    from backend.portfolio.history import get_compare_history
    from backend.providers.market_data import get_position_history

    positions, account, rates = copy.deepcopy((positions, account, rates))
    base = account.get('base_currency', 'EUR')
    for p in positions:
        if p.get('base_market_value') is not None:
            continue
        currency = p.get('currency') or base
        if currency != base and (not recent(rates.get(currency, {}).get('as_of')) or
                                 not valid_positive(rates.get(currency, {}).get('now'))):
            series = get_compare_history(f'{currency}{base}=X', '1M')
            if series is not None and not series.empty:
                rates[currency] = {'now': float(series.iloc[-1]),
                                   'as_of': series.index[-1].tz_localize('UTC').isoformat(),
                                   'source': 'Yahoo daily FX'}
        if not p.get('price_unit_verified') or not recent(p.get('valuation_as_of')):
            history = get_position_history(p, '1mo')
            if history:
                p.update(fx_native_price=history['prices'][-1], price_unit_verified=True,
                         valuation_as_of=datetime.fromisoformat(history['dates'][-1]).replace(tzinfo=timezone.utc).isoformat(),
                         valuation_source='Yahoo daily price')
    value_positions(positions, account, rates)
