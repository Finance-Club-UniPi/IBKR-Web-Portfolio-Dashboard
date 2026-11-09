"""Tests that unverified mixed-currency values are not combined."""

import unittest

from backend.portfolio.positions import get_summary, process_positions


def mixed_currency_portfolio(base_values=None):
    positions = [
        {
            'ticker': 'EUR_HOLDING', 'currency': 'EUR', 'quantity': 1,
            'avg_cost': 100, 'current_price': 100, 'market_value': 100,
            'unrealized_pnl': 0, 'realized_pnl': 0, 'price_unit_verified': False,
        },
        {
            'ticker': 'USD_HOLDING', 'currency': 'USD', 'quantity': 1,
            'avg_cost': 100, 'current_price': 100, 'market_value': 100,
            'unrealized_pnl': 0, 'realized_pnl': 0, 'price_unit_verified': False,
        },
    ]
    if base_values:
        for position, base_value in zip(positions, base_values, strict=True):
            position['base_market_value'] = base_value
    return process_positions(positions, base_currency='EUR', betas={
        'EUR_HOLDING': 1.0, 'USD_HOLDING': 2.0,
    })


class TestMixedCurrencyWeights(unittest.TestCase):
    def test_1_unverified_mixed_currency_values_have_no_allocation_weights(self):
        # EUR 100 and USD 100 are not equal exposures; without verified common-currency
        # values, suppress weights and the aggregate beta instead of reporting 50/50.
        self.assertTrue(mixed_currency_portfolio()['allocation_pct'].isna().all())

    def test_2_unverified_mixed_currency_values_have_no_portfolio_beta(self):
        df = mixed_currency_portfolio()
        self.assertIsNone(get_summary(df)['portfolio_beta'])
        self.assertIsNone(get_summary(df)['largest_position_pct'])

    def test_3_verified_base_values_produce_correct_mixed_currency_weights(self):
        df = mixed_currency_portfolio([100, 50])
        values = df.set_index('ticker')['allocation_pct']

        self.assertEqual(values['EUR_HOLDING'], 66.67)
        self.assertEqual(values['USD_HOLDING'], 33.33)
        self.assertIsNone(get_summary(df)['portfolio_beta'])  # Historical analysis is separate.


if __name__ == '__main__':
    unittest.main()
