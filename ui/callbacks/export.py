"""Download adapter; document rendering lives in ui/reports/pdf.py."""

from dash import Input, Output, State, dcc, no_update

from backend.portfolio.analysis import fingerprint
from backend.providers.ibkr import is_demo_mode
from ui.reports.pdf import build_pdf


def register(app):
    @app.callback(
        Output('download-pdf', 'data'), Input('export-pdf-btn', 'n_clicks'),
        State('portfolio-data', 'data'), State('market-intel-data', 'data'),
        State('portfolio-analysis', 'data'),
        prevent_initial_call=True,
    )
    def export_pdf(_, data, intel, analysis=None):
        if not data or not data.get('positions'):
            return no_update
        if analysis and analysis.get('input_key') and analysis['input_key'] != fingerprint(data, analysis['period']):
            analysis = {'reason': 'Historical analysis loading for current positions'}
        data = {**data, 'analysis': analysis or {}}
        content, filename = build_pdf(data, intel, demo=is_demo_mode())
        return dcc.send_bytes(content, filename)
