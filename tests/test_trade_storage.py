"""Uploads survive failed replacement and overlapping saves."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

import backend.storage.trades as trades


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(trades, '_DATA_DIR', str(tmp_path))
    path = tmp_path / 'uploaded_trades.json'
    monkeypatch.setattr(trades, '_STORE_PATH', str(path))
    return path


def fill(ticker):
    return {'ticker': ticker, 'side': 'BUY', 'shares': 1, 'price': 100,
            'time': '2026-01-01T12:00:00'}


def test_failed_write_preserves_previous_history(store, monkeypatch):
    trades.save_uploaded_trades([fill('A')])
    previous = store.read_bytes()

    def fail(*args, **kwargs):
        raise OSError('disk full')

    monkeypatch.setattr(trades.json, 'dump', fail)
    with pytest.raises(OSError, match='disk full'):
        trades.save_uploaded_trades([fill('B')])
    assert store.read_bytes() == previous
    assert list(store.parent.iterdir()) == [store]


def test_concurrent_uploads_keep_both_histories(store):
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda ticker: trades.save_uploaded_trades([fill(ticker)]), 'ABCD'))
    assert {t['ticker'] for t in json.loads(store.read_text())['trades']} == set('ABCD')


def test_unreadable_history_aborts_upload(store):
    store.write_text('{broken')
    with pytest.raises(ValueError, match='could not be read'):
        trades.save_uploaded_trades([fill('NEW')])
    assert store.read_text() == '{broken'


def test_identical_fills_retain_multiplicity_and_repeat_upload_is_idempotent(store):
    trades.save_uploaded_trades([fill('A'), fill('A')])
    trades.save_uploaded_trades([fill('A'), fill('A')])
    assert len(trades.load_uploaded_trades()) == 2


def test_legacy_history_migrates_with_backup(store):
    before = json.dumps([fill('A')])
    store.write_text(before)
    trades.save_uploaded_trades([fill('B')])
    assert store.with_name(store.name + '.v1-backup').read_text() == before
    assert len(trades.load_uploaded_trades()) == 2


def test_execution_identity_is_idempotent_even_within_one_upload(store):
    trade = {**fill('A'), 'account': 'TEST', 'exec_id': 'ONE'}
    trades.save_uploaded_trades([trade, trade])
    assert len(trades.load_uploaded_trades()) == 1


def test_invalid_incoming_trade_retains_previous_file(store):
    trades.save_uploaded_trades([fill('A')])
    before = store.read_bytes()
    with pytest.raises(ValueError):
        trades.save_uploaded_trades([{**fill('B'), 'shares': float('nan')}])
    assert store.read_bytes() == before


def _delete_for(position_id, portfolio, n_clicks=1):
    """Fire the real delete callback the way Dash does: pattern-matched button + triggered_id."""
    from dash._callback_context import context_value
    from dash._utils import AttributeDict

    import ui.callbacks.detail as detail

    class App:
        def clientside_callback(self, *a, **k):
            pass

        def callback(self, *a, **k):
            def register(fn):
                self.fns = {**getattr(self, 'fns', {}), fn.__name__: fn}
                return fn
            return register

    app = App()
    detail.register(app)
    prop = f'{{"index":"{position_id}","type":"position-delete-trades"}}.n_clicks'
    context_value.set(AttributeDict(triggered_inputs=[{'prop_id': prop, 'value': n_clicks}],
                                    inputs_list=[]))
    return app.fns['handle_delete_trades']([n_clicks], portfolio)


def _book():
    return {'positions': [
        {'position_id': 'X', 'ticker': 'X', 'conId': 1, 'account': 'U1'},
        {'position_id': 'Y', 'ticker': 'Y', 'conId': 2, 'account': 'U1'},
        {'position_id': 'Y2', 'ticker': 'Y', 'conId': 2, 'account': 'U2'},
    ]}


def _held(conid, account, ticker):
    return {**fill(ticker), 'conId': conid, 'account': account}


def test_delete_for_one_holding_keeps_the_others(store):
    trades.save_uploaded_trades([_held(1, 'U1', 'X'), _held(2, 'U1', 'Y')])
    remaining, _ = _delete_for('X', _book())
    assert [t['ticker'] for t in remaining] == ['Y']
    assert [t['ticker'] for t in trades.load_uploaded_trades()] == ['Y']
    assert json.loads(store.read_text())['schema_version'] == 2


def test_deleting_the_last_holding_leaves_a_loadable_empty_store(store):
    trades.save_uploaded_trades([_held(1, 'U1', 'X')])
    remaining, _ = _delete_for('X', _book())
    assert remaining == [] and trades.load_uploaded_trades() == []


def test_same_contract_in_two_accounts_only_deletes_the_selected_account(store):
    trades.save_uploaded_trades([_held(2, 'U1', 'Y'), _held(2, 'U2', 'Y')])
    _delete_for('Y', _book())
    assert [t['account'] for t in trades.load_uploaded_trades()] == ['U2']


def test_unreadable_store_blocks_delete_and_is_left_untouched(store):
    store.write_text('{broken')
    remaining, status = _delete_for('X', _book())
    assert store.read_text() == '{broken'
    from dash import no_update
    assert remaining is no_update and 'could not be read' in status[0].children


def test_delete_click_without_a_real_click_does_nothing(store):
    trades.save_uploaded_trades([_held(1, 'U1', 'X')])
    _delete_for('X', _book(), n_clicks=0)
    assert len(trades.load_uploaded_trades()) == 1


def _shared_book(second_account='U2'):
    rows = [{'position_id': 'A1', 'ticker': 'A', 'conId': 7, 'account': 'U1'}]
    if second_account:
        rows.append({'position_id': 'A2', 'ticker': 'A', 'conId': 7, 'account': second_account})
    return {'positions': rows}


def test_delete_keeps_account_less_trade_another_holding_also_shows(store):
    trades.save_uploaded_trades([_held(7, 'U1', 'A'), {**fill('A'), 'conId': 7, 'time': '2026-01-02T12:00:00'}])
    remaining, status = _delete_for('A1', _shared_book())
    assert [t.get('account') for t in remaining] == [None]
    assert [t.get('account') for t in trades.load_uploaded_trades()] == [None]
    assert 'Deleted 1' in status[0].children and 'kept 1' in status[0].children


def test_delete_removes_account_less_trade_when_only_one_holding_shows_it(store):
    trades.save_uploaded_trades([{**fill('A'), 'conId': 7}])
    remaining, status = _delete_for('A1', _shared_book(second_account=None))
    assert remaining == [] and trades.load_uploaded_trades() == []
    assert 'Deleted 1' in status[0].children and 'kept' not in status[0].children


def test_clear_uploaded_trades_is_gone():
    assert not hasattr(trades, 'clear_uploaded_trades')
