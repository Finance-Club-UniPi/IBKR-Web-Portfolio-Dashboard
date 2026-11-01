"""Portfolio risk metrics from a daily value series. Pure pandas/numpy, no I/O."""

from __future__ import annotations

from statistics import NormalDist

import numpy as np
import pandas as pd

TRADING_DAYS = 252
MIN_OBS = 30


def max_drawdown(values: pd.Series):
    """(drawdown series, max drawdown as a negative fraction, peak date, trough date)."""
    dd = values / values.cummax() - 1
    trough = dd.idxmin()
    peak = values.loc[:trough].idxmax()
    return dd, float(dd.min()), peak, trough


def _tail(returns: pd.Series, conf: float, last: float) -> dict:
    """Historical + normal VaR at `conf` as positive losses (fraction and currency)."""
    q = 1 - conf
    hist = -float(np.percentile(returns, q * 100))
    sd = float(returns.std())
    param = -NormalDist(float(returns.mean()), sd).inv_cdf(q) if sd > 0 else None
    return {'hist': hist, 'hist_amt': hist * last,
            'param': param, 'param_amt': None if param is None else param * last}


def compute_risk_metrics(values: pd.Series, benchmark: pd.Series | None,
                         rf_annual: float, budget: float | None = None) -> dict:
    """Risk metrics on daily simple returns. `budget` overrides the currency base
    for the VaR/CVaR amounts (default: latest portfolio value). Every metric is
    None when there are fewer than MIN_OBS returns; never raises on short input."""
    keys = ['start', 'end', 'last_value', 'var_hist_95', 'var_hist_99', 'var_hist_95_amt',
            'var_hist_99_amt', 'var_param_95', 'var_param_99', 'var_param_95_amt',
            'var_param_99_amt', 'cvar_95', 'cvar_95_amt', 'vol', 'mean_ann', 'sharpe',
            'sortino', 'max_drawdown', 'dd_peak', 'dd_trough', 'drawdown', 'beta', 'alpha',
            'skew', 'kurt', 'returns']
    values = values.dropna()
    returns = values.pct_change().dropna()
    out = {k: None for k in keys}
    out['n_obs'] = len(returns)
    if len(returns) < MIN_OBS:
        return out

    last = float(budget if budget is not None else values.iloc[-1])
    rf_d = rf_annual / TRADING_DAYS
    mean, sd = float(returns.mean()), float(returns.std())
    excess = returns - rf_d
    down = float(np.sqrt(np.mean(np.minimum(excess, 0) ** 2)))
    t95, t99 = _tail(returns, 0.95, last), _tail(returns, 0.99, last)
    cutoff = np.percentile(returns, 5)
    cvar = -float(returns[returns <= cutoff].mean())
    dd, mdd, peak, trough = max_drawdown(values)

    out.update(
        start=returns.index[0], end=returns.index[-1], last_value=last,
        var_hist_95=t95['hist'], var_hist_99=t99['hist'],
        var_hist_95_amt=t95['hist_amt'], var_hist_99_amt=t99['hist_amt'],
        var_param_95=t95['param'], var_param_99=t99['param'],
        var_param_95_amt=t95['param_amt'], var_param_99_amt=t99['param_amt'],
        cvar_95=cvar, cvar_95_amt=cvar * last,
        vol=sd * np.sqrt(TRADING_DAYS), mean_ann=mean * TRADING_DAYS,
        sharpe=float(excess.mean()) / sd * np.sqrt(TRADING_DAYS) if sd > 0 else None,
        sortino=float(excess.mean()) / down * np.sqrt(TRADING_DAYS) if down > 0 else None,
        max_drawdown=mdd, dd_peak=peak, dd_trough=trough, drawdown=dd,
        skew=float(returns.skew()), kurt=float(returns.kurt()), returns=returns,
    )

    if benchmark is not None:
        bench = benchmark.dropna()
        bench = bench[~bench.index.duplicated()].reindex(values.index, method='ffill')
        pair = pd.concat([returns, bench.pct_change()], axis=1, keys=['p', 'm']).dropna()
        if len(pair) >= MIN_OBS:
            x, y = pair['m'] - rf_d, pair['p'] - rf_d
            var_x = float(x.var())
            if var_x > 0:
                beta = float(x.cov(y)) / var_x
                out.update(beta=beta,
                           alpha=(float(y.mean()) - beta * float(x.mean())) * TRADING_DAYS)
    return out
