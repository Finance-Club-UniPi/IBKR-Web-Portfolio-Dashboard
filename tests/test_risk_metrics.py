"""Known-answer checks for risk_metrics (pure functions, no network)."""

import numpy as np
import pandas as pd
import pytest

from risk_metrics import compute_risk_metrics, max_drawdown


def values_from(returns, start=100.0):
    idx = pd.bdate_range('2024-01-01', periods=len(returns) + 1)
    return pd.Series(start * np.concatenate([[1.0], np.cumprod(1 + np.asarray(returns))]), index=idx)


RNG = np.random.default_rng(0)
NOISY = RNG.normal(0.0005, 0.01, 300)


def test_max_drawdown_known_answer_with_dates():
    idx = pd.bdate_range('2024-01-01', periods=4)
    dd, mdd, peak, trough = max_drawdown(pd.Series([100, 120, 90, 110], index=idx))
    assert mdd == pytest.approx(-0.25)
    assert peak == idx[1] and trough == idx[2]
    assert dd.iloc[-1] == pytest.approx(110 / 120 - 1)


def test_historical_var_matches_known_fifth_percentile():
    r = np.linspace(-0.05, 0.05, 101)        # 5th percentile is exactly -0.045
    m = compute_risk_metrics(values_from(r), None, 0.0)
    assert m['var_hist_95'] == pytest.approx(0.045, abs=1e-9)
    assert m['var_hist_99'] == pytest.approx(0.049, abs=1e-9)
    assert m['var_hist_95_amt'] == pytest.approx(0.045 * m['last_value'], rel=1e-9)


def test_cvar_is_at_least_var_and_equals_tail_mean():
    r = np.linspace(-0.05, 0.05, 101)
    m = compute_risk_metrics(values_from(r), None, 0.0)
    assert m['cvar_95'] >= m['var_hist_95']
    assert m['cvar_95'] == pytest.approx(-r[r <= -0.045].mean(), abs=1e-9)
    assert compute_risk_metrics(values_from(NOISY), None, 0.0)['cvar_95'] >= \
        compute_risk_metrics(values_from(NOISY), None, 0.0)['var_hist_95']


def test_sharpe_and_sortino_share_the_same_rf():
    lo = compute_risk_metrics(values_from(NOISY), None, 0.0)
    hi = compute_risk_metrics(values_from(NOISY), None, 0.05)
    assert hi['sharpe'] < lo['sharpe'] and hi['sortino'] < lo['sortino']
    ex = NOISY - 0.05 / 252
    assert hi['sharpe'] == pytest.approx(ex.mean() / ex.std(ddof=1) * np.sqrt(252))
    dd = np.sqrt(np.mean(np.minimum(ex, 0) ** 2))
    assert hi['sortino'] == pytest.approx(ex.mean() / dd * np.sqrt(252))


def test_parametric_var_is_normal_quantile():
    m = compute_risk_metrics(values_from(NOISY), None, 0.0)
    mu, sd = NOISY.mean(), NOISY.std(ddof=1)
    assert m['var_param_95'] == pytest.approx(-(mu - 1.6448536269514722 * sd), abs=1e-9)


def test_beta_is_two_when_portfolio_is_twice_benchmark():
    bm = NOISY
    bench = values_from(bm)
    port = values_from(2 * bm)
    m = compute_risk_metrics(port, bench, 0.0)
    assert m['beta'] == pytest.approx(2.0, abs=1e-9)
    assert m['alpha'] == pytest.approx(0.0, abs=1e-9)


def test_beta_none_without_benchmark():
    m = compute_risk_metrics(values_from(NOISY), None, 0.0)
    assert m['beta'] is None and m['alpha'] is None and m['sharpe'] is not None


def test_fewer_than_30_returns_gives_all_none_without_raising():
    m = compute_risk_metrics(values_from(NOISY[:29]), None, 0.0)
    assert m['n_obs'] == 29
    assert all(v is None for k, v in m.items() if k != 'n_obs')


def test_benchmark_converted_to_base_with_ffilled_fx():
    from dashboard_core.risk import to_base
    idx = pd.bdate_range('2024-01-01', periods=3)
    spx = pd.Series([100.0, 110.0, 121.0], index=idx)
    fx = pd.Series([0.9, 0.8], index=idx[[0, 1]])   # no rate on the last day
    assert to_base(spx, fx).round(6).tolist() == [90.0, 88.0, 96.8]
    assert to_base(spx, None) is None


def test_risk_free_is_averaged_over_the_lookback_window_only():
    from dashboard_core.risk import rf_over
    rates = pd.Series([4.0, 4.0, 2.0, 2.0], index=pd.bdate_range('2024-01-01', periods=4))
    assert rf_over(rates, rates.index[2:]) == pytest.approx(0.02)
    assert rf_over(rates, pd.bdate_range('2030-01-01', periods=3)) is None
    assert rf_over(None, rates.index) is None
