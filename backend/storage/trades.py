"""
Historical trade ingestion.

IBKR's live `reqExecutions` retention is limited (often just today), so this
module lets the user upload longer-range trade history as CSV exported from
the IBKR Client Portal → Performance & Reports → Transaction History.

Parsed trades are normalized into the same dict shape as live trades and
persisted to `data/uploaded_trades.json` so they survive restarts.
"""

from __future__ import annotations

import csv
import json
import logging
import math
import os
import shutil
import tempfile
import threading
from collections import Counter
from datetime import datetime
from io import StringIO
from pathlib import Path

from config import DATA_DIR

log = logging.getLogger(__name__)

_DATA_DIR = str(DATA_DIR)
_STORE_PATH = os.path.join(_DATA_DIR, 'uploaded_trades.json')
_WRITE_LOCK = threading.RLock()


def _parse_dt(raw):
    """Parse the various datetime formats IBKR emits → ISO 8601, or None."""
    if not raw:
        return None
    s = str(raw).strip().strip('"')
    for fmt in (
        '%Y%m%d;%H%M%S',       # Flex "20240115;093012"
        '%Y-%m-%d;%H:%M:%S',
        '%Y-%m-%d, %H:%M:%S',  # CSV "2024-01-15, 09:30:00"
        '%Y-%m-%d %H:%M:%S',
        '%Y%m%d',
        '%Y-%m-%d',
    ):
        try:
            return datetime.strptime(s, fmt).isoformat()
        except ValueError:
            continue
    return None


def _safe_float(v, default=0.0):
    try:
        return float(str(v).replace(',', ''))
    except (TypeError, ValueError):
        return default


def parse_activity_csv(content: bytes) -> list | None:
    """
    Parse IBKR Activity Statement / Trade Confirmation CSV into normalized
    trade dicts.  The CSV is multi-section (each section has its own header
    row starting with 'Header'); we only extract rows where the first column
    is 'Trades' and the DataDiscriminator is 'Order' or 'Trade'.

    Returns None when the file doesn't look like an IBKR Activity Statement
    (no Trades section found), [] when it's valid but has no qualifying trades,
    or a non-empty list of trade dicts.
    """
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            text = content.decode('latin-1')  # IBKR occasionally exports latin-1
        except Exception as e:
            log.warning('CSV decode error: %s', e)
            return None
    except Exception as e:
        log.warning('CSV decode error: %s', e)
        return None

    trades = []
    reader = csv.reader(StringIO(text))
    header: list | None = None
    found_trades_section = False

    for row in reader:
        if not row or row[0] != 'Trades':
            continue
        if len(row) >= 2 and row[1] == 'Header':
            header = row
            found_trades_section = True
            continue
        if len(row) < 2 or row[1] != 'Data' or not header:
            continue

        d = dict(zip(header, row, strict=False))
        disc = (d.get('DataDiscriminator') or '').strip()
        if disc not in ('Order', 'Trade'):
            continue   # skip SubTotal / Total / empty-discriminator rows

        symbol = (d.get('Symbol') or '').strip()
        qty    = _safe_float(d.get('Quantity'))
        price  = _safe_float(d.get('T. Price') or d.get('Trade Price'))
        dt     = _parse_dt(d.get('Date/Time'))
        if not (symbol and math.isfinite(qty) and qty and math.isfinite(price) and price > 0 and dt):
            log.debug('Skipping row — missing field(s): symbol=%r qty=%r price=%r dt=%r', symbol, qty, price, dt)
            continue
        side = 'BUY' if qty > 0 else 'SELL'
        trades.append({
            'ticker': symbol,
            'side':   side,
            'shares': abs(qty),
            'price':  round(price, 4),
            'time':   dt,
            'value':  round(abs(qty) * price, 2),
            'source': 'csv',
            'conId': int(d['Conid']) if d.get('Conid', '').isdigit() else None,
            'currency': d.get('Currency'), 'account': d.get('Account ID'),
            'exec_id': d.get('IB Exec ID') or d.get('Trade ID'),
        })
    if not found_trades_section:
        return None
    return trades


def _trade_key(t):
    if t.get('exec_id'):
        return (t.get('account'), t['exec_id'])
    return (
        t.get('account'), t.get('conId'), t.get('currency'), t.get('ticker'),
        t.get('side'),
        round(_safe_float(t.get('shares')), 4),
        round(_safe_float(t.get('price')), 4),
        t.get('time'),
    )


def _validate_trades(trades):
    try:
        if not isinstance(trades, list):
            raise ValueError('Invalid saved trade history')
        for trade in trades:
            if (not isinstance(trade, dict) or not trade.get('ticker') or trade.get('side') not in ('BUY', 'SELL')
                    or not all(math.isfinite(float(trade.get(k, 0))) and float(trade.get(k, 0)) > 0
                               for k in ('shares', 'price'))):
                raise ValueError('Invalid trade quantity, price or identity')
            datetime.fromisoformat(str(trade.get('time', '')))
    except (TypeError, KeyError, OverflowError) as e:
        raise ValueError('Invalid trade history record') from e


def load_uploaded_trades() -> list:
    if not os.path.exists(_STORE_PATH):
        return []
    try:
        with open(_STORE_PATH, encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, dict) and data.get('schema_version') == 2:
                data = data.get('trades')
            _validate_trades(data)
            return data
    except Exception as e:
        log.warning('Failed to load uploaded trades: %s', e)
        raise ValueError('Saved trade history could not be read; upload aborted') from e


def _backup_v1_store():
    """Keep the first copy of a legacy bare-list store before it is rewritten as v2."""
    if os.path.exists(_STORE_PATH) and isinstance(json.loads(Path(_STORE_PATH).read_text()), list) \
            and not os.path.exists(_STORE_PATH + '.v1-backup'):
        shutil.copy2(_STORE_PATH, _STORE_PATH + '.v1-backup')


def save_uploaded_trades(new_trades: list) -> list:
    """Merge `new_trades` into the persisted list, dedupe, and return the full list."""
    with _WRITE_LOCK:
        os.makedirs(_DATA_DIR, exist_ok=True)
        existing = load_uploaded_trades()
        _validate_trades(new_trades)
        counts = Counter(_trade_key(t) for t in existing)
        incoming = Counter()
        for trade in new_trades:
            key = _trade_key(trade)
            incoming[key] += 1
            if incoming[key] > counts[key] and (not trade.get('exec_id') or incoming[key] == 1):
                existing.append(trade)
        existing.sort(key=lambda x: x.get('time') or '')
        _backup_v1_store()
        atomic_json(_STORE_PATH, {'schema_version': 2, 'trades': existing})
        return existing


def trade_in_position(t, con_id, account, ticker, ticker_unique) -> bool:
    """The rule for which trades belong to a position; the detail view and per-holding delete share it."""
    if t.get('conId'):
        return t['conId'] == con_id and (not t.get('account') or t['account'] == account)
    return t.get('ticker') == ticker and ticker_unique


def delete_uploaded_trades(match) -> list:
    """Drop stored trades for which `match(trade)` is true; return what remains.

    Reads and rewrites under the same lock as saves; an unreadable store raises and is left untouched.
    """
    with _WRITE_LOCK:
        if not os.path.exists(_STORE_PATH):
            return []
        kept = [t for t in load_uploaded_trades() if not match(t)]
        _backup_v1_store()
        atomic_json(_STORE_PATH, {'schema_version': 2, 'trades': kept})
        return kept


def clear_uploaded_trades():
    """Delete the whole store (every holding) without parsing it, so a corrupt file can be cleared too.

    No backup is kept: a corrupt store has nothing worth saving, and the UI confirms first.
    """
    with _WRITE_LOCK:
        Path(_STORE_PATH).unlink(missing_ok=True)


def atomic_json(path, data):
    """Write a validated snapshot without replacing the old file on failure."""
    directory = os.path.dirname(str(path))
    os.makedirs(directory, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory, delete=False) as output:
            temporary = output.name
            json.dump(data, output, indent=2, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
