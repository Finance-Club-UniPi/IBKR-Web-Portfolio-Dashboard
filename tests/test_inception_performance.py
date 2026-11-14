"""Time-weighted account performance from Flex NAV and deposits."""

import pandas as pd
import pytest

import backend.portfolio.history as ph
import backend.portfolio.performance as perf
from backend.providers import flex
from backend.providers.flex import parse_report


def report(nav_rows='', cash_rows='', cash_section=True, nav_section=True):
    nav = f'<EquitySummaryInBase>{nav_rows}</EquitySummaryInBase>' if nav_section else ''
    cash = f'<CashTransactions>{cash_rows}</CashTransactions>' if cash_section else ''
    return f'''<FlexQueryResponse><FlexStatements><FlexStatement accountId="TEST" fromDate="20261102" toDate="20261112">
    <AccountInformation dateOpened="20261102" currency="EUR"/>
    <Trades><Trade levelOfDetail="EXECUTION" ibExecID="a.b.1.01" transactionID="1" conid="42" symbol="X"
    currency="USD" buySell="BUY" quantity="2" tradePrice="100" dateTime="20261102;120000" fxRateToBase="0.8"/></Trades>
    <OpenPositions><OpenPosition levelOfDetail="LOT" conid="42" symbol="X" currency="USD" side="Long"
    position="2" openDateTime="20261102;120000" originatingTransactionID="1"/></OpenPositions>
    {nav}{cash}</FlexStatement></FlexStatements></FlexQueryResponse>'''.encode()


def nav_row(day, total):
    return f'<EquitySummaryByReportDateInBase reportDate="{day}" total="{total}" cash="0"/>'


def deposit(day, amount, tid, currency='EUR', rate='1', kind='Deposits/Withdrawals', detail='DETAIL'):
    return (f'<CashTransaction type="{kind}" levelOfDetail="{detail}" reportDate="{day}" amount="{amount}" '
            f'currency="{currency}" fxRateToBase="{rate}" transactionID="{tid}"/>')


@pytest.fixture(autouse=True)
def no_configured_start(monkeypatch):
    monkeypatch.setitem(perf.cfg, 'performance', {'start_date': None})


# ── pure calculation ────────────────────────────────────────────────────────────

def test_twr_ignores_a_deposit_and_matches_the_worked_example():
    # 100 → 102 (+2%), deposit 100 → 202 → 200 (-1%): TWR = 1.02 * 0.99 - 1
    nav = {'2026-11-02': 100.0, '2026-11-03': 102.0, '2026-11-04': 200.0}
    flows = [{'day': '2026-11-04', 'amount': 100.0, 'id': 'd'}]
    levels = perf.twr_levels(nav, flows)
    assert levels.iloc[-1] == pytest.approx(1.02 * (200 / 202))
    assert levels.attrs['start_value'] == 100.0


def test_cash_drag_is_part_of_the_return():
    # Funded with 1000 on day one, 35% stays in cash. Invested part +2% => account +1.3%.
    nav = {'2026-11-02': 1000.0, '2026-11-03': 1000.0 + 650 * 0.02}
    flows = [{'day': '2026-11-02', 'amount': 1000.0, 'id': 'init'}]
    levels = perf.twr_levels(nav, flows)
    assert levels.iloc[-1] - 1 == pytest.approx(0.013)
    # Anchor day sits before the funding day so day one's move is not lost.
    assert levels.index[0] == pd.Timestamp('2026-11-01') and levels.iloc[0] == 1.0


def test_funding_day_move_counts_but_prior_history_does_not_invent_it():
    # No funding flow at the start of the data: the index starts at the first NAV day.
    levels = perf.twr_levels({'2026-11-02': 500.0, '2026-11-03': 510.0}, [])
    assert levels.index[0] == pd.Timestamp('2026-11-02')
    assert levels.iloc[-1] == pytest.approx(1.02)
    assert levels.attrs['start_value'] == 500.0


def test_weekend_flow_joins_the_next_nav_day_and_withdrawals_work():
    nav = {'2026-11-06': 100.0, '2026-11-09': 150.0, '2026-11-10': 75.0}
    flows = [{'day': '2026-11-07', 'amount': 50.0, 'id': 'sat'},      # joins Monday 9th
             {'day': '2026-11-10', 'amount': -75.0, 'id': 'out'}]
    levels = perf.twr_levels(nav, flows)
    assert levels.loc['2026-11-09'] == pytest.approx(1.0)             # 150 / (100 + 50)
    assert levels.loc['2026-11-10'] == pytest.approx(1.0)             # 75 / (150 - 75)


def test_zero_nav_days_before_funding_are_skipped_and_dead_capital_is_refused():
    levels = perf.twr_levels({'2026-11-01': 0.0, '2026-11-02': 100.0, '2026-11-03': 101.0},
                             [{'day': '2026-11-02', 'amount': 100.0, 'id': 'a'}])
    assert levels.iloc[-1] == pytest.approx(1.01)
    with pytest.raises(ValueError, match='zero throughout'):
        perf.twr_levels({'2026-11-02': 0.0, '2026-11-03': 0.0}, [])
    with pytest.raises(ValueError, match='no capital'):
        perf.twr_levels({'2026-11-02': 100.0, '2026-11-03': 100.0, '2026-11-04': 50.0},
                        [{'day': '2026-11-03', 'amount': -100.0, 'id': 'out'}])


def test_start_date_counts_from_the_close_before_it_and_ignores_older_flows():
    nav = {'2026-10-30': 100.0, '2026-11-02': 99.0, '2026-11-03': 101.0, '2026-11-04': 151.0}
    flows = [{'day': '2026-10-29', 'amount': 100.0, 'id': 'old'},       # before the base: already in NAV
             {'day': '2026-11-04', 'amount': 50.0, 'id': 'new'}]
    levels = perf.twr_levels(nav, flows, start='2026-11-02')
    assert levels.index[0] == pd.Timestamp('2026-10-30') and levels.iloc[0] == 1.0
    assert levels.loc['2026-11-02'] == pytest.approx(0.99)               # 2 Nov's own move counts
    assert levels.iloc[-1] == pytest.approx(0.99 * (101 / 99) * (151 / 151))
    assert levels.attrs['start_value'] == 100.0 and levels.attrs['counted_from'] == '2026-11-02'


def test_start_date_without_an_earlier_nav_uses_the_whole_history():
    levels = perf.twr_levels({'2026-11-02': 100.0, '2026-11-03': 102.0}, [], start='2026-11-01')
    assert levels.iloc[-1] == pytest.approx(1.02) and levels.attrs['counted_from'] is None


def test_configured_start_date_is_used_and_validated(tmp_path, monkeypatch):
    monkeypatch.setattr(flex, '_PATH', tmp_path / 'h.json')
    flex.save_history('TEST', nav={'2026-10-30': 100.0, '2026-11-02': 101.0, '2026-11-03': 102.01},
                      flows=[{'day': '2026-08-19', 'amount': 100.0, 'id': 'init'}])
    assert perf.inception_series('TEST').index[0] == pd.Timestamp('2026-10-30')   # whole history
    monkeypatch.setitem(perf.cfg, 'performance', {'start_date': '2026-11-03'})
    levels = perf.inception_series('TEST')
    assert levels.index[0] == pd.Timestamp('2026-11-02') and levels.iloc[-1] == pytest.approx(1.01)
    monkeypatch.setitem(perf.cfg, 'performance', {'start_date': 'soon'})
    with pytest.raises(ValueError, match='performance.start_date'):
        perf.inception_series('TEST')


def test_book_trades_are_not_executions_and_do_not_block_the_sync():
    book = ('<Trade levelOfDetail="EXECUTION" transactionType="BookTrade" ibExecID="" transactionID="9" conid="7" '
            'symbol="MP" currency="USD" buySell="SELL" quantity="-100" tradePrice="0" dateTime="20261029;000000"/>')
    xml = report().replace(b'</Trades>', book.encode() + b'</Trades>')
    rec = parse_report(xml, 'UTC')[0]
    assert [t['ticker'] for t in rec['trades']] == ['X']
    only_book = report().replace(b'<Trade levelOfDetail="EXECUTION" ibExecID="a.b.1.01"', b'<Trade transactionType="BookTrade" levelOfDetail="EXECUTION" ibExecID="a.b.1.01"')
    assert parse_report(only_book, 'UTC')[0]['trades'] == []
    # A genuine zero-price fill is still refused rather than silently dropped.
    with pytest.raises(ValueError, match='price missing'):
        parse_report(report().replace(b'tradePrice="100"', b'tradePrice="0"'), 'UTC')


# ── Flex parsing and storage ────────────────────────────────────────────────────

def test_parse_nav_and_only_external_deposits_in_base_currency():
    xml = report(nav_row('20261102', 1000) + nav_row('20261103', 1013),
                 deposit('20261102', 1000, 'D1') + deposit('20261103', 50, 'D2', 'USD', '0.8') +
                 deposit('20261103', 3, 'DIV', kind='Dividends'))
    rec = parse_report(xml, 'UTC')[0]
    assert rec['nav'] == {'2026-11-02': 1000.0, '2026-11-03': 1013.0}
    assert rec['flows'] == [{'day': '2026-11-02', 'amount': 1000.0, 'id': 'D1'},
                            {'day': '2026-11-03', 'amount': 40.0, 'id': 'D2'}]   # 50 USD * 0.8


def test_missing_sections_are_reported_as_none_and_do_not_break_existing_queries():
    rec = parse_report(report(nav_section=False, cash_section=False), 'UTC')[0]
    assert rec['nav'] is None and rec['flows'] is None
    assert len(rec['trades']) == 1     # trades and lots still parsed


def test_bad_nav_and_unconvertible_deposits_are_rejected():
    with pytest.raises(ValueError, match='NAV'):
        parse_report(report(nav_row('20261102', 'nan')), 'UTC')
    with pytest.raises(ValueError, match='NAV'):
        parse_report(report(nav_row('20261102', -5)), 'UTC')
    with pytest.raises(ValueError, match='FX rate'):
        parse_report(report(cash_rows=deposit('20261102', 10, 'X', 'USD', '')), 'UTC')
    with pytest.raises(ValueError, match='Detail'):
        parse_report(report(cash_rows=deposit('20261102', 10, 'X', detail='SUMMARY')), 'UTC')


def test_nav_and_flows_merge_across_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(flex, '_PATH', tmp_path / 'h.json')
    flex.save_history('TEST', nav={'2026-11-02': 1.0, '2026-11-03': 2.0},
                      flows=[{'day': '2026-11-02', 'amount': 1.0, 'id': 'a'}])
    flex.save_history('TEST', nav={'2026-11-03': 3.0},
                      flows=[{'day': '2026-11-02', 'amount': 1.5, 'id': 'a'},
                             {'day': '2026-11-04', 'amount': 2.0, 'id': 'b'}])
    state = flex.read_history()['accounts']['TEST']
    assert state['nav'] == {'2026-11-02': 1.0, '2026-11-03': 3.0}     # newer report wins
    assert [(f['id'], f['amount']) for f in state['flows']] == [('a', 1.5), ('b', 2.0)]
    flex.save_history('TEST')                                          # None leaves them alone
    assert flex.read_history()['accounts']['TEST']['nav']
    flex.save_history('TEST', flows=[])                                # [] is "present, none"
    assert 'flows' in flex.read_history()['accounts']['TEST']


def test_invalid_stored_nav_blocks_the_write(tmp_path, monkeypatch):
    path = tmp_path / 'h.json'
    monkeypatch.setattr(flex, '_PATH', path)
    flex.save_history('TEST', nav={'2026-11-02': 1.0})
    before = path.read_bytes()
    with pytest.raises(ValueError):
        flex.save_history('TEST', nav={'not-a-date': 1.0})
    assert path.read_bytes() == before


def test_inception_series_explains_what_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(flex, '_PATH', tmp_path / 'h.json')
    with pytest.raises(ValueError, match='Net Asset Value'):
        perf.inception_series('TEST')
    flex.save_history('TEST', nav={'2026-11-02': 100.0, '2026-11-03': 101.0})
    with pytest.raises(ValueError, match='Cash Transactions'):
        perf.inception_series('TEST')
    flex.save_history('TEST', flows=[])
    assert perf.inception_series('TEST').iloc[-1] == pytest.approx(1.01)
    with pytest.raises(ValueError):
        perf.inception_series(None)


# ── chart ───────────────────────────────────────────────────────────────────────

def test_since_inception_chart_uses_the_account_return_and_compares(tmp_path, monkeypatch):
    import ui.callbacks.performance as ui
    monkeypatch.setattr(flex, '_PATH', tmp_path / 'h.json')
    flex.save_history('TEST', nav={'2026-11-02': 1000.0, '2026-11-03': 1013.0, '2026-11-04': 1000.0},
                      flows=[{'day': '2026-11-02', 'amount': 1000.0, 'id': 'init'}])
    calls = []
    idx = pd.to_datetime(['2026-10-30', '2026-11-02', '2026-11-03', '2026-11-04'])
    bench = pd.Series([100.0, 100.0, 101.0, 102.0], index=idx)

    def compare(symbol, period, *window):
        calls.append((period, window))
        return bench

    monkeypatch.setattr(ui, 'get_compare_history', compare)
    holdings = {'base': 'EUR', 'positions': [], 'account': {'account_id': 'TEST', 'net_liquidation': 1000.0}}
    fig, stats, note = ui.build_performance(holdings, ui.INCEPTION, 'pct', ['SPY'])
    portfolio = next(t for t in fig.data if t.name == 'Portfolio')
    assert portfolio.y[-1] == pytest.approx(0.0, abs=1e-9)          # 1000 -> 1013 -> 1000, cash included
    assert max(portfolio.y) == pytest.approx(1.3)                    # +1.3% at the peak
    assert calls[0][0] == 'Custom' and 'Time-weighted' in note

    fig, _, note = ui.build_performance(holdings, ui.INCEPTION, 'abs', [])
    assert fig.data[0].y[0] == pytest.approx(1000.0)                 # starting capital


def test_since_inception_without_flex_history_says_how_to_enable_it(tmp_path, monkeypatch):
    import ui.callbacks.performance as ui
    monkeypatch.setattr(flex, '_PATH', tmp_path / 'h.json')
    holdings = {'base': 'EUR', 'positions': [], 'account': {'account_id': 'TEST', 'net_liquidation': 1000.0}}
    fig, stats, note = ui.build_performance(holdings, ui.INCEPTION, 'pct', [])
    assert not fig.data and stats == [] and 'Net Asset Value' in note
    assert ph.DEFAULT_PERIOD == '1Y'      # other periods keep using the holdings backcast
