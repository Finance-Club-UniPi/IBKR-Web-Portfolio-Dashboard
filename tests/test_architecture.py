"""Keep financial logic independent of the dashboard and exercise its entry point."""

import ast
import asyncio
from concurrent.futures import Future
from pathlib import Path

import pytest

from backend.coaching.conversations import commit_history, new_thread
from backend.coaching.scenarios import evaluate_scenario
from backend.providers.demo import build_demo_payload

ROOT = Path(__file__).resolve().parents[1]


def test_backend_does_not_import_ui_or_dash():
    for path in (ROOT / 'backend').rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            names = ([node.module or ''] if isinstance(node, ast.ImportFrom) else
                     [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
            assert not any(name.split('.')[0] in {'ui', 'dash'} for name in names), path


def test_scenario_returns_data_with_numerical_sector_answer():
    answer = evaluate_scenario('sector_geo',
                               {'positions': [{'ticker': 'ETF', 'allocation_pct': 100}]},
                               {'sector_geo': {'ETF': {'sector_weights': {'Technology': .5, 'Energy': .5}}}},
                               None)
    assert isinstance(answer, dict)
    assert 'Technology 50%' in answer['body'][0]


def test_conversation_operations_create_and_update_same_thread():
    thread = new_thread()
    threads, active_id = commit_history([thread], thread['id'], [{'q': 'My first question', 'a': 'Answer'}])
    assert active_id == thread['id'] and len(threads) == 1
    assert threads[0]['title'] == 'My first question'
    assert threads[0]['history'][0]['a'] == 'Answer'


def test_unfinished_fx_task_never_produces_aggregate_fallback(monkeypatch):
    asyncio.set_event_loop(asyncio.new_event_loop())
    import backend.portfolio_service as service

    class Pool:
        def __init__(self, **kwargs):
            pass

        def submit(self, fn, *args, **kwargs):
            future = Future()
            if fn is not service.compute_trade_fx:
                future.set_result({})
            return future

        def shutdown(self, **kwargs):
            pass

    monkeypatch.setattr(service.concurrent.futures, 'ThreadPoolExecutor', Pool)
    monkeypatch.setattr(service.concurrent.futures, 'wait', lambda *args, **kwargs: None)
    monkeypatch.setattr(service, 'load_uploaded_trades', lambda: [])
    raw = build_demo_payload()
    raw.pop('trade_fx')
    result = service.prepare_portfolio(raw)
    foreign = [p for p in result['positions'] if p['currency'] != 'EUR']
    assert all(p['fx_total_source'] is None for p in foreign)
    assert all('not available' in p['fx_total_reason'] for p in foreign)
    assert result['trades'] == raw['trades']


def test_app_serves_layout_dependencies_and_assets():
    asyncio.set_event_loop(asyncio.new_event_loop())
    from ui.app import app

    client = app.server.test_client()
    for route in ('/', '/_dash-layout', '/_dash-dependencies', '/assets/custom.css',
                  '/assets/finance-club-logo.png'):
        assert client.get(route).status_code == 200, route
    assert len(app.callback_map) == 61


def test_live_weight_basis_uses_verified_price_and_account_currency():
    from backend.portfolio.fx import position_value_in_base

    position = {'currency': 'USD', 'quantity': 2, 'multiplier': 1,
                'fx_native_price': 100, 'price_unit_verified': True, 'market_value': 99999}
    assert position_value_in_base(position, 'EUR', {'USD': {'now': .5}}) == 100
    assert position_value_in_base(position, 'USD', {}) == 200
    assert position_value_in_base(position, 'EUR', {}) is None
    assert position_value_in_base(dict(position, fx_price_stale=True), 'USD', {}) is None
    assert position_value_in_base(dict(position, quantity=float('nan')), 'USD', {}) is None


def test_partial_etf_sector_data_keeps_unknown_remainder():
    from backend.portfolio.exposure import sector_geo_breakdown

    sectors, _ = sector_geo_breakdown([{'ticker': 'ETF', 'allocation_pct': 100}],
                                      {'ETF': {'sector_weights': {'Technology': .5}}})
    assert {name: weight for name, weight, _ in sectors} == {'Technology': 50, 'Unknown': 50}


def test_demo_main_uses_same_entry_point_without_starting_broker(monkeypatch):
    import runpy
    import sys
    import threading

    import backend.providers.ibkr as broker
    from ui.app import app

    run = {}
    monkeypatch.setattr(sys, 'argv', ['main.py', '--demo'])
    monkeypatch.setattr(threading.Thread, 'start', lambda self: None)
    monkeypatch.setattr(app, 'run', lambda **kwargs: run.update(kwargs))
    monkeypatch.setattr(broker, 'start_connection', lambda **kwargs: pytest.fail('Live broker started in demo'))
    monkeypatch.setattr(broker, '_demo_mode', False)
    runpy.run_path(str(ROOT / 'main.py'), run_name='__main__')
    assert broker.is_demo_mode() and run['debug'] is False
