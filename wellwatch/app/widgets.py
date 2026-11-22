"""Shared widgets: KPI tile, sortable pandas table, level badge helpers."""

from __future__ import annotations

import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QHeaderView, QLabel, QTableView, QVBoxLayout

from .theme import LEVEL_COLORS, level_name, money, pct


class Tile(QFrame):
    def __init__(self, label: str, value: str = "-", color: str | None = None):
        super().__init__()
        self.setObjectName("tile")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        self.value = QLabel(value)
        self.value.setObjectName("tileValue")
        self.label = QLabel(label)
        self.label.setObjectName("tileLabel")
        self.label.setWordWrap(True)
        lay.addWidget(self.value)
        lay.addWidget(self.label)
        self.set(value, color)

    def set(self, value: str, color: str | None = None, label: str | None = None):
        self.value.setText(value)
        self.value.setStyleSheet(f"color: {color};" if color else "")
        if label:
            self.label.setText(label)


# column name -> formatter
FORMATS = {
    "risk": pct, "last_risk": pct, "first_risk": pct, "catch_rate": pct, "warning_precision": pct,
    "alert_precision": pct, "reduction_pct": pct,
    "expected_loss": money, "expected_loss_before": money, "expected_loss_after": money, "visit_cost": money,
    "value_cad": money, "value_caught_cad": money, "loss_no_action": money, "loss_with_plan": money,
    "plan_cost": money, "value_all_failures_cad": money,
    "level": level_name, "max_level": level_name,
}
LEVEL_COLUMNS = {"level", "max_level"}


class PandasModel(QAbstractTableModel):
    def __init__(self, df: pd.DataFrame | None = None, headers: dict | None = None):
        super().__init__()
        self.headers = headers or {}
        self.df = pd.DataFrame() if df is None else df.reset_index(drop=True)

    def set_df(self, df: pd.DataFrame):
        self.beginResetModel()
        self.df = df.reset_index(drop=True)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.df)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.df.columns)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        col = self.df.columns[index.column()]
        v = self.df.iat[index.row(), index.column()]
        if role == Qt.DisplayRole:
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return ""
            if col in FORMATS:
                try:
                    return FORMATS[col](v)
                except (TypeError, ValueError):
                    return str(v)
            if isinstance(v, float):
                return f"{v:,.2f}"
            if isinstance(v, pd.Timestamp):
                return v.strftime("%Y-%m-%d")
            return str(v)
        if role == Qt.UserRole:  # raw value for sorting
            return v
        if role == Qt.ForegroundRole and col in LEVEL_COLUMNS:
            try:
                return QColor(LEVEL_COLORS[int(v)])
            except (TypeError, ValueError, KeyError):
                return None
        if role == Qt.ToolTipRole and isinstance(v, str) and len(v) > 40:
            return v
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            col = self.df.columns[section]
            return self.headers.get(col, col.replace("_", " ").capitalize())
        return None

    def row(self, i: int) -> dict:
        return self.df.iloc[i].to_dict()


class _Proxy(QSortFilterProxyModel):
    def lessThan(self, left, right):
        a, b = left.data(Qt.UserRole), right.data(Qt.UserRole)
        try:
            if pd.isna(a):
                return True
            if pd.isna(b):
                return False
            return a < b
        except TypeError:
            return str(a) < str(b)


class DataTable(QTableView):
    """Sortable table for a DataFrame. row_at(proxy_index) returns the underlying row as a dict."""

    def __init__(self, headers: dict | None = None, stretch_last: bool = True):
        super().__init__()
        self.model_ = PandasModel(headers=headers)
        self.proxy = _Proxy()
        self.proxy.setSourceModel(self.model_)
        self.proxy.setSortRole(Qt.UserRole)
        self.setModel(self.proxy)
        self.setSortingEnabled(True)
        self.setSelectionBehavior(QTableView.SelectRows)
        self.setSelectionMode(QTableView.SingleSelection)
        self.setAlternatingRowColors(False)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setStretchLastSection(stretch_last)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.setWordWrap(False)

    def set_df(self, df: pd.DataFrame):
        self.model_.set_df(df)
        self.resizeColumnsToContents()
        for i in range(self.model_.columnCount()):
            self.setColumnWidth(i, min(self.columnWidth(i), 380))

    def selected_row(self) -> dict | None:
        idx = self.selectionModel().selectedRows()
        if not idx:
            return None
        return self.model_.row(self.proxy.mapToSource(idx[0]).row())

    def row_at(self, proxy_index) -> dict:
        return self.model_.row(self.proxy.mapToSource(proxy_index).row())


def title(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("title")
    return lab


def muted(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("muted")
    lab.setWordWrap(True)
    return lab
