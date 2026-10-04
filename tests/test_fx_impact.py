"""Daily translation estimate: sign, units, quote direction, and UI contract."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from data_processor import process_positions
from demo_data import build_demo_payload
from fx_impact import daily_fx_impact, fresh_quote_time, fx_session_open, previous_daily_close, quote_to_base


def position(**changes):
    result = dict(currency='USD', quantity=10, multiplier=2, current_price=100,
                  price_stale=False, price_unit_verified=True)
    result.update(changes)
    return result


def test_signed_impact_and_contract_multiplier():
    rates = {'USD': {'now': 0.92, 'previous': 0.90}}
    assert daily_fx_impact(position(), 'EUR', rates) == (40.0, '')
    assert daily_fx_impact(position(quantity=-10), 'EUR', rates) == (-40.0, '')
    assert daily_fx_impact(position(), 'EUR', {'USD': {'now': 0.88, 'previous': 0.90}}) == (-40.0, '')
    # A verified native snapshot takes precedence over unverified TWS portfolio price.
    assert daily_fx_impact(position(current_price=500, fx_native_price=100), 'EUR', rates) == (40.0, '')


def test_inverse_pair_and_non_eur_third_currency():
    inverse = quote_to_base(1.25, 1.20, inverted=True)
    assert daily_fx_impact(position(currency='GBP'), 'USD', {'GBP': inverse})[0] == pytest.approx(-66.67, abs=0.01)
    direct = quote_to_base(1.10, 1.05)
    assert daily_fx_impact(position(currency='GBP'), 'CHF', {'GBP': direct})[0] == 100.0


def test_same_currency_and_unavailable_inputs():
    assert daily_fx_impact(position(currency='CHF', price_stale=True), 'CHF', {}) == (0.0, 'No currency conversion')
    for changes in (dict(price_stale=True), dict(fx_price_stale=True),
                    dict(current_price=float('nan')), dict(price_unit_verified=False),
                    dict(multiplier=None)):
        value, reason = daily_fx_impact(position(**changes), 'EUR', {'USD': {'now': .9, 'previous': .8}})
        assert value is None and reason
    for rates in ({}, {'USD': {'now': 0, 'previous': .9}},
                  {'USD': {'now': .9, 'previous': float('nan')}}):
        value, reason = daily_fx_impact(position(), 'EUR', rates)
        assert value is None and 'FX rate' in reason


def test_prior_completed_bar_and_freshness():
    bars = [SimpleNamespace(date='20261002', close=1.2),
            SimpleNamespace(date='20261005', close=1.3)]
    assert previous_daily_close(bars, date(2026, 10, 5)) == 1.2
    assert previous_daily_close(bars, date(2026, 10, 12)) is None
    now = datetime.now(timezone.utc)
    assert fresh_quote_time(now, now)
    assert not fresh_quote_time(now - timedelta(minutes=11), now)
    assert not fresh_quote_time(None, now)
    assert not fx_session_open(datetime(2026, 10, 3, 15, tzinfo=timezone.utc))
    assert fx_session_open(datetime(2026, 10, 5, 15, tzinfo=timezone.utc))


def test_demo_path_and_numeric_sortable_column():
    raw = build_demo_payload('balanced')
    df = process_positions(raw['positions'], raw['market_data'],
                           base_currency=raw['account']['base_currency'],
                           daily_fx_rates=raw['daily_fx_rates'])
    assert df.loc[df.ticker == 'ASML', 'fx_today'].iloc[0] == 0
    assert df.loc[df.ticker == 'AAPL', 'fx_today'].iloc[0] < 0
    assert df['fx_today'].dtype.kind == 'f'

    from dashboard_core.summary import register

    callbacks = {}

    class App:
        def callback(self, *args, **kwargs):
            def save(fn):
                callbacks[fn.__name__] = fn
                return fn
            return save

    register(App())
    table, _, _ = callbacks['update_holdings']({
        'positions': df.to_dict('records'), 'account': raw['account']}, 'connected')
    fx_col = next(c for c in table.columns if c['id'] == 'fx_today')
    assert fx_col['name'] == 'FX today (EUR)'
    assert fx_col['type'] == 'numeric' and table.sort_action == 'native'
    assert fx_col['format']['nully'] == '—'
    assert next(r for r in table.data if r['ticker'] == 'ASML')['fx_today'] == 0
    assert 'previous FX daily close' in table.tooltip_header['fx_today']
