"""
Pure pandas transforms over raw IBKR positions.

Takes the raw `positions` list + `market_data` dict from `ibkr_client`
and enriches it with: pnl_pct, total_cost, allocation_pct, daily change,
bid/ask spread, 52-week range position, volume, VWAP.

No network, no state. Everything is deterministic given its inputs — so
these functions are safe to call from any thread or from tests.
"""

from __future__ import annotations

import math

import pandas as pd

from backend.portfolio.fx import daily_fx_impact


def process_positions(positions, market_data=None, *, base_currency='EUR', daily_fx_rates=None,
                      betas=None, trade_fx=None):
    if not positions:
        return pd.DataFrame()
    trade_fx = trade_fx or {}

    df = pd.DataFrame(positions)
    market_data = market_data or {}
    betas = betas or {}

    if 'price_stale' not in df.columns:
        df['price_stale'] = False
    df['pnl_pct'] = ((df['current_price'] - df['avg_cost']) / df['avg_cost'] * 100).round(2)
    df['total_cost'] = (df['avg_cost'] * df['quantity']).round(2)

    base_values = _verified_base_values(df)
    total_base_value = base_values.sum() if base_values is not None else None
    if (total_base_value is not None and total_base_value > 0
            and math.isfinite(float(total_base_value))):
        df['allocation_pct'] = ((base_values / total_base_value) * 100).round(2)
    else:
        df['allocation_pct'] = float('nan')

    # Market data enrichment
    daily_changes = []
    daily_change_pcts = []
    spreads = []
    low_52w_list = []
    high_52w_list = []
    range_pct_list = []
    volumes = []
    vwaps = []

    for _, row in df.iterrows():
        sym = row['ticker']
        md = market_data.get(sym, {})
        price = row['current_price']

        # Daily change vs previous close
        prev_close = md.get('prev_close')
        if prev_close and prev_close > 0:
            chg = round(price - prev_close, 2)
            chg_pct = round((chg / prev_close) * 100, 2)
        else:
            chg = None
            chg_pct = None
        daily_changes.append(chg)
        daily_change_pcts.append(chg_pct)

        # Bid/ask spread
        bid = md.get('bid')
        ask = md.get('ask')
        if bid and ask and bid > 0 and ask > 0:
            spreads.append(round(ask - bid, 4))
        else:
            spreads.append(None)

        # 52-week range
        low_52w = md.get('low_52w')
        high_52w = md.get('high_52w')
        low_52w_list.append(low_52w)
        high_52w_list.append(high_52w)
        if low_52w and high_52w and high_52w > low_52w:
            range_pct = round((price - low_52w) / (high_52w - low_52w) * 100, 1)
        else:
            range_pct = None
        range_pct_list.append(range_pct)

        volumes.append(md.get('volume'))
        vwaps.append(md.get('vwap'))

    df['daily_change'] = daily_changes
    df['daily_change_pct'] = daily_change_pcts
    df['spread'] = spreads
    df['low_52w'] = low_52w_list
    df['high_52w'] = high_52w_list
    df['range_52w_pct'] = range_pct_list
    df['volume'] = volumes
    df['vwap'] = vwaps

    fx = [daily_fx_impact(row.to_dict(), base_currency, daily_fx_rates or {})
          for _, row in df.iterrows()]
    df['fx_today'] = [value for value, _ in fx]
    df['fx_today_reason'] = [reason for _, reason in fx]
    fx_total = []
    # Only complete trade-lot estimates can supply since-purchase FX.
    sources = []
    for _, row in df.iterrows():
        ticker = row['ticker']
        if ticker in trade_fx:
            value, reason = trade_fx[ticker]
            source = 'trade history'
        elif str(row.get('currency') or '').upper() == base_currency.upper():
            value, reason, source = 0.0, 'No currency conversion', None
        else:
            value, reason, source = None, 'Trade-history FX estimate is not available yet', None
        fx_total.append((value, reason))
        sources.append(source if value is not None else None)
    df['fx_total'] = pd.Series([value for value, _ in fx_total], index=df.index, dtype='float64')
    df['fx_total_reason'] = [reason for _, reason in fx_total]
    df['fx_total_source'] = sources
    df['beta'] = pd.Series([betas.get(t) for t in df['ticker']], index=df.index, dtype='float64')

    df = df.sort_values('market_value', ascending=False).reset_index(drop=True)
    return df


def portfolio_beta(df):
    """Value-weighted beta of the holdings that have one.

    Weights are re-normalised over the covered holdings, so a missing beta
    never drags the result toward zero. Cash is excluded (invested beta).
    Returns (beta | None, covered_weight_pct, covered_count).
    """
    if df.empty or 'beta' not in df.columns:
        return None, 0.0, 0
    base_values = _verified_base_values(df)
    if base_values is None:
        return None, 0.0, 0
    if (base_values < 0).any():
        return None, 0.0, 0
    total = base_values.sum()
    if not math.isfinite(float(total)) or total <= 0:
        return None, 0.0, 0
    known_mask = df['beta'].apply(lambda v: pd.notna(v) and math.isfinite(float(v))) & (base_values > 0)
    known = df.loc[known_mask]
    covered_values = base_values[known_mask]
    covered = covered_values.sum()
    if not total or not covered:
        return None, 0.0, 0
    beta = (known['beta'] * covered_values).sum() / covered
    return round(float(beta), 2), round(float(covered / total * 100), 1), len(known)


def _verified_base_values(df):
    """Return finite per-position base-currency values only when all are known."""
    if 'base_market_value' not in df.columns:
        return None
    values = pd.to_numeric(df['base_market_value'], errors='coerce')
    if values.isna().any() or not all(math.isfinite(float(value)) for value in values):
        return None
    return values


def get_summary(df):
    if df.empty:
        return {}

    total_cost = df['total_cost'].sum()
    total_value = df['market_value'].sum()
    total_unrealized = df['unrealized_pnl'].sum()
    total_realized = df['realized_pnl'].sum()

    mask = df['daily_change'].notna()
    total_daily_pnl = round((df.loc[mask, 'daily_change'] * df.loc[mask, 'quantity']).sum(), 2) if mask.any() else None

    fx = df['fx_today'].dropna() if 'fx_today' in df.columns else df['market_value'].iloc[0:0]
    fx_all = df['fx_total'].dropna() if 'fx_total' in df.columns else fx.iloc[0:0]
    beta, beta_coverage_pct, beta_count = portfolio_beta(df)
    if df['allocation_pct'].notna().all():
        largest_idx = df['allocation_pct'].idxmax()
        largest_position = df.loc[largest_idx, 'ticker']
        largest_position_pct = df.loc[largest_idx, 'allocation_pct']
    else:
        largest_position = None
        largest_position_pct = None

    summary = {
        'total_value':         round(total_value, 2),
        'total_unrealized_pnl':round(total_unrealized, 2),
        'total_realized_pnl':  round(total_realized, 2),
        'total_fx_today':      round(float(fx.sum()), 2) if len(fx) else None,
        'fx_covered':          int(len(fx)),
        'total_fx_total':      round(float(fx_all.sum()), 2) if len(fx_all) else None,
        'fx_total_covered':    int(len(fx_all)),
        'portfolio_beta':      beta,
        'beta_coverage_pct':   beta_coverage_pct,
        'beta_covered':        beta_count,
        'total_pnl_pct':       round(total_unrealized / total_cost * 100, 2) if total_cost else 0,
        'num_positions':       len(df),
        'best_performer':      df.loc[df['pnl_pct'].idxmax(), 'ticker'],
        'worst_performer':     df.loc[df['pnl_pct'].idxmin(), 'ticker'],
        'total_daily_pnl':     total_daily_pnl,
        'largest_position':    largest_position,
        'largest_position_pct':largest_position_pct,
    }

    return summary


def daily_pnl(account: dict, summary: dict):
    """Preserve broker zero; use a computed fallback only in verified base units."""
    value = account.get('daily_pnl')
    if value is None and account.get('portfolio_value_units') == 'base':
        value = summary.get('total_daily_pnl')
    return value if value is not None and math.isfinite(value) else None
