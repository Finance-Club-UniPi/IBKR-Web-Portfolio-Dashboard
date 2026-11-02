"""Regression test for since-purchase FX across multiple buys."""

import unittest
from datetime import date
from unittest.mock import patch

import fx_history
from data_processor import process_positions
from fx_impact import total_fx_impact


class TestTradeLotFx(unittest.TestCase):
    def test_multiple_buys_use_lot_rates_and_incomplete_history_is_unavailable(self):
        position = {
            'ticker': 'AAPL', 'quantity': 2, 'avg_cost': 150, 'current_price': 300,
            'market_value': 600, 'unrealized_pnl': 300, 'realized_pnl': 0,
            'price_stale': False, 'currency': 'USD', 'multiplier': 1,
            'price_unit_verified': True, 'fx_native_price': 300,
            'base_market_value': 600, 'base_unrealized_pnl': 340,
        }
        trades = [
            {'ticker': 'AAPL', 'side': 'BUY', 'shares': 1, 'price': 100, 'time': '2026-01-05'},
            {'ticker': 'AAPL', 'side': 'BUY', 'shares': 1, 'price': 200, 'time': '2026-02-05'},
        ]
        rates = {'USD': {'now': 1.0, 'previous': 0.99}}
        purchase_rates = {date(2026, 1, 5): 0.80, date(2026, 2, 5): 0.90}

        with patch.object(fx_history, 'fx_history', return_value=purchase_rates):
            complete = fx_history.compute_trade_fx([position], 'EUR', trades, daily_fx_rates=rates)
            incomplete = fx_history.compute_trade_fx([position], 'EUR', trades[:1], daily_fx_rates=rates)

        self.assertEqual(total_fx_impact(position, 'EUR', rates), (80.0, ''))
        self.assertEqual(complete['AAPL'], (90.0, ''))

        result = process_positions(
            [position], {}, base_currency='EUR', daily_fx_rates=rates, trade_fx=complete,
        )
        self.assertEqual(result.loc[0, 'fx_total'], 90.0)
        self.assertEqual(result.loc[0, 'fx_total_source'], 'trade history')

        result = process_positions(
            [position], {}, base_currency='EUR', daily_fx_rates=rates, trade_fx=incomplete,
        )
        self.assertTrue(result['fx_total'].isna().all())
        self.assertIn('covers 1 of 2 shares', result.loc[0, 'fx_total_reason'])


if __name__ == '__main__':
    unittest.main()
