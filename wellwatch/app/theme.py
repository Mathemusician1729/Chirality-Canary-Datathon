"""Light and dark themes, the alert-level palette and number formats used everywhere in the app."""

from __future__ import annotations

import math

LEVEL_COLORS = {3: "#c0392b", 2: "#e67e22", 1: "#d4ac0d", 0: "#7f8c8d"}   # critical, warning, watch, none
LEVEL_NAMES = {3: "Critical", 2: "Warning", 1: "Watch", 0: "None"}
ACCENT = "#1f6f8b"

PALETTES = {
    "light": {"bg": "#f5f6f8", "panel": "#ffffff", "text": "#1f2933", "muted": "#616e7c", "border": "#d9dde3",
              "nav": "#1f2933", "nav_text": "#e4e7eb", "nav_sel": "#1f6f8b", "plot_bg": "#ffffff", "plot_fg": "#1f2933"},
    "dark": {"bg": "#16191d", "panel": "#1f2329", "text": "#e4e7eb", "muted": "#9aa5b1", "border": "#323a43",
             "nav": "#0f1215", "nav_text": "#cbd2d9", "nav_sel": "#2b8fb3", "plot_bg": "#1f2329", "plot_fg": "#e4e7eb"},
}


def stylesheet(theme: str) -> str:
    p = PALETTES.get(theme, PALETTES["light"])
    return f"""
    QWidget {{ background: {p['bg']}; color: {p['text']}; font-family: 'Segoe UI'; font-size: 10pt; }}
    QFrame#panel, QGroupBox {{ background: {p['panel']}; border: 1px solid {p['border']}; border-radius: 6px; }}
    QGroupBox {{ margin-top: 14px; padding-top: 8px; font-weight: 600; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
    QListWidget#nav {{ background: {p['nav']}; color: {p['nav_text']}; border: none; font-size: 11pt; }}
    QListWidget#nav::item {{ padding: 12px 14px; }}
    QListWidget#nav::item:selected {{ background: {p['nav_sel']}; color: white; }}
    QLabel#title {{ font-size: 16pt; font-weight: 600; background: transparent; }}
    QLabel#muted {{ color: {p['muted']}; background: transparent; }}
    QLabel {{ background: transparent; }}
    QFrame#tile {{ background: {p['panel']}; border: 1px solid {p['border']}; border-radius: 8px; }}
    QLabel#tileValue {{ font-size: 18pt; font-weight: 600; }}
    QLabel#tileLabel {{ color: {p['muted']}; font-size: 9pt; }}
    QTableView {{ background: {p['panel']}; gridline-color: {p['border']}; border: 1px solid {p['border']};
                 selection-background-color: {ACCENT}; selection-color: white; }}
    QHeaderView::section {{ background: {p['bg']}; color: {p['muted']}; border: none;
                           border-bottom: 1px solid {p['border']}; padding: 4px 6px; font-weight: 600; }}
    QPushButton {{ background: {ACCENT}; color: white; border: none; border-radius: 4px; padding: 6px 12px; }}
    QPushButton:disabled {{ background: {p['border']}; color: {p['muted']}; }}
    QPushButton#secondary {{ background: {p['panel']}; color: {p['text']}; border: 1px solid {p['border']}; }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QPlainTextEdit, QTextEdit {{
        background: {p['panel']}; border: 1px solid {p['border']}; border-radius: 4px; padding: 3px 6px; }}
    QStatusBar {{ background: {p['panel']}; color: {p['muted']}; border-top: 1px solid {p['border']}; }}
    QTabWidget::pane {{ border: 1px solid {p['border']}; }}
    QTabBar::tab {{ background: {p['bg']}; padding: 6px 12px; }}
    QTabBar::tab:selected {{ background: {p['panel']}; font-weight: 600; }}
    """


def money(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    return f"C${v:,.0f}"


def pct(v, digits: int = 1) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    return f"{v:.{digits}%}"


def level_name(level) -> str:
    try:
        return LEVEL_NAMES.get(int(level), "None")
    except (TypeError, ValueError):
        return "None"
