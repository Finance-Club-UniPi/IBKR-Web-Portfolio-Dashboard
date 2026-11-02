"""Regression tests for trade-lot FX attribution."""

import unittest
from datetime import date
from unittest.mock import patch

import fx_history
from data_processor import process_positions
from fx_impact import total_fx_impact


class TradeLotFxPrecedenceTests(unittest.TestCase):
    def setUp(self):
        self.position = {
            'ticker': 'AAPL',
            'conId': 1,
            'quantity': 2,
            'avg_cost': 150,
            'current_price': 300,
            'market_value': 600,
            'unrealized_pnl': 300,
            'realized_pnl': 0,
            'price_stale': False,
            'currency': 'USD',
            'multiplier': 1.0,
            'price_unit_verified': True,
            'fx_native_price': 300,
            'base_market_value': 600,
            'base_unrealized_pnl': 340,
        }
        self.rates = {'USD': {'now': 1.0, 'previous': 0.99}}
        self.trades = [
            {'ticker': 'AAPL', 'side': 'BUY', 'shares': 1, 'price': 100, 'time': '2026-01-05T10:00:00'},
            {'ticker': 'AAPL', 'side': 'BUY', 'shares': 1, 'price': 200, 'time': '2026-02-05T10:00:00'},
        ]
        self.historical_rates = {date(2026, 1, 5): 0.80, date(2026, 2, 5): 0.90}

    def test_complete_trade_history_overrides_incorrect_aggregate_estimate(self):
        with patch.object(fx_history, 'fx_history', return_value=self.historical_rates):
            trade_fx = fx_history.compute_trade_fx(
                [self.position], 'EUR', self.trades, daily_fx_rates=self.rates,
            )

        # The aggregate formula returns €80; purchase-lot rates correctly return €90.
        self.assertEqual(total_fx_impact(self.position, 'EUR', self.rates), (80.0, ''))
        self.assertEqual(trade_fx['AAPL'], (90.0, ''))

        df = process_positions(
            [self.position], {}, base_currency='EUR', daily_fx_rates=self.rates, trade_fx=trade_fx,
        )
        self.assertEqual(df.loc[0, 'fx_total'], 90.0)
        self.assertEqual(df.loc[0, 'fx_total_source'], 'trade history')

    def test_incomplete_trade_history_stays_unavailable(self):
        with patch.object(fx_history, 'fx_history', return_value=self.historical_rates):
            trade_fx = fx_history.compute_trade_fx(
                [self.position], 'EUR', self.trades[:1], daily_fx_rates=self.rates,
            )

        df = process_positions(
            [self.position], {}, base_currency='EUR', daily_fx_rates=self.rates, trade_fx=trade_fx,
        )
        self.assertTrue(df['fx_total'].isna().all())
        self.assertIn('Trade history covers 1 of 2 shares', df.loc[0, 'fx_total_reason'])


if __name__ == '__main__':
    unittest.main()
