"""Flex protocol, lot reconciliation, execution identity and persistence safety."""

from datetime import datetime, timedelta, timezone

import pytest

from backend.providers import flex


@pytest.fixture
def history_file(tmp_path, monkeypatch):
    path = tmp_path / 'history.json'
    monkeypatch.setattr(flex, '_PATH', path)
    return path


def xml(side='Long', qty='2'):
    return f'''<FlexQueryResponse><FlexStatements><FlexStatement accountId="TEST" fromDate="20260101" toDate="20260102">
    <AccountInformation dateOpened="20250101"/>
    <Trades><Trade levelOfDetail="EXECUTION" ibExecID="a.b.1.01" transactionID="123" conid="42"
    symbol="X" currency="USD" buySell="BUY" quantity="2" tradePrice="100" dateTime="20260101;120000" fxRateToBase="0.8"/></Trades>
    <OpenPositions><OpenPosition levelOfDetail="LOT" conid="42" symbol="X" currency="USD"
    side="{side}" position="{qty}" openDateTime="20260101;120000" originatingTransactionID="123"/></OpenPositions>
    </FlexStatement></FlexStatements></FlexQueryResponse>'''.encode()


def test_parse_lots_including_shorts_and_timezone():
    record = flex.parse_report(xml('Short'), 'Europe/Athens')[0]
    assert record['lots'][0]['quantity'] == -2
    assert record['trades'][0]['time'] == '2026-01-01T10:00:00+00:00'
    assert record['trades'][0]['fx_rate'] == .8
    with pytest.raises(ValueError):
        flex.parse_report(xml(qty='nan'), 'UTC')
    with pytest.raises(ValueError):
        flex.parse_report(b'<!DOCTYPE x><FlexQueryResponse/>', 'UTC')


def test_execution_ids_preserve_distinct_fills_and_corrections(history_file):
    trade = flex.parse_report(xml(), 'UTC')[0]['trades'][0]
    first = {**trade, 'exec_id': 'a.b.1.01'}
    second = {**trade, 'exec_id': 'a.b.2.01'}
    flex.save_history('TEST', [first, second, first])
    assert len(flex.read_history()['accounts']['TEST']['trades']) == 2
    revised = {**first, 'exec_id': 'a.b.1.02', 'price': 101}
    flex.save_history('TEST', [revised, first])
    saved = flex.read_history()['accounts']['TEST']['trades']
    assert {t['exec_id'] for t in saved} == {'a.b.1.02', 'a.b.2.01'}
    assert next(t for t in saved if t['exec_id'] == 'a.b.1.02')['price'] == 101


def test_bad_existing_history_cannot_be_overwritten(history_file):
    history_file.write_text('{bad')
    with pytest.raises(ValueError):
        flex.save_history('TEST', [])
    assert history_file.read_text() == '{bad'


def test_atomic_failure_retains_original(history_file, monkeypatch):
    flex.save_history('TEST')
    previous = history_file.read_bytes()
    import backend.storage.trades as storage
    monkeypatch.setattr(storage.os, 'replace', lambda *args: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        flex.save_history('TEST', lots=[], report_end='2026-01-01')
    assert history_file.read_bytes() == previous


def test_reconcile_authoritative_short_lots_and_reject_closing_gap():
    record = flex.parse_report(xml('Short'), 'UTC')[0]
    state = {'trades': record['trades'], 'lots': record['lots'], 'report_end': record['report_end']}
    p = {'conId': 42, 'quantity': -2}
    start = '2026-01-03T00:00:00+00:00'
    lots, reason = flex.reconciled_lots(p, state, start)
    assert not reason and lots[0]['quantity'] == -2 and lots[0]['fx_rate'] == .8
    state['trades'].append({**record['trades'][0], 'exec_id': 'CLOSE', 'side': 'BUY', 'shares': 1,
                            'time': '2026-01-03T12:00:00+00:00'})
    assert flex.reconciled_lots(p, state, start)[0] is None
    state['trades'] = []
    assert flex.reconciled_lots(p, state, '2026-01-05T00:00:00+00:00')[0] is None


def test_flex_protocol_uses_fixed_endpoints_and_retries_generation(monkeypatch):
    calls = []
    replies = iter([b'<FlexStatementResponse><Status>Success</Status><ReferenceCode>R</ReferenceCode></FlexStatementResponse>',
                    b'<FlexStatementResponse><ErrorCode>1019</ErrorCode></FlexStatementResponse>', xml()])

    def request(endpoint, params):
        calls.append((endpoint, params))
        return next(replies)

    monkeypatch.setattr(flex, '_request', request)
    monkeypatch.setattr(flex.time, 'sleep', lambda _: None)
    day = datetime(2026, 1, 1).date()
    assert flex.fetch_report(day, day + timedelta(days=1), 'SECRET', 'Q') == xml()
    assert calls[0][0] == 'SendRequest'
    assert calls[1][1]['q'] == 'R'


def test_generation_failure_reports_ibkr_error_without_the_token(monkeypatch):
    reply = (b'<FlexStatementResponse><Status>Fail</Status><ErrorCode>1015</ErrorCode>'
             b'<ErrorMessage>Token SECRET is invalid.</ErrorMessage></FlexStatementResponse>')
    monkeypatch.setattr(flex, '_request', lambda endpoint, params: reply)
    day = datetime(2026, 1, 1).date()
    with pytest.raises(RuntimeError, match='IBKR error 1015: Token \\*\\*\\* is invalid') as caught:
        flex.fetch_report(day, day, 'SECRET', 'Q')
    assert 'SECRET' not in str(caught.value)


def test_sync_steps_back_when_yesterdays_statement_is_not_built_yet(history_file, monkeypatch):
    now = datetime.now(timezone.utc).date()
    calls = []

    def fetch(start, end, token, query):
        calls.append(end)
        if end > now - timedelta(days=3):
            raise flex.StatementNotAvailable('IBKR error 1003')
        text = xml().decode().replace('fromDate="20260101"', f'fromDate="{start:%Y%m%d}"')
        return text.replace('toDate="20260102"', f'toDate="{end:%Y%m%d}"').encode()

    monkeypatch.setattr(flex, 'fetch_report', fetch)
    monkeypatch.setattr(flex.time, 'sleep', lambda _: None)
    flex.sync_history('TEST', 'SECRET', 'Q', 'UTC')
    assert calls[:3] == [now - timedelta(days=1), now - timedelta(days=2), now - timedelta(days=3)]
    stored = flex.read_history()['accounts']['TEST']
    assert stored['coverage']['to'] == (now - timedelta(days=3)).isoformat()   # not the day IBKR refused


def test_sync_gives_up_after_a_few_unavailable_days(history_file, monkeypatch):
    calls = []

    def fetch(*args):
        calls.append(args)
        raise flex.StatementNotAvailable('IBKR error 1003')

    monkeypatch.setattr(flex, 'fetch_report', fetch)
    monkeypatch.setattr(flex.time, 'sleep', lambda _: None)
    with pytest.raises(flex.StatementNotAvailable):
        flex.sync_history('TEST', 'SECRET', 'Q', 'UTC')
    assert len(calls) == 4


def test_only_error_1003_is_treated_as_a_not_yet_built_statement(monkeypatch):
    day = datetime(2026, 1, 1).date()
    for code, kind in (('1003', flex.StatementNotAvailable), ('1015', RuntimeError)):
        reply = f'<FlexStatementResponse><Status>Fail</Status><ErrorCode>{code}</ErrorCode></FlexStatementResponse>'.encode()
        monkeypatch.setattr(flex, '_request', lambda endpoint, params, reply=reply: reply)
        with pytest.raises(RuntimeError) as caught:
            flex.fetch_report(day, day, 'SECRET', 'Q')
        assert type(caught.value) is kind


def test_sync_downloads_latest_lots_first_then_backfills(history_file, monkeypatch):
    now = datetime.now(timezone.utc).date()
    calls = []

    def fetch(start, end, token, query):
        calls.append((start, end))
        assert (end - start).days < 365
        text = xml().decode().replace('fromDate="20260101"', f'fromDate="{start:%Y%m%d}"')
        text = text.replace('toDate="20260102"', f'toDate="{end:%Y%m%d}"')
        return text.encode()

    monkeypatch.setattr(flex, 'fetch_report', fetch)
    flex.sync_history('TEST', 'SECRET', 'Q', 'UTC')
    stored = flex.read_history()['accounts']['TEST']
    assert calls[0][1] == now - timedelta(days=1)
    assert datetime.fromisoformat(stored['report_end']).date() == now - timedelta(days=1)
    assert stored['coverage']['from'] == '2025-01-01'
    assert len(stored['trades']) == 1


def test_full_history_fx_uses_reported_short_lots(history_file, monkeypatch):
    import backend.providers.fx_history as provider
    record = flex.parse_report(xml('Short'), 'UTC')[0]
    state = {'trades': record['trades'], 'lots': record['lots'], 'report_end': record['report_end']}
    monkeypatch.setattr(provider, 'fx_history', lambda *args: {datetime(2026, 1, 1).date(): .8})
    p = {'account': 'TEST', 'conId': 42, 'ticker': 'X', 'currency': 'USD', 'quantity': -2,
         'valuation_rate': .9, 'valuation_price': 100, 'valuation_as_of': '2026-01-03'}
    result = provider.compute_trade_fx([p], 'EUR', lot_state=state, executions_from='2026-01-03T00:00:00+00:00')
    assert result['TEST:42'][0] == -20


def test_cancelled_execution_is_not_restored_by_an_older_backfill(history_file):
    original = flex.parse_report(xml(), 'UTC')[0]['trades'][0]
    flex.save_history('TEST', [original])
    cancelled = {**original, 'exec_id': 'a.b.1.02', 'cancelled': True,
                 'correction_of': original['transaction_id'], 'transaction_id': 'VOID'}
    flex.save_history('TEST', [cancelled, original])
    saved = flex.read_history()['accounts']['TEST']['trades']
    assert len(saved) == 1 and saved[0]['cancelled']


def test_empty_summary_query_is_not_accepted_as_lot_history():
    with pytest.raises(ValueError, match='Lot detail'):
        flex.parse_report(xml().replace(b'levelOfDetail="LOT"', b'levelOfDetail="SUMMARY"'), 'UTC')


def test_invalid_incoming_execution_does_not_replace_good_history(history_file):
    trade = flex.parse_report(xml(), 'UTC')[0]['trades'][0]
    flex.save_history('TEST', [trade])
    before = history_file.read_bytes()
    with pytest.raises(ValueError):
        flex.save_history('TEST', [{**trade, 'price': -10}])
    assert history_file.read_bytes() == before


def test_sync_fills_a_gap_after_more_than_a_year_offline(history_file, monkeypatch):
    today = datetime.now(timezone.utc).date()
    old_end = today - timedelta(days=800)
    retained = today.replace(year=today.year - 4, month=1, day=1)
    flex.save_history('TEST', lots=[], report_end=old_end.isoformat() + 'T23:59:59.999999+00:00',
                      coverage={'from': retained.isoformat(), 'to': old_end.isoformat()})
    calls = []

    def fetch(start, end, *args):
        calls.append((start, end))
        report = xml().decode().replace('fromDate="20260101"', f'fromDate="{start:%Y%m%d}"')
        report = report.replace('dateOpened="20250101"', f'dateOpened="{retained:%Y%m%d}"')
        return report.replace('toDate="20260102"', f'toDate="{end:%Y%m%d}"').encode()

    monkeypatch.setattr(flex, 'fetch_report', fetch)
    flex.sync_history('TEST', 'SECRET', 'Q', 'UTC')
    assert calls[1][0] == old_end + timedelta(days=1)
    assert calls[-1][1] == calls[0][0] - timedelta(days=1)
