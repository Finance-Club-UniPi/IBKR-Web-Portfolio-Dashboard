"""Broker FX regressions with no TWS connection or external requests."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from ib_async import IB, Stock, Ticker

import backend.providers.ibkr as broker


@pytest.fixture(autouse=True)
def isolated_valuations(monkeypatch):
    import backend.storage.cache as cache
    monkeypatch.setattr(cache, '_DISK', None)
    monkeypatch.setattr(cache, '_MEM', {})


class FakeIB:
    _logger = logging.getLogger(__name__)
    qualifyContractsAsync = IB.qualifyContractsAsync

    def __init__(self, failure=None, base='EUR'):
        self.failure = failure
        self.base = base
        self.qualified = []
        self.requested = []
        self.cancelled = []
        self.generic_requests = []
        self.history_timeouts = []
        self.cancelled_qualification = False
        self.stock = Stock('TEST', 'SMART', 'USD' if base == 'EUR' else 'EUR', conId=42)

    def isConnected(self):
        return True

    def portfolio(self):
        return [SimpleNamespace(contract=self.stock, position=2, marketPrice=100,
                                marketValue=200, averageCost=90, unrealizedPNL=20, realizedPNL=0)]

    def accountValues(self):
        return [SimpleNamespace(tag='BaseCurrency', value=self.base, currency='', account='TEST')]

    def reqMarketDataType(self, kind):
        pass

    async def reqContractDetailsAsync(self, contract):
        if contract.secType == 'STK':
            await asyncio.sleep(.01)
            contract.exchange = contract.exchange or 'SMART'
        if contract.secType == 'CASH':
            self.qualified.append(contract.pair())
            if self.failure == 'qualification_stall':
                try:
                    await asyncio.Event().wait()
                finally:
                    self.cancelled_qualification = True
            if self.failure == 'unknown':
                return []
            if self.failure == 'no_conid':
                return [SimpleNamespace(contract=contract)]
            contract.conId = 200
        return [SimpleNamespace(contract=contract)]

    def reqMktData(self, contract, **kwargs):
        fx = contract.secType == 'CASH'
        if fx:
            self.requested.append(contract.pair())
            assert kwargs['snapshot'] is False
        elif 'genericTickList' in kwargs:
            assert kwargs.get('genericTickList') == '456,165'
            assert contract.exchange, 'Subscriptions must wait for contract qualification'
            self.generic_requests.append(kwargs['genericTickList'])
        price = 1.25 if fx else 100.0
        stamp = datetime.now(timezone.utc)
        if fx and self.failure == 'stale':
            stamp -= timedelta(days=2)
        if fx and self.failure == 'quote_stall':
            return Ticker(contract=contract)
        ticker = Ticker(contract=contract)
        ticker.time = stamp
        ticker.marketDataType = 3 if fx and self.failure == 'delayed' else 1
        ticker.bid = ticker.ask = ticker.last = price
        ticker.bidSize = ticker.askSize = 1
        ticker.low52week, ticker.high52week = price / 2, price * 2
        return ticker

    def cancelMktData(self, contract):
        if contract.secType == 'CASH':
            self.cancelled.append(contract.pair())

    async def reqTickersAsync(self, *contracts):
        assert all(c.secType != 'CASH' for c in contracts), 'FX must use cancellable snapshots'
        return [self.reqMktData(c) for c in contracts]

    async def reqHistoricalDataAsync(self, contract, **kwargs):
        self.history_timeouts.append(kwargs['timeout'])
        if self.failure == 'history_timeout':
            await asyncio.sleep(kwargs['timeout'])
            return []
        return [SimpleNamespace(date=datetime.now(timezone.utc).date() - timedelta(days=1), close=1.20)]

    async def reqExecutionsAsync(self, filt):
        return []

    def reqPnL(self, account):
        return SimpleNamespace(dailyPnL=0.0)

    def cancelPnL(self, account):
        pass


@pytest.mark.parametrize('failure', ['unknown', 'no_conid'])
def test_rejected_contract_never_requests_quotes(failure):
    async def run():
        ib = FakeIB(failure)
        assert await broker._fx_ticker(ib, 'EURUSD', .1) is None
        assert ib.requested == []
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['qualification_stall', 'quote_stall'])
def test_fx_deadline_cancels_pending_work(failure):
    async def run():
        ib = FakeIB(failure)
        assert await asyncio.wait_for(broker._fx_ticker(ib, 'EURUSD', .02), .5) is None
        if failure == 'qualification_stall':
            assert ib.cancelled_qualification and not ib.requested
        else:
            assert ib.cancelled == ib.requested == ['EURUSD']
    asyncio.run(run())


@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_caller_cancellation_also_releases_fx_subscription(cleanup_fails):
    async def run():
        ib = FakeIB('quote_stall')
        task = asyncio.create_task(broker._fx_ticker(ib, 'EURUSD', 1))
        while not ib.requested:
            await asyncio.sleep(0)
        cancel = ib.cancelMktData

        def cleanup(contract):
            cancel(contract)
            if cleanup_fails:
                raise ConnectionError('Broker disconnected')

        ib.cancelMktData = cleanup
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ib.cancelled == ['EURUSD']
    asyncio.run(run())


@pytest.mark.parametrize('base,now,previous', [('EUR', .8, 1 / 1.2), ('USD', 1.25, 1.2)])
def test_fetch_normalizes_supported_pair_in_both_directions(monkeypatch, base, now, previous):
    async def run():
        ib = FakeIB(base=base)
        connection = broker._IBKRConnection()
        connection._ib = ib
        result = await asyncio.wait_for(connection._do_fetch(), 1)
        assert set(ib.qualified) == {'EURUSD'}
        ccy = 'USD' if base == 'EUR' else 'EUR'
        assert result['daily_fx_rates'][ccy]['now'] == pytest.approx(now)
        assert result['daily_fx_rates'][ccy]['previous'] == pytest.approx(previous)
        assert result['account']['eurusd_rate'] == 1.25
        assert result['positions'][0]['base_market_value'] == pytest.approx(200 * now)
        assert ib.cancelled == ib.requested
        assert ib.requested == ['EURUSD']  # Display and attribution share one subscription.
        assert all(0 < timeout <= broker._FX_BUDGET for timeout in ib.history_timeouts)
    monkeypatch.setattr(broker, 'fx_session_open', lambda: True)
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['unknown', 'no_conid', 'qualification_stall', 'quote_stall',
                                     'history_timeout', 'stale', 'delayed'])
def test_optional_fx_failure_keeps_portfolio_and_marks_fx_unavailable(monkeypatch, failure):
    async def run():
        ib = FakeIB(failure)
        connection = broker._IBKRConnection()
        connection._ib = ib
        result = await asyncio.wait_for(connection._do_fetch(), 1)
        assert result['positions'][0]['ticker'] == 'TEST'
        if failure == 'history_timeout':
            assert result['daily_fx_rates']['USD']['now'] == .8
            assert 'previous' not in result['daily_fx_rates']['USD']
        else:
            assert result['daily_fx_rates'] == {}
        assert (result['positions'][0]['base_market_value'] is not None) == (failure == 'history_timeout')
        assert result['account']['daily_pnl'] == 0
        assert ib.cancelled == ib.requested
        if failure in ('unknown', 'no_conid', 'qualification_stall', 'quote_stall'):
            assert result['account']['eurusd_rate'] == broker.cfg['display']['eurusd_fallback']
        if failure in ('unknown', 'no_conid'):
            assert ib.requested == []
    monkeypatch.setattr(broker, 'fx_session_open', lambda: True)
    monkeypatch.setattr(broker, '_FX_QUOTE_TIMEOUT', .02)
    monkeypatch.setattr(broker, '_FX_BUDGET', .04)
    asyncio.run(run())


def test_other_currency_can_use_inverse_after_rejected_direct_pair(monkeypatch):
    async def run():
        ib = FakeIB()
        ib.stock.currency = 'GBP'
        qualify = ib.reqContractDetailsAsync

        async def details(contract):
            if contract.secType == 'CASH' and contract.pair() == 'GBPEUR':
                ib.qualified.append('GBPEUR')
                return []
            return await qualify(contract)

        ib.reqContractDetailsAsync = details
        connection = broker._IBKRConnection()
        connection._ib = ib
        result = await asyncio.wait_for(connection._do_fetch(), 1)
        assert result['daily_fx_rates']['GBP']['now'] == .8
        assert result['daily_fx_rates']['GBP']['previous'] == pytest.approx(1 / 1.2)
        assert 'GBPEUR' in ib.qualified and 'GBPEUR' not in ib.requested
        assert 'EURGBP' in ib.requested
        assert ib.cancelled == ib.requested
    monkeypatch.setattr(broker, 'fx_session_open', lambda: True)
    asyncio.run(run())


def test_stalled_currency_does_not_discard_successful_currency(monkeypatch):
    async def run():
        ib = FakeIB()
        stock = ib.portfolio()[0]
        extra = SimpleNamespace(**{**vars(stock), 'contract': Stock('OTHER', 'SMART', 'GBP', conId=43)})
        ib.portfolio = lambda: [stock, extra]
        qualify = ib.reqContractDetailsAsync
        stalled = []

        async def details(contract):
            if contract.secType == 'CASH' and 'GBP' in contract.pair():
                try:
                    await asyncio.Event().wait()
                finally:
                    stalled.append(contract.pair())
            return await qualify(contract)

        ib.reqContractDetailsAsync = details
        connection = broker._IBKRConnection()
        connection._ib = ib
        result = await asyncio.wait_for(connection._do_fetch(), 1)
        assert set(result['daily_fx_rates']) == {'USD'}
        assert len(stalled) == 2
        assert len(result['positions']) == 2
        assert result['positions'][0]['base_market_value'] == 160
        assert result['positions'][1]['base_market_value'] is None
    monkeypatch.setattr(broker, 'fx_session_open', lambda: True)
    monkeypatch.setattr(broker, '_FX_QUOTE_TIMEOUT', .02)
    monkeypatch.setattr(broker, '_FX_BUDGET', .04)
    asyncio.run(run())


def test_account_exchange_rate_and_nlv_currency_work_without_forex_quotes(monkeypatch):
    async def run():
        ib = FakeIB('unknown')
        ib.accountValues = lambda: [
            SimpleNamespace(tag='NetLiquidation', value='1000', currency='EUR', account='TEST'),
            SimpleNamespace(tag='ExchangeRate', value='.8', currency='USD', account='TEST'),
            SimpleNamespace(tag='AccountReady', value='true', currency='', account='TEST')]
        connection = broker._IBKRConnection()
        connection._ib = ib
        result = await connection._do_fetch()
        assert result['account']['base_currency'] == 'EUR'
        assert result['positions'][0]['base_market_value'] == 160
        assert result['valuation_rates']['USD']['source'] == 'IBKR account ExchangeRate'
        assert not ib.requested
    monkeypatch.setattr(broker, 'fx_session_open', lambda: True)
    asyncio.run(run())


def test_first_portfolio_result_does_not_wait_for_stalled_optional_requests(monkeypatch):
    async def run():
        ib = FakeIB('qualification_stall')
        connection = broker._IBKRConnection()
        connection._ib = ib
        result = await asyncio.wait_for(connection._do_fetch(), .5)
        assert result['positions']
        assert any(not task.done() for task in connection._extra_tasks.values())
        for task in connection._extra_tasks.values():
            task.cancel()
        await asyncio.gather(*connection._extra_tasks.values(), return_exceptions=True)
    monkeypatch.setattr(broker, 'fx_session_open', lambda: True)
    asyncio.run(run())


def test_dividend_subscription_waits_for_a_missing_exchange_to_be_qualified():
    async def run():
        ib = FakeIB()
        ib.stock.exchange = ''
        connection = broker._IBKRConnection()
        connection._ib = ib
        await connection._do_fetch()
        await asyncio.wait_for(asyncio.gather(*connection._extra_tasks.values()), 1)
        assert ib.generic_requests == ['456,165']
        assert ib.stock.exchange == 'SMART'
    asyncio.run(run())


def test_cancelled_dividend_task_releases_its_stream():
    async def run():
        ib = FakeIB()
        request = ib.reqMktData
        cancelled = []

        def subscribe(contract, **kwargs):
            ticker = request(contract, **kwargs)
            if kwargs.get('genericTickList'):
                ticker.low52week = float('nan')
            return ticker

        ib.reqMktData = subscribe
        ib.cancelMktData = lambda contract: cancelled.append(contract.secType)
        connection = broker._IBKRConnection()
        connection._ib = ib
        await connection._do_fetch()
        task = next(task for (_, kind), task in connection._extra_tasks.items() if kind == 'dividends')
        task.cancel()
        await asyncio.gather(*connection._extra_tasks.values(), return_exceptions=True)
        assert cancelled.count('STK') == 1
    asyncio.run(run())


def test_holdings_change_does_not_duplicate_an_inflight_fx_stream(monkeypatch):
    async def run():
        ib = FakeIB('quote_stall')
        connection = broker._IBKRConnection()
        connection._ib = ib
        await connection._do_fetch()
        position = ib.portfolio()[0]
        position.position = 3
        ib.portfolio = lambda: [position]
        await connection._do_fetch()
        assert ib.requested == ['EURUSD']
        for task in connection._extra_tasks.values():
            task.cancel()
        await asyncio.gather(*connection._extra_tasks.values(), return_exceptions=True)
        assert ib.cancelled == ['EURUSD']
    monkeypatch.setattr(broker, 'fx_session_open', lambda: True)
    asyncio.run(run())
