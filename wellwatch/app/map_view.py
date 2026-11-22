"""Well map on a real basemap: Leaflet with Esri light / dark grey canvas tiles, shown in Qt WebEngine.

Each scored well is a circle: colour = alert level, size = expected loss; click it for a popup with the
risk, expected loss and the main reason. Only map tiles and the Leaflet library (pinned version, checked
with a subresource-integrity hash) are fetched from the internet; well data never leaves the app.

Falls back to a plain longitude/latitude scatter plot when offline mode is on, when the map page cannot
load (no internet), or in offscreen tests.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QStackedLayout, QWidget

try:  # must be imported before the QApplication exists; main_window imports this module early
    from PySide6.QtWebEngineWidgets import QWebEngineView
except ImportError:  # pragma: no cover
    QWebEngineView = None

from .theme import LEVEL_COLORS, PALETTES, level_name, money

LEAFLET = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4"
LEAFLET_JS_SRI = "sha512-puJW3E/qXDqYp9IfhAI54BJEaWIfloJ7JWs7OeD5i6ruC9JZL1gERT1wjtwXFlh7CjE7ZJ+/vcRZRkIYIb6p4g=="
LEAFLET_CSS_SRI = "sha512-h9FcoyWjHcOcmEVkxOfTLnmZFWIH0iZhZT1H2TbOq55xssQGEJHEaIm+PgoUaZbRvQTNTluNOEfb1ZRy6D3BOw=="
NO_ALERT_COLOR = "#7f8c8d"

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="__LEAFLET__/leaflet.min.css" integrity="__CSS_SRI__" crossorigin="anonymous" referrerpolicy="no-referrer">
<script src="__LEAFLET__/leaflet.min.js" integrity="__JS_SRI__" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<style>
html, body, #map { height: 100%; margin: 0; background: __BG__; font-family: 'Segoe UI', sans-serif; }
.legend { background: rgba(255,255,255,.92); padding: 6px 9px; border-radius: 4px; font-size: 12px;
          line-height: 19px; color: #222; box-shadow: 0 1px 4px rgba(0,0,0,.25); }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
.leaflet-popup-content { font-size: 12px; line-height: 17px; }
</style></head><body><div id="map"></div><script>
var ok = (typeof L !== 'undefined'), map = null, layer = null, fitted = false;
if (ok) {
  map = L.map('map', {preferCanvas: true}).setView([52.6, -110], 6);
  L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/__STYLE__/MapServer/tile/{z}/{y}/{x}', {
    maxZoom: 16, attribution: 'Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors'
  }).addTo(map);
  L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/__LABELS__/MapServer/tile/{z}/{y}/{x}', {
    maxZoom: 16, pane: 'shadowPane'
  }).addTo(map);
  layer = L.layerGroup().addTo(map);
  var legend = L.control({position: 'bottomleft'});
  legend.onAdd = function () { var d = L.DomUtil.create('div', 'legend'); d.innerHTML = __LEGEND__; return d; };
  legend.addTo(map);
}
function esc(s) { return String(s).replace(/[&<>"]/g, function (c) {
  return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
function setData(rows) {
  if (!ok) { return false; }
  layer.clearLayers();
  var pts = [];
  rows.forEach(function (r) {
    var m = L.circleMarker([r.lat, r.lon], {radius: r.size, color: r.level >= 2 ? '#222' : r.color,
      weight: r.level >= 2 ? 1.2 : 0.5, fillColor: r.color, fillOpacity: r.opacity});
    m.bindTooltip(esc(r.uwi) + ' &middot; ' + esc(r.label));
    m.bindPopup('<b>' + esc(r.uwi) + '</b><br>' + esc(r.field) + '<br>' + esc(r.label) + ' &middot; 7-day risk ' +
                esc(r.risk) + '<br>Expected loss ' + esc(r.loss) + '<br><i>' + esc(r.driver) + '</i>');
    m.addTo(layer);
    pts.push([r.lat, r.lon]);
  });
  if (!fitted && pts.length) { map.fitBounds(pts, {padding: [25, 25]}); fitted = true; }
  return true;
}
</script></body></html>"""


def _legend_html() -> str:
    rows = [(3, "Critical"), (2, "Warning"), (1, "Watch"), (0, "No alert")]
    items = "".join(f"<i style='background:{LEVEL_COLORS.get(lvl, NO_ALERT_COLOR) if lvl else NO_ALERT_COLOR}'></i>"
                    f"{name}<br>" for lvl, name in rows)
    return json.dumps(items + "<span style='color:#666'>size = expected loss</span>")


def _color(level: int) -> str:
    return LEVEL_COLORS.get(int(level), NO_ALERT_COLOR) if level else NO_ALERT_COLOR


def map_rows(m: pd.DataFrame) -> list[dict]:
    """Rows for the map page; higher alert levels last so they draw on top."""
    if m is None or m.empty:
        return []
    m = m.dropna(subset=["lat", "lon"]).sort_values(["level", "expected_loss"])
    el = m["expected_loss"].clip(lower=0).to_numpy(float)
    size = 3 + 11 * np.sqrt(el / max(el.max(), 1.0))
    out = []
    for r, s in zip(m.itertuples(index=False), size):
        lvl = int(r.level)
        out.append({"lat": float(r.lat), "lon": float(r.lon), "uwi": str(r.uwi), "field": str(r.field),
                    "level": lvl, "label": level_name(lvl) if lvl else "No alert", "color": _color(lvl),
                    "opacity": 0.95 if lvl >= 2 else (0.8 if lvl == 1 else 0.35), "size": round(float(s), 1),
                    "risk": f"{float(r.risk):.1%}", "loss": money(float(r.expected_loss)),
                    "driver": "" if pd.isna(r.driver) else str(r.driver)})
    return out


class WellMap(QWidget):
    """Basemap with well circles, or the scatter-plot fallback."""

    def __init__(self, theme: str = "light", use_web: bool = True):
        super().__init__()
        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._meta: dict = {}
        self._rows: list[dict] = []
        self._ready = False
        self._theme = theme

        # fallback: plain scatter plot of longitude / latitude
        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Longitude")
        self.plot.setLabel("left", "Latitude")
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.scatter = pg.ScatterPlotItem(hoverable=True, hoverPen=pg.mkPen("k", width=2), tip=self._tip)
        self.plot.addItem(self.scatter)
        self._stack.addWidget(self.plot)

        self.web = None
        if use_web and QWebEngineView is not None and QGuiApplication.platformName() != "offscreen":
            self.web = QWebEngineView()
            self.web.loadFinished.connect(self._loaded)
            self._stack.addWidget(self.web)
            self._stack.setCurrentWidget(self.web)
            self._load_page()

    @property
    def using_basemap(self) -> bool:
        return self.web is not None and self._stack.currentWidget() is self.web

    # ------------------------------------------------------------------ page
    def _load_page(self):
        self._ready = False
        dark = self._theme == "dark"
        html = (PAGE.replace("__LEAFLET__", LEAFLET).replace("__JS_SRI__", LEAFLET_JS_SRI)
                .replace("__CSS_SRI__", LEAFLET_CSS_SRI).replace("__STYLE__", "World_Dark_Gray_Base" if dark else "World_Light_Gray_Base")
                .replace("__LABELS__", "World_Dark_Gray_Reference" if dark else "World_Light_Gray_Reference")
                .replace("__BG__", "#1b1f24" if dark else "#e9eef2").replace("__LEGEND__", _legend_html()))
        self.web.setHtml(html, QUrl("https://wellwatch.local/"))

    def _loaded(self, ok: bool):
        if not ok:
            self._fallback()
            return
        self.web.page().runJavaScript("typeof L !== 'undefined'", 0, self._leaflet_checked)

    def _leaflet_checked(self, has_leaflet):
        if not has_leaflet:  # no internet / CDN blocked
            self._fallback()
            return
        self._ready = True
        self._push()

    def _fallback(self):
        self._ready = False
        self._stack.setCurrentWidget(self.plot)

    def _push(self):
        if self.web is not None and self._ready:
            self.web.page().runJavaScript(f"setData({json.dumps(self._rows)});")

    # ------------------------------------------------------------------ API
    def set_theme(self, theme: str):
        self.plot.setBackground(PALETTES[theme]["plot_bg"])
        if theme != self._theme:
            self._theme = theme
            if self.using_basemap:
                self._load_page()  # dark / light tiles; data is pushed again when it loads

    def set_wells(self, m: pd.DataFrame):
        self._rows = map_rows(m)
        self._push()
        self._meta = {i: r for i, r in enumerate(self._rows)}
        if self._rows:
            brushes = [pg.mkBrush(r["color"] + ("ff" if r["level"] >= 2 else ("cc" if r["level"] == 1 else "55")))
                       for r in self._rows]
            self.scatter.setData(x=[r["lon"] for r in self._rows], y=[r["lat"] for r in self._rows],
                                 size=[2 * r["size"] for r in self._rows], brush=brushes, pen=None,
                                 data=list(range(len(self._rows))))
        else:
            self.scatter.clear()

    def _tip(self, x, y, data):
        r = self._meta.get(data)
        if r is None:
            return ""
        return f"{r['uwi']} ({r['field']})\n{r['label']}, risk {r['risk']}, expected loss {r['loss']}\n{r['driver']}"
