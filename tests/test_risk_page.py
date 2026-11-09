"""Risk page integration with deterministic history and no external requests."""

import asyncio
import io

import numpy as np
import pandas as pd
import pytest
from dash import no_update

import backend.portfolio.history as history
import ui.callbacks.risk as risk


@pytest.fixture
def histories(monkeypatch):
    dates = pd.bdate_range('2024-01-01', periods=100)
    bench = pd.Series(100 * np.cumprod(1 + np.random.default_rng(2).normal(.001, .01, 100)), index=dates)
    values = bench * 2
    calls = []

    def portfolio(positions, base, period):
        calls.append(('portfolio', base, period))
        return values, []

    def compare(symbol, period):
        calls.append((symbol, period))
        if symbol == '^IRX':
            return pd.Series(4.0, index=dates)
        if symbol.endswith('=X'):
            return pd.Series(.9, index=dates)
        return bench

    monkeypatch.setattr(risk, 'get_portfolio_history', portfolio)
    monkeypatch.setattr(risk, 'get_compare_history', compare)
    monkeypatch.setattr(risk, 'get_estr_history', lambda: pd.Series(3.0, index=dates))
    return values, calls


@pytest.mark.parametrize('period', ['1Y', '3Y', '5Y'])
@pytest.mark.parametrize('base', ['EUR', 'USD', 'GBP'])
def test_risk_builds_all_cards_and_charts_with_base_currency(histories, period, base):
    values, calls = histories
    values.attrs['chained'] = [('NEW', values.index[30])]
    cards, var, dd, note = risk.build_risk({'base': base, 'positions': []}, period)
    assert len(cards) == 12 and len(var.data) == 2 and len(dd.data) == 2
    assert ('portfolio', base, period) in calls
    assert f'in {base} on the latest value' in note
    assert 'dividend-adjusted' in note and 'chained' in note
    if base == 'EUR':
        assert '3.00%' in note and '€STR' in note
    elif base == 'USD':
        assert '4.00%' in note and ('^IRX', period) in calls
    else:
        assert 'Risk-free data unavailable, 0% used' in note
        assert ('^IRX', period) not in calls
    assert ((f'USD{base}=X', period) in calls) == (base != 'USD')


@pytest.mark.parametrize('missing', ['bench', 'fx', 'rf', 'portfolio', 'short'])
def test_missing_history_degrades_without_breaking_page(histories, monkeypatch, missing):
    values, _ = histories
    original = risk.get_compare_history
    monkeypatch.setattr(risk, 'get_compare_history', lambda s, p: (
        None if (missing == 'bench' and s == '^GSPC') or (missing == 'fx' and s.endswith('=X'))
        else original(s, p)))
    if missing == 'rf':
        monkeypatch.setattr(risk, 'get_estr_history', lambda: None)
    if missing == 'portfolio':
        monkeypatch.setattr(risk, 'get_portfolio_history', lambda *a: (None, []))
    if missing == 'short':
        monkeypatch.setattr(risk, 'get_portfolio_history', lambda *a: (values.iloc[:10], []))
    cards, var, _, note = risk.build_risk({'base': 'EUR', 'positions': []}, '1Y')
    if missing in ('short', 'portfolio'):
        assert not cards
        assert var.layout.annotations
    else:
        assert len(cards) == 12
        if missing == 'rf':
            assert '0% used' in note
        else:
            assert cards[9].children[1].children == '—'


def test_hidden_risk_page_skips_history_and_errors_stay_on_risk_page(monkeypatch):
    asyncio.set_event_loop(asyncio.new_event_loop())
    from ui.app import app
    callback = next(v['callback'].__wrapped__ for k, v in app.callback_map.items() if 'risk-cards.children' in k)

    def fail(*args):
        pytest.fail('Hidden risk page fetched history')

    monkeypatch.setattr(risk, 'build_risk', fail)
    assert callback(None, '1Y', 'portfolio', 0) == (no_update,) * 4

    def unavailable(*args):
        raise ValueError('History service down')

    monkeypatch.setattr(risk, 'build_risk', unavailable)
    cards, var, _, note = callback({}, '1Y', 'risk', 0)
    assert cards == [] and 'History service down' in note
    assert var.layout.annotations[0].text == 'Risk metrics unavailable.'


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
        'inputs': [{'id': 'risk-holdings', 'property': 'data', 'value': {'base': 'EUR', 'positions': []}},
                   {'id': 'risk-period', 'property': 'value', 'value': '1Y'},
                   {'id': 'page-nav', 'property': 'data', 'value': 'risk'},
                   {'id': 'refresh-interval', 'property': 'n_intervals', 'value': 0}],
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
