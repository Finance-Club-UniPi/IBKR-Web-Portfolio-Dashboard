"""ReportLab chart rendering for PDF reports."""

import pandas as pd


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
