"""Numerical and callback regressions for the October audit."""

import json
from types import SimpleNamespace

import pandas as pd
from dash.development.base_component import Component

import ai_provider
import coach
import market_valuation
from data_processor import portfolio_beta
from exposure import sector_geo_breakdown


class App:
    def __init__(self):
        self.functions = {}
        self.inputs = {}

    def callback(self, *args, **kwargs):
        def save(fn):
            self.functions[fn.__name__] = fn
            self.inputs[fn.__name__] = args
            return fn
        return save

    def clientside_callback(self, *args, **kwargs):
        pass


def text(value):
    if isinstance(value, Component):
        return text(getattr(value, 'children', None))
    if isinstance(value, (list, tuple)):
        return ' '.join(text(item) for item in value)
    return '' if value is None else str(value)


def test_etf_sector_weights_are_fractions_in_both_coach_answers():
    port = {'positions': [{'ticker': 'ETF', 'allocation_pct': 100}],
            'summary': {'largest_position': 'ETF', 'largest_position_pct': 100}}
    intel = {'sector_geo': {'ETF': {'is_etf': True, 'sector_weights': {'Technology': .5, 'Energy': .5}}}}
    assert 'Technology 50%' in text(coach.scenario_sector_geo(port, intel, None))
    assert '~50%' in text(coach.scenario_biggest_risk(port, intel, None))


def test_missing_weights_are_unavailable_in_coach_and_chart():
    from dashboard_core.summary import register

    port = {'positions': [{'ticker': 'X', 'allocation_pct': float('nan'), 'market_value': 100}],
            'summary': {'largest_position_pct': None}}
    for scenario in ('biggest_risk', 'sector_geo', 'what_if'):
        answer = text(coach.render_scenario(scenario, port, {'sector_geo': {'X': {}}}, None))
        assert 'unavailable' in answer
        assert 'nan' not in answer and 'TypeError' not in answer
    app = App()
    register(app)
    assert 'unavailable' in text(app.functions['update_donut'](port))
    assert sector_geo_breakdown(port['positions'], {}) == ([], [])


def test_short_portfolio_beta_is_explicitly_unavailable():
    frame = pd.DataFrame({'base_market_value': [100, -50], 'beta': [1, 2]})
    assert portfolio_beta(frame) == (None, 0, 0)


def test_broker_zero_pnl_wins_over_calculated_fallback(demo_df, demo_summary):
    from dashboard_core.summary import register

    app = App()
    register(app)
    data = {'positions': demo_df.to_dict('records'), 'summary': dict(demo_summary, total_daily_pnl=50),
            'account': {'base_currency': 'EUR', 'portfolio_value_units': 'base', 'daily_pnl': 0}}
    cards = app.functions['update_summary'](data, 'connected')
    card = next(c for c in cards if c.children[0].children == "Today's P&L")
    assert card.children[1].children == '€+0.00'
    data['account']['daily_pnl'] = None
    assert '+50.00' in text(app.functions['update_summary'](data, 'connected'))


def test_selection_uses_sorted_viewport(monkeypatch):
    from dashboard_core import detail

    app = App()
    detail.register(app)
    monkeypatch.setattr(detail, 'ctx', SimpleNamespace(triggered_id='holdings-datatable'))
    result = app.functions['select_ticker']({'row': 0}, None, [], [{'ticker': 'SORTED_FIRST'}], None)
    assert result[0] == 'SORTED_FIRST'
    assert any(getattr(arg, 'component_property', None) == 'derived_viewport_data'
               for arg in app.inputs['select_ticker'])


def test_each_browser_and_refresh_get_market_intelligence(monkeypatch):
    from dashboard_core import intel

    monkeypatch.setattr(intel, 'get_sector_geo', lambda _: {'X': {'sector': 'Technology'}})
    monkeypatch.setattr(intel, 'get_earnings_data', lambda _: {})
    app = App()
    intel.register(app)
    data = {'positions': [{'ticker': 'X'}]}
    for _ in range(3):
        assert app.functions['populate_market_intel'](data)['sector_geo']['X']['sector'] == 'Technology'
    dependencies = app.inputs['render_sector_geo']
    assert any(arg.component_id == 'portfolio-data' and arg.__class__.__name__ == 'Input'
               for arg in dependencies if hasattr(arg, 'component_id'))


def test_performance_refresh_depends_on_existing_interval():
    from dashboard_core.performance import register

    app = App()
    register(app)
    assert any(getattr(arg, 'component_id', None) == 'refresh-interval'
               for arg in app.inputs['render_performance'])


def test_ai_context_carries_units_cash_and_no_nan():
    port = {'positions': [{'ticker': 'X', 'currency': 'GBP', 'allocation_pct': float('nan')}],
            'summary': {'total_value': 100},
            'account': {'base_currency': 'USD', 'portfolio_value_units': 'unverified',
                        'cash_base': 42, 'net_liquidation': 142}}
    raw = ai_provider.build_portfolio_context(port, None, None)
    context = json.loads(raw)
    assert 'NaN' not in raw and '_eur' not in raw
    assert context['account']['cash_base'] == 42
    assert context['summary']['value_units'] == 'unverified'
    assert context['account']['base_currency'] == 'USD'
    assert context['positions'][0]['currency'] == 'GBP'
    port['account']['portfolio_value_units'] = 'base'
    port['summary'].update(total_unrealized_pnl=0, total_pnl_pct=0)
    assert 'USD' in text(coach.scenario_performance(port, None, None))


def test_buffett_matches_annual_dollar_observations(monkeypatch):
    class Response:
        def __init__(self, records):
            self.records = records

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps([{}, self.records]).encode()

    def fetch(request, timeout):
        market = 'CM.MKT.LCAP.CD' in request.full_url
        return Response([{'date': '2025', 'value': 60e12 if market else None},
                         {'date': '2024', 'value': 50e12 if market else 25e12}])

    monkeypatch.setattr(market_valuation.urllib.request, 'urlopen', fetch)
    monkeypatch.setattr(market_valuation, 'cached_fetch', lambda key, ttl, fn: fn())
    result = market_valuation.get_buffett_indicator()
    assert result['value'] == 200
    assert result['gdp_quarter'] == '2024'
    assert result['market_cap_t'] == 50


def test_pdf_preserves_fractional_quantity_zero_and_missing_daily_pnl(demo_df, demo_summary, monkeypatch):
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    from reportlab.platypus import Table

    from dashboard_core import export

    cells = []
    original = Table.__init__

    def record(self, data, *args, **kwargs):
        cells.extend(data)
        original(self, data, *args, **kwargs)

    monkeypatch.setattr(Table, '__init__', record)
    monkeypatch.setattr(export, 'fetch_performance', lambda *args: None)
    app = App()
    export.register(app)
    positions = demo_df.to_dict('records')
    positions[0]['quantity'] = .75
    data = {'positions': positions, 'summary': dict(demo_summary, total_daily_pnl=50),
            'account': {'base_currency': 'EUR', 'portfolio_value_units': 'base', 'daily_pnl': 0}}
    assert app.functions['export_pdf'](1, data, None)['filename'].endswith('.pdf')
    assert any('0.75' in row for row in cells)
    daily = next(row for row in cells if row[0] == "Today's P&L")
    assert daily[1] == '€+0.00'
    data['account'].update(daily_pnl=None, portfolio_value_units='unverified')
    assert app.functions['export_pdf'](1, data, None)['filename'].endswith('.pdf')


def test_portfolio_payload_forwards_broker_trades(demo_payload, monkeypatch):
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    from dashboard_core import data_callbacks as callbacks

    monkeypatch.setattr(callbacks, 'fetch_all_data', lambda: demo_payload)
    monkeypatch.setattr(callbacks, 'is_demo_mode', lambda: True)
    monkeypatch.setattr(callbacks, 'get_betas', lambda _: {})
    monkeypatch.setattr(callbacks, 'get_dividend_data_yf', lambda _: {})
    app = App()
    callbacks.register(app)
    data, status = app.functions['fetch_data']()
    assert status == 'connected'
    assert data['trades'] == demo_payload['trades']
