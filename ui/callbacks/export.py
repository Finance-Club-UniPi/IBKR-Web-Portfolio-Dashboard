"""Download adapter; document rendering lives in ui/reports/pdf.py."""

from dash import Input, Output, State, dcc, no_update

from backend.providers.ibkr import is_demo_mode
from ui.reports.pdf import build_pdf


def register(app):
    @app.callback(
        Output('download-pdf', 'data'), Input('export-pdf-btn', 'n_clicks'),
        State('portfolio-data', 'data'), State('market-intel-data', 'data'),
        prevent_initial_call=True,
    )
    def export_pdf(_, data, intel):
        if not data or not data.get('positions'):
            return no_update
        content, filename = build_pdf(data, intel, demo=is_demo_mode())
        return dcc.send_bytes(content, filename)
