"""Pure helpers behind the PDF export (dashboard_core/export.py).

Sector/country roll-up, period returns versus the S&P 500, the weekly
comparison series and the reportlab line chart.  `fetch_performance` is the only
function that touches the network; it reuses the shared 4-hour price cache.
"""

from __future__ import annotations

import logging

import pandas as pd

from net_util import run_parallel
from portfolio_history import compare_frame, get_compare_history, get_portfolio_history

log = logging.getLogger(__name__)

BENCHMARK = '^GSPC'
BENCHMARK_NAME = 'S&P 500'
CHART_DAYS = 7               # calendar days shown in the weekly chart
_START_TOLERANCE = pd.Timedelta('5D')   # a '1y' download may start a few days late

_SECTOR_SHORT = {
    'Communication Services': 'Comm. Services',
    'Consumer Cyclical': 'Cons. Cyclical',
    'Consumer Defensive': 'Cons. Defensive',
    'Financial Services': 'Financials',
    'Basic Materials': 'Materials',
}


def short_sector(name: str | None) -> str:
    return _SECTOR_SHORT.get(name or '', name or '—')


# ── Sector & geography ───────────────────────────────────────────────────────

# ── Performance versus the S&P 500 ───────────────────────────────────────────

def _value_at(series: pd.Series, when: pd.Timestamp) -> float | None:
    """Last value on or before `when`; None if the series starts well after it."""
    if when < series.index[0] - _START_TOLERANCE:
        return None
    if when < series.index[0]:
        return float(series.iloc[0])
    return float(series.asof(when))


def window_return(series: pd.Series | None, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    if series is None or series.empty:
        return None
    first, last = _value_at(series, start), _value_at(series, end)
    if not first or last is None:
        return None
    return (last / first - 1) * 100


def period_returns(value: pd.Series, bench: pd.Series | None):
    """[(label, portfolio %, S&P 500 % | None)] for 1W/1M/3M/YTD/1Y ending at the last close.

    A period the history does not cover comes back as None for that side.
    """
    end = value.index[-1]
    starts = [
        ('1W', end - pd.Timedelta('7D')),
        ('1M', end - pd.DateOffset(months=1)),
        ('3M', end - pd.DateOffset(months=3)),
        ('YTD', pd.Timestamp(end.year - 1, 12, 31)),
        ('1Y', end - pd.DateOffset(years=1)),
    ]
    return [(label, window_return(value, start, end), window_return(bench, start, end))
            for label, start in starts]


def weekly_frame(value: pd.Series, bench: pd.Series | None, days: int = CHART_DAYS) -> pd.DataFrame:
    """Cumulative % return of the portfolio (and S&P 500) over the last `days`.

    Starts from the last close on or before the window so day one shows the
    first move; empty when fewer than two common dates exist.
    """
    start = value.index[-1] - pd.Timedelta(f'{days}D')
    before = value.index[value.index <= start]
    window = value.loc[before[-1]:] if len(before) else value
    others = {BENCHMARK_NAME: bench} if bench is not None else {}
    return compare_frame(window, others, 'pct')


def fetch_performance(positions: list[dict], base_ccy: str):
    """(portfolio value series, S&P 500 series | None, [(ticker, reason left out)]), or None.

    The portfolio series is the dashboard's backcast (today's holdings valued at
    dividend-adjusted historical closes), so it is a like-for-like comparison, not an account ledger.
    """
    slim = [{'ticker': p.get('ticker'), 'quantity': p.get('quantity'),
             'currency': p.get('currency') or '', 'multiplier': p.get('multiplier')}
            for p in positions]
    results = run_parallel({
        'portfolio': lambda: get_portfolio_history(slim, base_ccy, '1Y'),
        'bench': lambda: get_compare_history(BENCHMARK, '1Y'),
    })
    value, excluded = results.get('portfolio') or (None, [])
    if value is None or len(value) < 2:
        return None
    return value, results.get('bench'), excluded


# ── Chart ────────────────────────────────────────────────────────────────────

def weekly_chart(frame: pd.DataFrame, width: float, height: float,
                 series_colors: dict[str, str]):
    """reportlab Drawing: one line per column of `frame` (cumulative % return)."""
    from reportlab.graphics.charts.linecharts import HorizontalLineChart
    from reportlab.graphics.shapes import Drawing, Line, String
    from reportlab.graphics.widgets.markers import makeMarker
    from reportlab.lib import colors

    drawing = Drawing(width, height)
    chart = HorizontalLineChart()
    chart.x, chart.y = 38, 20
    chart.width, chart.height = width - 50, height - 44
    chart.data = [[float(v) for v in frame[col]] for col in frame.columns]

    everything = [v for row in chart.data for v in row] + [0.0]
    lo, hi = min(everything), max(everything)
    pad = (hi - lo) * 0.12 or 0.5
    chart.valueAxis.valueMin, chart.valueAxis.valueMax = lo - pad, hi + pad
    chart.valueAxis.labelTextFormat = '%.1f%%'
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.labels.fontName = 'Helvetica'
    chart.valueAxis.visibleGrid = True
    chart.valueAxis.gridStrokeColor = colors.HexColor('#e0e0e0')
    chart.valueAxis.gridStrokeWidth = 0.4
    chart.categoryAxis.categoryNames = [d.strftime('%a %d %b') for d in frame.index]
    chart.categoryAxis.labels.fontSize = 7
    chart.categoryAxis.labels.fontName = 'Helvetica'
    chart.categoryAxis.joinAxisMode = 'bottom'
    chart.joinedLines = 1

    legend_x = chart.x
    for i, col in enumerate(frame.columns):
        color = colors.HexColor(series_colors.get(col, '#888888'))
        chart.lines[i].strokeColor = color
        chart.lines[i].strokeWidth = 2.0 if col == 'Portfolio' else 1.5
        chart.lines[i].symbol = makeMarker('FilledCircle', size=3.5, fillColor=color, strokeColor=color)
        last = float(frame[col].iloc[-1])
        label = f"{col} {last:+.2f}%"
        drawing.add(Line(legend_x, height - 8, legend_x + 14, height - 8,
                         strokeColor=color, strokeWidth=2))
        drawing.add(String(legend_x + 18, height - 11, label, fontSize=8,
                           fontName='Helvetica-Bold', fillColor=colors.HexColor('#444444')))
        legend_x += 24 + 4.6 * len(label)
    drawing.add(chart)
    return drawing
