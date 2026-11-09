"""Read-only IBKR Flex reports, downloaded off the dashboard request path."""

import json
import logging
import math
import os
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from datetime import time as day_time
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.portfolio.fx import valid_positive
from backend.storage.trades import atomic_json
from config import DATA_DIR

log = logging.getLogger(__name__)
_PATH = DATA_DIR / 'broker_history.json'
_LOCK = threading.RLock()
_POOL = ThreadPoolExecutor(max_workers=1)
_JOB = None
_NEXT = 0.0
_STATUS = 'Flex not configured'
_BASE_URL = 'https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/'


def read_history():
    try:
        result = json.loads(Path(_PATH).read_text())
    except FileNotFoundError:
        return {'version': 1, 'accounts': {}}
    _validate_history(result)
    return result


def _validate_history(result):
    try:
        if not isinstance(result, dict) or result.get('version') != 1 or not isinstance(result.get('accounts'), dict):
            raise ValueError('Broker history is invalid; existing data retained')
        for account, state in result['accounts'].items():
            if not isinstance(state, dict) or not isinstance(state.get('trades'), list) or not isinstance(state.get('lots'), list):
                raise ValueError('Invalid broker history account record')
            for trade in state['trades']:
                if (not isinstance(trade, dict) or not trade.get('exec_id') or trade.get('account') != account
                        or trade.get('side') not in ('BUY', 'SELL') or not trade.get('conId')
                        or not math.isfinite(float(trade.get('shares', 0)))
                        or (not trade.get('cancelled') and
                            (not valid_positive(trade.get('shares')) or not valid_positive(trade.get('price'))))):
                    raise ValueError('Invalid saved execution; existing history retained')
                if datetime.fromisoformat(str(trade.get('time', ''))).tzinfo is None:
                    raise ValueError('Saved execution timestamp has no timezone')
            for lot in state['lots']:
                if (not isinstance(lot, dict) or lot.get('account') != account or not lot.get('conId')
                        or not math.isfinite(float(lot.get('quantity', float('nan')))) or not lot.get('quantity')):
                    raise ValueError('Invalid saved open lot')
                if datetime.fromisoformat(str(lot.get('opened_at', ''))).tzinfo is None:
                    raise ValueError('Saved lot timestamp has no timezone')
    except (TypeError, KeyError, OverflowError) as e:
        raise ValueError("Invalid broker history record; previous data retained") from e


def save_history(account, trades=(), lots=None, report_end=None, coverage=None):
    """Merge by real execution identity, preserving reports on read/write failures."""
    with _LOCK:
        state = read_history()
        old = state['accounts'].setdefault(account, {'trades': [], 'lots': []})
        merged = {t['exec_id']: t for t in old['trades']}
        for trade in trades:
            if trade.get('exec_id') and trade.get('account') == account:
                voided = {t.get('correction_of') or t.get('transaction_id') for t in merged.values() if t.get('cancelled')}
                if not trade.get('cancelled') and trade.get('transaction_id') and trade['transaction_id'] in voided:
                    continue
                for identity, previous in list(merged.items()):
                    corrected = trade.get('correction_of') and previous.get('transaction_id') == trade['correction_of']
                    revision = (trade['exec_id'].count('.') >= 3 and identity.count('.') >= 3 and
                                identity.rsplit('.', 1)[0] == trade['exec_id'].rsplit('.', 1)[0] and
                                identity != trade['exec_id'])
                    if revision and identity.rsplit('.', 1)[-1].isdigit() and trade['exec_id'].rsplit('.', 1)[-1].isdigit():
                        if int(identity.rsplit('.', 1)[-1]) > int(trade['exec_id'].rsplit('.', 1)[-1]):
                            break
                    if corrected or revision:
                        merged.pop(identity)
                else:
                    merged[trade['exec_id']] = trade
        old['trades'] = sorted(merged.values(), key=lambda t: t['time'])
        if lots is not None and (not old.get('report_end') or report_end >= old['report_end']):
            old.update(lots=lots, report_end=report_end)
        if coverage:
            old['coverage'] = coverage
            old['synced_at'] = datetime.now(timezone.utc).isoformat()
        _validate_history(state)
        atomic_json(_PATH, state)
        return old


def report_datetime(raw, zone):
    value = str(raw or '').replace(';', ' ')
    for fmt in ('%Y%m%d %H%M%S', '%Y%m%d %H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y%m%d', '%Y-%m-%d'):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=ZoneInfo(zone)).astimezone(timezone.utc)
        except ValueError:
            pass
    raise ValueError('Invalid Flex date/time')


def parse_report(xml, zone):
    if len(xml) > 50_000_000 or b'<!DOCTYPE' in xml.upper():
        raise ValueError('Invalid Flex XML document')
    root = ET.fromstring(xml)
    statements = []
    for node in root.iter('FlexStatement'):
        account = node.get('accountId')
        if not account:
            raise ValueError('Flex account ID missing')
        end_day = report_datetime(node.get('toDate'), zone).astimezone(ZoneInfo(zone)).date()
        end = datetime.combine(end_day, day_time.max, ZoneInfo(zone)).astimezone(timezone.utc).isoformat()
        if node.find('Trades') is None or node.find('OpenPositions') is None:
            raise ValueError('Flex query needs execution Trades and Open Positions at Lot detail')
        trades, lots = [], []
        for row in node.iter():
            a = row.attrib
            if row.tag == 'Trade' and a.get('levelOfDetail', '').upper() == 'EXECUTION':
                identity = a.get('ibExecID') or a.get('execID') or a.get('transactionID')
                cancelled = 'Ca' in a.get('notes', '').split(';')
                qty, price = float(a['quantity']), float(a['tradePrice'])
                if not identity or not math.isfinite(qty) or (not cancelled and (not qty or not valid_positive(price))) or not a.get('conid'):
                    raise ValueError('Flex execution identity, quantity or price missing')
                side = a.get('buySell', '').upper()
                if side not in ('BUY', 'SELL'):
                    raise ValueError('Invalid Flex execution side')
                trades.append({'account': account, 'conId': int(a['conid']), 'ticker': a['symbol'],
                               'currency': a['currency'], 'exec_id': identity, 'side': side,
                               'shares': abs(qty), 'price': price,
                               'time': report_datetime(a['dateTime'], zone).isoformat(),
                               'fx_rate': float(a['fxRateToBase']) if a.get('fxRateToBase') else None,
                               'commission': float(a.get('ibCommission') or 0),
                               'commission_currency': a.get('ibCommissionCurrency'),
                               'cancelled': cancelled, 'transaction_id': a.get('transactionID'), 'source': 'flex', 'correction_of': a.get('origTradeID')})
            elif row.tag == 'OpenPosition' and a.get('levelOfDetail', '').upper() in ('LOT', 'LOTS'):
                qty = abs(float(a['position'])) * (-1 if a.get('side', '').lower() == 'short' else 1)
                if not math.isfinite(qty) or not qty:
                    raise ValueError('Invalid Flex lot quantity')
                lots.append({'account': account, 'conId': int(a['conid']), 'ticker': a['symbol'],
                             'currency': a['currency'], 'quantity': qty,
                             'opened_at': report_datetime(a['openDateTime'], zone).isoformat(),
                             'origin_id': a.get('originatingTransactionID'), 'report_end': end})
        if any(row.tag == 'OpenPosition' for row in node.iter()) and not lots:
            raise ValueError('Configure Flex Open Positions at Lot detail')
        if any(row.tag == 'Trade' for row in node.iter()) and not trades:
            raise ValueError('Configure Flex Trades at Execution detail')
        info = node.find('.//AccountInformation')
        statements.append({'account': account, 'trades': trades, 'lots': lots, 'report_end': end,
                           'from': node.get('fromDate'), 'to': node.get('toDate'),
                           'opened': info.get('dateOpened') if info is not None else None})
    if not statements:
        raise ValueError('Flex query must include Activity statements, execution Trades and Open Positions Lots')
    return statements


def _request(endpoint, params):
    url = _BASE_URL + endpoint + '?' + urllib.parse.urlencode({**params, 'v': 3})
    request = urllib.request.Request(url, headers={'User-Agent': 'Archive/1.0 Python/3'})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.read(50_000_001)
    except Exception as e:
        # Flex credentials are URL parameters; never log request URLs or raw exceptions.
        raise RuntimeError(f'Flex transport failed ({type(e).__name__})') from None


def fetch_report(start, end, token, query):
    xml = _request('SendRequest', {'t': token, 'q': query, 'fd': start.strftime('%Y%m%d'), 'td': end.strftime('%Y%m%d')})
    root = ET.fromstring(xml)
    reference = root.findtext('ReferenceCode')
    if not reference or root.findtext('Status') != 'Success':
        raise RuntimeError('Flex report generation failed; check query/token configuration')
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        time.sleep(6)
        xml = _request('GetStatement', {'t': token, 'q': reference})
        root = ET.fromstring(xml)
        if root.tag == 'FlexQueryResponse':
            return xml
        if root.findtext('ErrorCode') not in ('1018', '1019'):
            raise RuntimeError('Flex report unavailable; check token and query permissions')
    raise TimeoutError('Flex report generation exceeded 60 seconds')


def sync_history(account, token, query, zone):
    global _STATUS
    today = datetime.now(ZoneInfo(zone)).date()
    retained = date(today.year - 4, 1, 1)
    old = read_history()['accounts'].get(account, {})
    end = today - timedelta(days=1)
    latest_start = max(retained, end - timedelta(days=364))
    if old.get('report_end'):
        latest_start = max(latest_start, datetime.fromisoformat(old['report_end']).astimezone(ZoneInfo(zone)).date() - timedelta(days=2))

    def download(start, stop):
        reports = parse_report(fetch_report(start, stop, token, query), zone)
        report = next((r for r in reports if r['account'] == account), None)
        if report is None:
            raise ValueError('Flex report does not contain the selected account')
        if (report_datetime(report['from'], zone).astimezone(ZoneInfo(zone)).date() > start or
                report_datetime(report['to'], zone).astimezone(ZoneInfo(zone)).date() < stop):
            raise ValueError('Flex returned incomplete requested date coverage')
        save_history(account, report['trades'], report['lots'], report['report_end'])
        return report

    latest = download(latest_start, end)
    opened = report_datetime(latest['opened'], zone).astimezone(ZoneInfo(zone)).date() if latest.get('opened') else retained
    cursor = max(retained, opened)
    if old.get('coverage') and old['coverage']['from'] <= cursor.isoformat():
        cursor = max(cursor, date.fromisoformat(old['coverage']['to']) + timedelta(days=1))
    while cursor < latest_start:
        stop = min(cursor + timedelta(days=364), latest_start - timedelta(days=1))
        download(cursor, stop)
        cursor = stop + timedelta(days=1)
    save_history(account, coverage={'from': max(retained, opened).isoformat(), 'to': end.isoformat()})
    _STATUS = 'Flex history synchronized'


def schedule_sync(account):
    global _JOB, _NEXT, _STATUS
    token, query, zone = (os.environ.get(k) for k in ('IBKR_FLEX_TOKEN', 'IBKR_FLEX_QUERY_ID', 'IBKR_REPORT_TIMEZONE'))
    if not token or not query or not zone:
        _STATUS = 'Set IBKR_FLEX_TOKEN, IBKR_FLEX_QUERY_ID and IBKR_REPORT_TIMEZONE for automatic history'
        return _STATUS
    with _LOCK:
        if time.monotonic() >= _NEXT and (_JOB is None or _JOB.done()):
            _NEXT = time.monotonic() + 6 * 3600

            def run():
                global _STATUS, _NEXT
                try:
                    sync_history(account, token, query, zone)
                except Exception as e:
                    _STATUS = str(e) if isinstance(e, (ValueError, RuntimeError, TimeoutError)) else 'Flex sync failed'
                    _NEXT = time.monotonic() + 300
                    log.warning('Flex sync failed; previous history retained')
            _JOB = _POOL.submit(run)
    return _STATUS


def reconciled_lots(position, state, executions_from=None):
    """Use authoritative lots; refuse to guess which lots a later closing fill removed."""
    end = state.get('report_end')
    if not end or not executions_from or datetime.fromisoformat(executions_from) > datetime.fromisoformat(end) + timedelta(microseconds=1):
        return None, 'Open-lot history coverage gap; waiting for a newer Flex statement'
    lots = [dict(lot) for lot in state.get('lots', []) if lot['conId'] == position.get('conId')]
    qty = float(position['quantity'])
    by_transaction = {t.get('transaction_id'): t for t in state.get('trades', []) if t.get('transaction_id')}
    for lot in lots:
        if not lot.get('origin_id'):
            return None, 'Transferred or adjusted lot origin requires reconciliation'
        opening = by_transaction.get(lot['origin_id']) or {}
        if opening.get('fx_rate'):
            lot['fx_rate'] = opening['fx_rate']
    for trade in state.get('trades', []):
        if trade['conId'] != position.get('conId') or trade['time'] <= end:
            continue
        if trade.get('cancelled'):
            return None, 'Cancelled executions require a newer Flex lot snapshot'
        signed = trade['shares'] * (1 if trade['side'] == 'BUY' else -1)
        if signed * qty < 0:
            return None, 'Closing fills require a newer Flex lot allocation'
        lots.append({'quantity': signed, 'opened_at': trade['time'], 'fx_rate': trade.get('fx_rate'), 'origin_id': trade['exec_id']})
    if abs(sum(lot['quantity'] for lot in lots) - qty) > max(1e-6, abs(qty) * 1e-4):
        return None, 'Reported lots do not reconcile to the current position'
    if any(lot['quantity'] * qty < 0 for lot in lots):
        return None, 'Reported lot direction does not match the position'
    return lots, ''
