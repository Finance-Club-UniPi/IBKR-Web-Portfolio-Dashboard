"""Dash application assembly: layout, callbacks, health endpoint, and assets."""

from __future__ import annotations

import dash

import ui.callbacks.coach as _coach_ui_mod
import ui.callbacks.detail as _detail_mod
import ui.callbacks.export as _export_mod
import ui.callbacks.intel as _intel_mod
import ui.callbacks.performance as _performance_mod
import ui.callbacks.portfolio as _data_callbacks_mod
import ui.callbacks.risk as _risk_mod
import ui.callbacks.summary as _summary_mod
import ui.callbacks.valuation as _valuation_mod
import ui.health as health
import ui.layout as _layout_mod
from config import cfg

app = dash.Dash(__name__, suppress_callback_exceptions=True)

_REFRESH_MS = cfg['dashboard']['refresh_interval_seconds'] * 1000
app.layout = _layout_mod.build_layout(_REFRESH_MS)

_data_callbacks_mod.register(app)
_summary_mod.register(app)
_performance_mod.register(app)
_risk_mod.register(app)
_export_mod.register(app)
_detail_mod.register(app)
_intel_mod.register(app)
_valuation_mod.register(app)
_coach_ui_mod.register(app)

health.register(app.server)
