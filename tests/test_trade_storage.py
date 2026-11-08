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
    assert {t['ticker'] for t in json.loads(store.read_text())} == set('ABCD')
