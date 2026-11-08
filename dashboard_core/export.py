"""PDF export of the current portfolio snapshot.

Single callback, isolated. Lazily imports reportlab inside the callback so
launching the app doesn't pay the import cost when nobody clicks Export.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime
from xml.sax.saxutils import escape

import pandas as pd
from dash import Input, Output, State, dcc, no_update

from dashboard_core.helpers import EURUSD_FALLBACK, ccy_symbol, to_base
from exposure import sector_geo_breakdown
from ibkr_client import is_demo_mode
from pdf_report import (
    BENCHMARK_NAME,
    fetch_performance,
    period_returns,
    short_sector,
    weekly_chart,
    weekly_frame,
)
from styles import (
    COLOR_BAD_DEEP,
    COLOR_BRAND,
    COLOR_GOOD_DEEP,
    COLOR_WARN_BG,
    COLOR_WARN_BORDER,
    COLOR_WARN_DEEP,
)

log = logging.getLogger(__name__)

# Reportlab needs actual hex values — CSS variables don't work with colors.HexColor().
_PDF_SURFACE      = '#f5f5f5'
_PDF_SURFACE_SOFT = '#fafafa'
_PDF_BORDER       = '#e0e0e0'


def register(app):
    @app.callback(
        Output('download-pdf', 'data'),
        Input('export-pdf-btn', 'n_clicks'),
        State('portfolio-data', 'data'),
        State('market-intel-data', 'data'),
        prevent_initial_call=True,
    )
    def export_pdf(_, data, intel):
        if not data or 'positions' not in data:
            return no_update
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            HRFlowable,
            KeepTogether,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )

        df       = pd.DataFrame(data['positions'])
        s        = data.get('summary', {})
        a        = data.get('account', {})
        rate     = a.get('eurusd_rate', EURUSD_FALLBACK)
        base_ccy = a.get('base_currency', 'EUR')
        sym      = ccy_symbol(base_ccy)
        values_base = a.get('portfolio_value_units') == 'base'
        sec_ccy  = 'USD' if base_ccy != 'USD' else 'EUR'
        sec_sym  = ccy_symbol(sec_ccy)

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                leftMargin=15*mm, rightMargin=15*mm,
                                topMargin=18*mm, bottomMargin=18*mm)
        styles = getSampleStyleSheet()
        story = []

        demo = is_demo_mode()

        # Every section spans the same usable width (A4 210mm − 2×15mm margins)
        # so tables line up edge-to-edge and the page reads symmetrically.
        CONTENT_W   = 180 * mm
        SECTION_GAP = 8 * mm
        note_style  = ParagraphStyle('note', parent=styles['Normal'], fontSize=7.5,
                                     leading=10, textColor=colors.HexColor('#888888'),
                                     spaceBefore=4)

        def section_header(text: str, into=None):
            """Uppercase label + thin full-width rule — one consistent style."""
            into = story if into is None else into
            into.append(Paragraph(text.upper(), ParagraphStyle(
                'section', parent=styles['Normal'], fontSize=10,
                fontName='Helvetica-Bold', textColor=colors.HexColor('#555555'),
                leading=12, spaceAfter=3)))
            into.append(HRFlowable(width=CONTENT_W, thickness=0.6,
                                   color=colors.HexColor(_PDF_BORDER),
                                   spaceAfter=6, spaceBefore=0))

        def pct_cell(v):
            return f"{v:+.2f}%" if v is not None and pd.notna(v) else '—'

        def pct_color(v):
            return colors.HexColor(COLOR_GOOD_DEEP if v >= 0 else COLOR_BAD_DEEP)

        # Title
        title_text = "Portfolio Snapshot — DEMO MODE" if demo else "Portfolio Snapshot"
        story.append(Paragraph(title_text, ParagraphStyle(
            'title', parent=styles['Heading1'], fontSize=18, spaceAfter=4)))
        story.append(Paragraph(
            f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            ParagraphStyle('sub', parent=styles['Normal'], fontSize=9,
                           textColor=colors.HexColor('#888888'),
                           spaceAfter=12 if demo else SECTION_GAP)))
        if demo:
            story.append(Paragraph(
                "Sample portfolio — not real trading data.",
                ParagraphStyle('demo', parent=styles['Normal'], fontSize=9,
                               textColor=colors.HexColor(COLOR_WARN_DEEP),
                               backColor=colors.HexColor(COLOR_WARN_BG),
                               borderColor=colors.HexColor(COLOR_WARN_BORDER),
                               borderWidth=0.5, borderPadding=6,
                               spaceAfter=SECTION_GAP)))

        # ── Summary ───────────────────────────────────────────────────────────
        section_header("Summary")
        total_val   = s.get('total_value', 0)
        unreal_pnl  = s.get('total_unrealized_pnl', 0)
        real_pnl    = s.get('total_realized_pnl', 0) or 0
        daily_pnl   = a.get('daily_pnl')
        if daily_pnl is None and values_base:
            daily_pnl = s.get('total_daily_pnl')
        def _base(v, verified=values_base):
            return f"{sym if verified else ''}{v:,.2f}"
        def _base_signed(v, verified=values_base):
            if v is None:
                return '—'
            return f"{sym if verified else ''}{v:+,.2f}"
        def _sec(v):
            if not values_base or base_ccy not in ('USD', 'EUR'):
                return '—'
            sec_val = v / rate if base_ccy == 'USD' else v * rate
            return f"{sec_sym}{sec_val:,.2f}"

        summary_data = [
            ['Metric', base_ccy if values_base else 'TWS units', sec_ccy if values_base else '—'],
            ['Total Value',    _base(total_val),         _sec(total_val)],
            ['Unrealized P&L', _base_signed(unreal_pnl), _sec(unreal_pnl)],
            ['Realized P&L',   _base_signed(real_pnl),   _sec(real_pnl)],
            ["Today's P&L",    _base_signed(daily_pnl, verified=True),
                              f"{sec_sym}{daily_pnl / rate if base_ccy == 'USD' else daily_pnl * rate:,.2f}"
                              if daily_pnl is not None and base_ccy in ('EUR', 'USD') and rate else '—'],
            ['Cash',           f"{sym}{a.get('cash_base', 0):,.2f}",
                               f"${a.get('cash_usd', 0) or 0:,.2f}"],
        ]
        t = Table(summary_data, colWidths=[100*mm, 40*mm, 40*mm])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(_PDF_SURFACE)),
            ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE',   (0, 0), (-1, -1), 9),
            ('ALIGN',      (1, 0), (-1, -1), 'RIGHT'),
            ('GRID',       (0, 0), (-1, -1), 0.25, colors.HexColor(_PDF_BORDER)),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor(_PDF_SURFACE_SOFT)]),
            ('TOPPADDING',  (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(t)

        # Income summary — one-liner showing projected dividends, yield, and
        # how many holdings actually pay. Hidden when no holdings pay anything.
        div_data   = data.get('div_data') or {}
        qty_by_tk  = dict(zip(df['ticker'], df['quantity'], strict=False))
        annual_usd = sum(
            qty_by_tk.get(tk, 0) * float(d.get('next_12m') or 0)
            for tk, d in div_data.items()
        )
        if annual_usd > 0 and total_val > 0:
            annual_base = to_base(annual_usd, rate, base_ccy)
            yield_pct   = (annual_base / total_val) * 100
            payers      = sum(1 for d in div_data.values() if (d.get('next_12m') or 0) > 0)
            total_hold  = len(df)
            story.append(Spacer(1, 3*mm))
            story.append(Paragraph(
                f"Projected 12-month dividends: <b>{sym}{annual_base:,.0f}</b> &nbsp;·&nbsp; "
                f"Portfolio yield: <b>{yield_pct:.2f}%</b> &nbsp;·&nbsp; "
                f"{payers} of {total_hold} holdings pay",
                ParagraphStyle('income', parent=styles['Normal'], fontSize=9,
                               textColor=colors.HexColor('#444444'),
                               backColor=colors.HexColor(_PDF_SURFACE_SOFT),
                               borderColor=colors.HexColor(_PDF_BORDER),
                               borderWidth=0.5, borderPadding=6),
            ))
        story.append(Spacer(1, SECTION_GAP))

        # ── Performance ───────────────────────────────────────────────────────
        perf = []
        section_header("Performance", perf)
        total_pct = s.get('total_pnl_pct')
        cost_basis = total_val - unreal_pnl
        if total_pct is None and cost_basis:
            total_pct = unreal_pnl / cost_basis * 100
        prior_val = total_val - daily_pnl if daily_pnl is not None else None
        today_pct = daily_pnl / prior_val * 100 if values_base and prior_val else None
        best   = s.get('best_performer', '—')
        worst  = s.get('worst_performer', '—')
        best_row  = df[df['ticker'] == best].iloc[0]  if best  != '—' and best  in df['ticker'].values else None
        worst_row = df[df['ticker'] == worst].iloc[0] if worst != '—' and worst in df['ticker'].values else None
        best_str  = f"{best} ({best_row['pnl_pct']:+.2f}%)"   if best_row  is not None else best
        worst_str = f"{worst} ({worst_row['pnl_pct']:+.2f}%)" if worst_row is not None else worst
        perf_data = [
            ['Total Return', "Today", 'Best Performer', 'Worst Performer'],
            [pct_cell(total_pct), pct_cell(today_pct), best_str, worst_str],
        ]
        pt = Table(perf_data, colWidths=[45*mm] * 4)
        pt_style = [
            ('BACKGROUND',    (0, 0), (-1, 0), colors.HexColor(_PDF_SURFACE)),
            ('FONTNAME',      (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE',      (0, 0), (-1, -1), 9),
            ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
            ('GRID',          (0, 0), (-1, -1), 0.25, colors.HexColor(_PDF_BORDER)),
            ('TEXTCOLOR',     (2, 1), (2, 1), colors.HexColor(COLOR_GOOD_DEEP)),  # best = green
            ('TEXTCOLOR',     (3, 1), (3, 1), colors.HexColor(COLOR_BAD_DEEP)),  # worst = red
            ('FONTNAME',      (0, 1), (-1, 1), 'Helvetica-Bold'),
            ('TOPPADDING',    (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]
        for c, v in ((0, total_pct), (1, today_pct)):
            if v is not None:
                pt_style.append(('TEXTCOLOR', (c, 1), (c, 1), pct_color(v)))
        pt.setStyle(TableStyle(pt_style))
        perf.append(pt)

        # Portfolio vs S&P 500 — period returns plus a chart of the last week.
        # Network/history failures only drop this part; the rest of the PDF stays.
        try:
            hist = fetch_performance(data['positions'], base_ccy)
        except Exception as e:
            log.warning('PDF performance history unavailable: %s', e)
            hist = None
        chart_block = []
        if hist is None:
            perf.append(Paragraph("Price history unavailable — comparison with the "
                                  f"{escape(BENCHMARK_NAME)} omitted.", note_style))
        else:
            value, bench, excluded = hist
            rets = period_returns(value, bench)
            rows = [['Return'] + [r[0] for r in rets],
                    ['Portfolio'] + [pct_cell(r[1]) for r in rets]]
            if bench is not None:
                rows.append([BENCHMARK_NAME] + [pct_cell(r[2]) for r in rets])
                rows.append(['Difference (pp)'] + [
                    f"{r[1] - r[2]:+.2f}" if r[1] is not None and r[2] is not None else '—'
                    for r in rets])
            rt = Table(rows, colWidths=[45*mm] + [27*mm] * 5)
            rt_style = [
                ('BACKGROUND',    (0, 0), (-1, 0), colors.HexColor(_PDF_SURFACE)),
                ('FONTNAME',      (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTNAME',      (0, 1), (0, -1), 'Helvetica-Bold'),
                ('FONTSIZE',      (0, 0), (-1, -1), 9),
                ('ALIGN',         (1, 0), (-1, -1), 'RIGHT'),
                ('GRID',          (0, 0), (-1, -1), 0.25, colors.HexColor(_PDF_BORDER)),
                ('TOPPADDING',    (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ]
            for r, idx in ((1, 1), (2, 2)):
                if r >= len(rows):
                    break
                for c, ret in enumerate(rets, start=1):
                    if ret[idx] is not None:
                        rt_style.append(('TEXTCOLOR', (c, r), (c, r), pct_color(ret[idx])))
            rt.setStyle(TableStyle(rt_style))
            perf.append(Spacer(1, 3*mm))
            perf.append(rt)
            note = (f"Portfolio = today's holdings valued at historical closes in {base_ccy} "
                    f"(cash and past trades excluded; prices include dividend adjustments). {BENCHMARK_NAME} is the price "
                    "index in USD.")
            if excluded:
                note += " Not included (no usable price history): " + ', '.join(
                    f"{t} ({r})" for t, r in excluded) + '.'
            perf.append(Paragraph(escape(note), note_style))

            frame = weekly_frame(value, bench)
            if len(frame) >= 2:
                chart_block.append(Spacer(1, 4*mm))
                chart_block.append(Paragraph(
                    f"Last 7 days — cumulative return, trading days from "
                    f"{frame.index[0]:%d %b} to {frame.index[-1]:%d %b %Y}",
                    ParagraphStyle('chartcap', parent=styles['Normal'], fontSize=9,
                                   fontName='Helvetica-Bold', textColor=colors.HexColor('#555555'),
                                   spaceAfter=2)))
                chart_block.append(weekly_chart(
                    frame, CONTENT_W, 62*mm,
                    {'Portfolio': COLOR_BRAND, BENCHMARK_NAME: '#f97316'}))
        story.append(KeepTogether(perf))
        if chart_block:
            story.append(KeepTogether(chart_block))
        story.append(Spacer(1, SECTION_GAP))

        # ── Sector & geography ────────────────────────────────────────────────
        sg = (intel or {}).get('sector_geo') or {}
        if sg:
            sectors, countries = sector_geo_breakdown(data['positions'], sg)
            geo = []
            section_header("Sector & Geography", geo)
            body_style = ParagraphStyle('sgcell', parent=styles['Normal'], fontSize=7.5, leading=9,
                                        textColor=colors.HexColor('#444444'))
            top_sectors, rest = sectors[:8], sectors[8:]
            sec_rows = [['Sector', 'Weight', 'Holdings']]
            for name, pct, tickers in top_sectors:
                sec_rows.append([name, f"{pct:.1f}%", Paragraph(escape(', '.join(tickers)), body_style)])
            if rest:
                sec_rows.append([f"Other ({len(rest)})", f"{sum(r[1] for r in rest):.1f}%", ''])
            sec_t = Table(sec_rows, colWidths=[36*mm, 16*mm, 58*mm])
            cty_rows = [['Country', 'Weight']] + [[n, f"{p:.1f}%"] for n, p in countries[:8]]
            if len(countries) > 8:
                cty_rows.append(['Other', f"{sum(p for _, p in countries[8:]):.1f}%"])
            cty_t = Table(cty_rows, colWidths=[46*mm, 18*mm])
            for tbl in (sec_t, cty_t):
                tbl.setStyle(TableStyle([
                    ('BACKGROUND',    (0, 0), (-1, 0), colors.HexColor(_PDF_SURFACE)),
                    ('FONTNAME',      (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE',      (0, 0), (-1, -1), 8),
                    ('ALIGN',         (1, 0), (1, -1), 'RIGHT'),
                    ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
                    ('GRID',          (0, 0), (-1, -1), 0.25, colors.HexColor(_PDF_BORDER)),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1),
                     [colors.white, colors.HexColor(_PDF_SURFACE_SOFT)]),
                    ('TOPPADDING',    (0, 0), (-1, -1), 3),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ]))
            side_by_side = Table([[sec_t, '', cty_t]], colWidths=[110*mm, 6*mm, 64*mm])
            side_by_side.setStyle(TableStyle([
                ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING',  (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING',   (0, 0), (-1, -1), 0),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ]))
            geo.append(side_by_side)
            geo.append(Paragraph("ETFs are split across their underlying sectors; "
                                 "country is the instrument's domicile.", note_style))
            story.append(KeepTogether(geo))
            story.append(Spacer(1, SECTION_GAP))

        # ── Holdings ──────────────────────────────────────────────────────────
        section_header("Holdings")
        pnl_sym = sym if values_base else ''
        hold_data = [['Ticker', 'Sector', 'Qty', 'Avg Cost', 'Price', 'Day %',
                      f'Value ({base_ccy})' if values_base else 'Value (TWS)',
                      'P&L', 'P&L %', '52w %', 'Weight']]
        for _, row in df.iterrows():
            day_pct = row.get('daily_change_pct')
            day_str = f"{day_pct:+.2f}%" if pd.notna(day_pct) and day_pct is not None else '—'
            info = sg.get(row['ticker']) or {}
            sector = 'ETF' if info.get('is_etf') else short_sector(info.get('sector'))
            rng = row.get('range_52w_pct')
            hold_data.append([
                row['ticker'],
                sector,
                f"{row['quantity']:,.6f}".rstrip('0').rstrip('.'),
                f"{ccy_symbol(row.get('currency') or '') if values_base else ''}{row['avg_cost']:,.2f}",
                (f"{ccy_symbol(row.get('currency') or '')}{row['fx_native_price']:,.2f}"
                 if pd.notna(row.get('fx_native_price')) else f"~{row['current_price']:,.2f}"),
                day_str,
                f"{sym if values_base else ''}{row['market_value']:,.2f}",
                f"{pnl_sym}{row['unrealized_pnl']:+,.2f}",
                f"{row['pnl_pct']:+.2f}%",
                f"{rng:.0f}%" if rng is not None and pd.notna(rng) else '—',
                f"{row['allocation_pct']:.1f}%",
            ])
        # Widths sum to 180mm — same span as every other section.
        ht = Table(hold_data, repeatRows=1,
                   colWidths=[16*mm, 25*mm, 11*mm, 18*mm, 18*mm, 13*mm, 22*mm, 19*mm, 14*mm, 12*mm, 12*mm])
        # colour positive/negative day %, P&L and P&L % cells per row
        ht_style = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(_PDF_SURFACE)),
            ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE',   (0, 0), (-1, -1), 7.5),
            ('ALIGN',      (2, 0), (-1, -1), 'RIGHT'),
            ('ALIGN',      (0, 0), (1, -1), 'LEFT'),
            ('LEFTPADDING', (0, 0), (-1, -1), 3),
            ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ('GRID',       (0, 0), (-1, -1), 0.25, colors.HexColor(_PDF_BORDER)),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor(_PDF_SURFACE_SOFT)]),
            ('TOPPADDING',  (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]
        for i, row in enumerate(df.itertuples(), start=1):
            day_pct = getattr(row, 'daily_change_pct', None)
            if day_pct is not None and pd.notna(day_pct):
                ht_style.append(('TEXTCOLOR', (5, i), (5, i), pct_color(day_pct)))
            pnl = getattr(row, 'pnl_pct', 0) or 0
            ht_style.append(('TEXTCOLOR', (7, i), (8, i), pct_color(pnl)))
        ht.setStyle(TableStyle(ht_style))
        story.append(ht)
        story.append(Paragraph("52w % = position of the price within its 52-week range "
                               "(0% = low, 100% = high).", note_style))

        doc.build(story)
        buf.seek(0)
        prefix = "portfolio_demo" if demo else "portfolio"
        filename = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        return dcc.send_bytes(buf.read(), filename)
