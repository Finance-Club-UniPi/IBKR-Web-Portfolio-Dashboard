"""Core dictionary payloads shared by backend services and Dash stores.

None means unavailable. Raw broker market_value/P&L fields have unverified
units unless account.portfolio_value_units says base. base_market_value is
separately valued from verified native quote units and account-currency FX.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict


class PositionData(TypedDict):
    conId: NotRequired[int]
    ticker: str
    quantity: float
    avg_cost: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    pnl_pct: float
    allocation_pct: float
    currency: NotRequired[str]
    multiplier: NotRequired[float | None]
    price_unit_verified: NotRequired[bool]
    fx_native_price: NotRequired[float | None]
    fx_price_stale: NotRequired[bool]
    fx_today: NotRequired[float | None]
    fx_today_reason: NotRequired[str]
    fx_total: NotRequired[float | None]
    fx_total_reason: NotRequired[str]
    fx_total_source: NotRequired[str | None]
    base_market_value: NotRequired[float | None]
    base_unrealized_pnl: NotRequired[float]
    beta: NotRequired[float | None]
    realized_pnl: NotRequired[float]
    price_stale: NotRequired[bool]
    daily_change: NotRequired[float | None]
    daily_change_pct: NotRequired[float | None]
    low_52w: NotRequired[float | None]
    high_52w: NotRequired[float | None]
    spread: NotRequired[float | None]
    volume: NotRequired[float | None]
    vwap: NotRequired[float | None]
    range_52w_pct: NotRequired[float | None]


class SummaryData(TypedDict):
    total_value: float
    total_unrealized_pnl: float
    num_positions: int
    largest_position: str | None
    largest_position_pct: float | None
    best_performer: str
    worst_performer: str
    total_pnl_pct: NotRequired[float | None]
    total_daily_pnl: NotRequired[float | None]
    total_realized_pnl: NotRequired[float]
    total_fx_today: NotRequired[float | None]
    fx_covered: NotRequired[int]
    total_fx_total: NotRequired[float | None]
    fx_total_covered: NotRequired[int]
    portfolio_beta: NotRequired[float | None]
    beta_coverage_pct: NotRequired[float]
    beta_covered: NotRequired[int]


class AccountData(TypedDict, total=False):
    base_currency: str
    portfolio_value_units: str
    cash_base: float
    cash_usd: float
    buying_power: float
    available_funds: float
    excess_liquidity: float
    maint_margin: float
    init_margin: float
    cushion: float
    leverage: float
    equity_with_loan: float
    sma: float
    day_trades_remaining: float
    net_liquidation: float
    gross_position_value: float
    eurusd_rate: float
    daily_pnl: float | None


class PortfolioData(TypedDict):
    summary: SummaryData
    positions: list[PositionData]
    account: AccountData
    trades: NotRequired[list[dict]]
    div_data: NotRequired[dict]


class SectorGeoEntry(TypedDict, total=False):
    sector: str
    industry: str
    country: str
    longName: str
    is_etf: bool
    sector_weights: dict[str, float]


class MarketIntelData(TypedDict):
    tickers: list[str]
    sector_geo: dict[str, SectorGeoEntry]
    earnings: dict


class ValuationData(TypedDict, total=False):
    buffett: dict
    sp500_pe: dict
    cape: dict
    treasury: dict
