"""Risk page integration with deterministic history and no external requests."""

import asyncio
import io

import numpy as np
import pandas as pd
import pytest

import backend.portfolio.history as history
import ui.callbacks.risk as risk


@pytest.fixture
def histories(monkeypatch):
    import backend.portfolio.analysis as analysis
    dates = pd.bdate_range('2024-01-01', periods=100)
    bench = pd.Series(100 * np.cumprod(1 + np.random.default_rng(2).normal(.001, .01, 100)), index=dates)
    monkeypatch.setattr(analysis, 'get_position_history', lambda *a: {
        'dates': dates.strftime('%Y-%m-%d').tolist(), 'prices': bench.tolist()})
    monkeypatch.setattr(analysis, 'get_compare_history', lambda symbol, period: (
        pd.Series(1.0, index=dates) if symbol.endswith('=X') else
        pd.Series(3.0, index=dates) if symbol == '^IRX' else bench))
    monkeypatch.setattr(analysis, 'get_estr_history', lambda: pd.Series(3.0, index=dates))

    def request(data, period):
        try:
            return analysis.calculate_analysis(data, period)
        except ValueError as e:
            return {'status': 'unavailable', 'reason': str(e), 'period': period}
    monkeypatch.setattr(risk, 'request_analysis', request)
    return {'positions': [{'ticker': 'X', 'conId': 1, 'currency': 'USD', 'base_market_value': 200}],
            'account': {'base_currency': 'USD', 'net_liquidation': 200}}


@pytest.mark.parametrize('period', ['1Y', '3Y', '5Y'])
@pytest.mark.parametrize('base', ['EUR', 'USD', 'GBP'])
def test_risk_builds_shared_account_analysis(histories, period, base):
    histories['account']['base_currency'] = base
    cards, var, dd, note, summary = risk.build_risk(histories, period)
    assert len(cards) == 12 and len(var.data) == 2 and len(dd.data) == 2
    assert summary['beta'] == pytest.approx(1)
    assert summary['period'] == period and 'account equity' in note
    from backend.portfolio.positions import get_summary, process_positions
    from ui.components.summary import build_summary_cards
    p = {'ticker': 'X', 'quantity': 1, 'current_price': 100, 'avg_cost': 90,
         'market_value': 100, 'unrealized_pnl': 10, 'realized_pnl': 0, 'base_market_value': 100}
    frame = process_positions([p])
    data = {**histories, 'positions': frame.to_dict('records'), 'summary': get_summary(frame)}
    portfolio_cards = build_summary_cards(data, 'connected', summary)
    assert portfolio_cards[-1].children[1].children == cards[9].children[1].children


def test_analysis_error_is_visible_without_breaking_navigation(monkeypatch):
    asyncio.set_event_loop(asyncio.new_event_loop())
    from ui.app import app
    callback = next(v['callback'].__wrapped__ for k, v in app.callback_map.items() if 'risk-cards.children' in k)
    monkeypatch.setattr(risk, 'request_analysis', lambda *a: {
        'status': 'unavailable', 'reason': 'FX history unavailable', 'period': '1Y'})
    cards, var, _, note, summary = callback({}, '1Y', 'portfolio', 0, 0)
    assert cards == [] and 'FX history unavailable' in note
    assert summary['beta'] is None and var.layout.annotations


def test_navigation_roundtrip_keeps_demo_switcher_and_both_pages():
    asyncio.set_event_loop(asyncio.new_event_loop())
    from ui.app import app
    client = app.server.test_client()
    layout = client.get('/_dash-layout').get_data(as_text=True)
    for component in ('risk-page', 'page-nav', 'nav-risk', 'demo-portfolio-switcher', 'demo-portfolio-id'):
        assert f'"id":"{component}"' in layout
    nav_key = next(k for k in app.callback_map if 'nav-risk.style' in k)
    visibility_key = next(k for k in app.callback_map if 'main-content-area.style' in k)
    for page in ('risk', 'portfolio'):
        response = client.post('/_dash-update-component', json={
            'output': nav_key,
            'outputs': [{'id': out.component_id, 'property': out.component_property}
                        for out in app.callback_map[nav_key]['output']],
            'inputs': [{'id': 'nav-portfolio', 'property': 'n_clicks', 'value': 1},
                       {'id': 'nav-risk', 'property': 'n_clicks', 'value': 1}],
            'state': [], 'changedPropIds': [f'nav-{page}.n_clicks'],
        })
        assert response.status_code == 200
        assert response.json['response']['page-nav']['data'] == page
        visible = app.callback_map[visibility_key]['callback'].__wrapped__
        main_style, risk_style = visible('connected', page)
        assert (main_style.get('display') == 'none') == (page == 'risk')
        assert (risk_style.get('display') == 'none') == (page == 'portfolio')
        assert visible('disconnected:3', page) == ({'display': 'none'}, {'display': 'none'})
    assert any('demo-portfolio-switcher.children' in k for k in app.callback_map)
    assert any('demo-portfolio-id.data' in k for k in app.callback_map)


def test_ecb_fetch_parses_sorts_and_handles_unavailable_data(monkeypatch):
    csv = b'TIME_PERIOD,OBS_VALUE\n2024-01-02,3.9\n2024-01-01,4.0\n2024-01-03,\n'
    monkeypatch.setattr(history, 'cached_fetch', lambda key, ttl, fn: fn())
    monkeypatch.setattr(history, 'fetch_with_retry', lambda fn, **kwargs: fn())
    monkeypatch.setattr(history.urllib.request, 'urlopen', lambda *a, **kw: io.BytesIO(csv))
    rates = history.get_estr_history()
    assert rates.tolist() == [4.0, 3.9]
    assert rates.index.is_monotonic_increasing
    monkeypatch.setattr(history.urllib.request, 'urlopen', lambda *a, **kw: io.BytesIO(b'error'))
    assert history.get_estr_history() is None


def test_risk_callback_serializes_cards_and_charts_over_http(histories):
    asyncio.set_event_loop(asyncio.new_event_loop())
    from ui.app import app
    key = next(k for k in app.callback_map if 'risk-cards.children' in k)
    response = app.server.test_client().post('/_dash-update-component', json={
        'output': key,
        'outputs': [{'id': out.component_id, 'property': out.component_property}
                    for out in app.callback_map[key]['output']],
        'inputs': [{'id': 'risk-holdings', 'property': 'data', 'value': histories},
                   {'id': 'risk-period', 'property': 'value', 'value': '1Y'},
                   {'id': 'page-nav', 'property': 'data', 'value': 'risk'},
                   {'id': 'refresh-interval', 'property': 'n_intervals', 'value': 0},
                   {'id': 'fast-connect-poll', 'property': 'n_intervals', 'value': 0}],
        'state': [], 'changedPropIds': ['page-nav.data'],
    })
    assert response.status_code == 200
    result = response.json['response']
    assert len(result['risk-cards']['children']) == 12
    assert len(result['risk-var-graph']['figure']['data']) == 2
    assert len(result['risk-dd-graph']['figure']['data']) == 2


def test_demo_portfolio_selection_still_triggers_refresh(monkeypatch):
    asyncio.set_event_loop(asyncio.new_event_loop())
    from types import SimpleNamespace

    import backend.providers.ibkr as broker
    import ui.callbacks.portfolio as portfolio
    from ui.app import app

    monkeypatch.setattr(broker, '_demo_mode', True)
    monkeypatch.setattr(broker, '_demo_portfolio_id', 'balanced')
    monkeypatch.setattr(portfolio, 'ctx', SimpleNamespace(triggered_id={
        'type': 'demo-portfolio-btn', 'index': 'growth',
    }))
    key = next(k for k in app.callback_map if 'demo-portfolio-id.data' in k)
    callback = app.callback_map[key]['callback'].__wrapped__
    new_id, refresh = callback([0, 1, 0], 'balanced', 5)
    assert new_id == 'growth' and refresh == 6
    assert broker.get_demo_portfolio() == 'growth'


def test_summary_callback_reads_the_shared_analysis_over_http(histories):
    from backend.portfolio.positions import get_summary, process_positions
    from ui.app import app
    p = dict(ticker='X', quantity=1, current_price=100, avg_cost=90, market_value=100,
             unrealized_pnl=10, realized_pnl=0, base_market_value=100)
    df = process_positions([p])
    data = {**histories, 'positions': df.to_dict('records'), 'summary': get_summary(df)}
    import json

    from plotly.utils import PlotlyJSONEncoder
    data = json.loads(json.dumps(data, cls=PlotlyJSONEncoder))
    response = app.server.test_client().post('/_dash-update-component', json={
        'output': 'summary-cards.children', 'outputs': {'id': 'summary-cards', 'property': 'children'},
        'inputs': [{'id': 'portfolio-data', 'property': 'data', 'value': data},
                   {'id': 'portfolio-analysis', 'property': 'data', 'value': {'beta': -1.25, 'period': '3Y'}}],
        'state': [{'id': 'connection-status', 'property': 'data', 'value': 'connected'}],
        'changedPropIds': ['portfolio-analysis.data'],
    })
    assert response.status_code == 200
    cards = response.json['response']['summary-cards']['children']
    assert cards[-1]['props']['children'][1]['props']['children'] == '-1.25'


def test_pdf_receives_shared_beta_and_rejects_a_different_book(histories, monkeypatch):
    import ui.callbacks.export as export
    from backend.portfolio.analysis import fingerprint
    received = []
    monkeypatch.setattr(export, 'build_pdf', lambda data, *args, **kwargs: (received.append(data) or b'PDF', 'test.pdf'))

    class App:
        def callback(self, *args, **kwargs):
            def register(fn):
                self.export = fn
                return fn
            return register

    app = App()
    export.register(app)
    shared = {'beta': -.5, 'period': '3Y', 'input_key': fingerprint(histories, '3Y')}
    app.export(1, histories, {}, shared)
    assert received[-1]['analysis']['beta'] == -.5
    histories['positions'][0]['quantity'] = 5
    app.export(2, histories, {}, shared)
    assert 'beta' not in received[-1]['analysis']
