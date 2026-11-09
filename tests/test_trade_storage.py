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
