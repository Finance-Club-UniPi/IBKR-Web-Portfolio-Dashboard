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


def total_position(**changes):
    # 10 USD shares bought at 100 when EUR/USD was 1.25 (0.80 EUR per USD); now 120 at 0.90.
    result = dict(currency='USD', quantity=10, multiplier=1, avg_cost=100, fx_native_price=120,
                  price_unit_verified=True, base_market_value=1080.0, base_unrealized_pnl=280.0)
    result.update(changes)
    return result


def test_total_fx_impact_matches_closed_form():
    from fx_impact import total_fx_impact

    # q * P1 * (X1 - X0) = 10 * 120 * (0.90 - 0.80)
    assert total_fx_impact(total_position(), 'EUR') == (120.0, '')
    # Cross-check against a fresh rate passes when units agree (10 * 120 * 0.90 = 1080).
    assert total_fx_impact(total_position(), 'EUR', {'USD': {'now': 0.90, 'previous': 0.89}}) == (120.0, '')
    # Short positions keep the sign convention: FX loss on a short when the USD rises.
    short = total_position(quantity=-10, base_market_value=-1080.0, base_unrealized_pnl=-280.0)
    assert total_fx_impact(short, 'EUR')[0] == -120.0


def test_total_fx_impact_unavailable_inputs():
    from fx_impact import total_fx_impact

    assert total_fx_impact(total_position(currency='EUR'), 'EUR') == (0.0, 'No currency conversion')
    for changes in (dict(base_market_value=None), dict(base_unrealized_pnl=float('nan')),
                    dict(avg_cost=0), dict(fx_native_price=None), dict(price_unit_verified=False),
                    dict(multiplier=None), dict(fx_price_stale=True),
                    dict(base_market_value=1080.0, base_unrealized_pnl=1080.0)):
        value, reason = total_fx_impact(total_position(**changes), 'EUR')
        assert value is None and reason, changes
    # Base value in the wrong units (e.g. USD, not EUR) is rejected by the rate cross-check.
    value, reason = total_fx_impact(total_position(base_market_value=1200.0), 'EUR',
                                    {'USD': {'now': 0.90, 'previous': 0.89}})
    assert value is None and 'does not match' in reason


def test_demo_total_fx_matches_price_times_rate_move():
    raw = build_demo_payload('balanced')
    df = process_positions(raw['positions'], raw['market_data'], base_currency='EUR',
                           daily_fx_rates=raw['daily_fx_rates'])
    aapl = df[df.ticker == 'AAPL'].iloc[0]
    assert aapl['fx_total'] == pytest.approx(60 * 195.5 * (1 / 1.08 - 1 / 1.12), abs=0.5)
    assert df.loc[df.ticker == 'ASML', 'fx_total'].iloc[0] == 0
    assert df['fx_total'].notna().all()


def _trade(side, shares, day, ticker='AAPL'):
    return {'ticker': ticker, 'side': side, 'shares': shares, 'price': 100, 'time': f'{day}T10:00:00'}


def test_open_lots_are_fifo_and_handle_partial_sells():
    from datetime import date

    from fx_impact import open_lots

    trades = [_trade('BUY', 10, '2026-01-05'), _trade('BUY', 10, '2026-02-05'),
              _trade('SELL', 15, '2026-03-05'), _trade('BUY', 1, '2026-03-06', 'MSFT')]
    assert open_lots(trades, 'AAPL') == [(date(2026, 2, 5), 5.0)]


def test_total_fx_from_trades_sums_lots():
    from fx_impact import total_fx_from_trades

    rates = {'2026-01-05': 0.80, '2026-02-05': 0.85}
    rate_on = lambda d: rates[d.isoformat()]  # noqa: E731
    trades = [_trade('BUY', 6, '2026-01-05'), _trade('BUY', 4, '2026-02-05')]
    # 120 * (6 * (0.90 - 0.80) + 4 * (0.90 - 0.85)) = 120 * 0.8
    assert total_fx_from_trades(total_position(ticker='AAPL'), 'EUR', trades, rate_on, 0.90) == (96.0, '')


def test_total_fx_from_trades_reports_why_it_is_unavailable():
    from fx_impact import total_fx_from_trades

    pos = total_position(ticker='AAPL')
    rate_on = lambda d: 0.8  # noqa: E731
    value, reason = total_fx_from_trades(pos, 'EUR', [], rate_on, 0.9)
    assert value is None and 'No trade history' in reason
    value, reason = total_fx_from_trades(pos, 'EUR', [_trade('BUY', 4, '2026-01-05')], rate_on, 0.9)
    assert value is None and 'covers 4 of 10' in reason
    full = [_trade('BUY', 10, '2026-01-05')]
    assert total_fx_from_trades(pos, 'EUR', full, lambda d: None, 0.9)[0] is None
    assert total_fx_from_trades(pos, 'EUR', full, rate_on, None)[0] is None
    assert total_fx_from_trades(total_position(ticker='AAPL', currency='EUR'), 'EUR', full, rate_on, .9)[0] == 0.0


def test_merge_trades_dedupes_across_sources_but_keeps_repeat_fills():
    from fx_impact import merge_trades

    live = [{'ticker': 'AAPL', 'side': 'BUY', 'shares': 5, 'price': 100.0, 'time': '2026-03-02T15:00:00+00:00'}]
    csv = [_trade('BUY', 5, '2026-03-02'), _trade('BUY', 5, '2026-03-02')]
    csv[0]['price'] = csv[1]['price'] = 100.0
    assert len(merge_trades(live, csv)) == 2   # two real fills, the live one is the same as one of them


def test_process_positions_uses_trade_fx_when_ibkr_base_pnl_is_unusable():
    pos = [dict(ticker='AAPL', conId=1, quantity=10, avg_cost=100, current_price=120, market_value=1200,
                unrealized_pnl=200, realized_pnl=0, price_stale=False, currency='USD', multiplier=1.0,
                price_unit_verified=True, fx_native_price=120)]
    df = process_positions(pos, {}, base_currency='EUR', trade_fx={'AAPL': (96.0, '')})
    assert df.loc[0, 'fx_total'] == 96.0 and df.loc[0, 'fx_total_source'] == 'trade history'
    df = process_positions(pos, {}, base_currency='EUR', trade_fx={'AAPL': (None, 'No trade history')})
    assert df['fx_total'].isna().all() and df.loc[0, 'fx_total_reason'] == 'No trade history'
