"""
Centralized Design System (Tokens, QSS Theme, and Reusable Custom Controls)
Colors: bg #0F1626, surface #162036, border #243350, text #E6ECF8, muted #9AA8C4,
accent #2D8CFF, success #22C55E, warning #F59E0B, danger #EF4444.
"""

from typing import List, Tuple, Optional
from PySide6.QtWidgets import (
    QCheckBox, QWidget, QHBoxLayout, QPushButton, QButtonGroup, QDialog, QApplication
)
from PySide6.QtCore import Qt, Signal, QRectF, QRect
from PySide6.QtGui import QPainter, QColor, QGuiApplication


TOKENS = {
    "bg": "#0F1626",
    "surface": "#162036",
    "surface_alt": "#1D2A44",
    "border": "#243350",
    "text": "#E6ECF8",
    "muted": "#9AA8C4",
    "accent": "#2D8CFF",
    "accent_hover": "#1B76E5",
    "success": "#22C55E",
    "warning": "#F59E0B",
    "danger": "#EF4444",
    "radius": 8,
    "font_size": 14,
}


THEME_QSS = """
* {
    font-family: 'Noto Sans Khmer', 'Kantumruy Pro', 'Khmer OS Siemreap', 'Leelawadee UI', 'Segoe UI', sans-serif;
    font-size: 9.75pt;
    color: #E6ECF8;
}

QMainWindow, QDialog, QMessageBox {
    background-color: #0F1626;
    color: #E6ECF8;
}

QWidget#centralWidget, QWidget#scrollBody {
    background-color: #0F1626;
}

/* Menu Bar & Menus */
QMenuBar {
    background-color: #162036;
    color: #E6ECF8;
    border-bottom: 1px solid #243350;
    padding: 2px 6px;
}
QMenuBar::item {
    background: transparent;
    padding: 5px 12px;
    border-radius: 6px;
}
QMenuBar::item:selected {
    background-color: #243350;
}
QMenu {
    background-color: #162036;
    color: #E6ECF8;
    border: 1px solid #243350;
    border-radius: 8px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 20px;
    border-radius: 4px;
}
QMenu::item:selected {
    background-color: #2D8CFF;
    color: #FFFFFF;
}

/* Cards & GroupBoxes */
QGroupBox {
    background-color: #162036;
    border: 1px solid #243350;
    border-radius: 8px;
    margin-top: 14px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
    color: #9AA8C4;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #E6ECF8;
    background-color: #0F1626;
    border-radius: 4px;
}

QFrame.card {
    background-color: #162036;
    border: 1px solid #243350;
    border-radius: 8px;
}

/* Buttons */
QPushButton {
    background-color: #1D2A44;
    color: #E6ECF8;
    border: 1px solid #243350;
    border-radius: 8px;
    padding: 6px 12px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #243350;
    border-color: #2D8CFF;
}
QPushButton:pressed {
    background-color: #0F1626;
}
QPushButton:disabled {
    background-color: #162036;
    color: #5C6B8A;
    border-color: #1E293B;
}
QPushButton:focus {
    border-color: #2D8CFF;
}

QPushButton[variant="primary"] {
    background-color: #2D8CFF;
    color: #FFFFFF;
    border: 1px solid #2D8CFF;
    font-weight: 700;
}
QPushButton[variant="primary"]:hover {
    background-color: #1B76E5;
}

QPushButton[variant="success"] {
    background-color: #16A34A;
    color: #FFFFFF;
    border: 1px solid #22C55E;
    font-weight: 700;
}
QPushButton[variant="success"]:hover {
    background-color: #15803D;
}

QPushButton[variant="danger"] {
    background-color: rgba(239, 68, 68, 0.14);
    color: #FCA5A5;
    border: 1px solid rgba(239, 68, 68, 0.45);
}
QPushButton[variant="danger"]:hover {
    background-color: rgba(239, 68, 68, 0.28);
    border-color: #EF4444;
    color: #FFFFFF;
}

/* Inputs, Combos, Spins */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #0F1626;
    color: #E6ECF8;
    border: 1px solid #243350;
    border-radius: 8px;
    padding: 5px 10px;
}
QComboBox {
    padding: 4px 10px;
}
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: #2D8CFF;
}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
    background-color: #162036;
    color: #5C6B8A;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background-color: #162036;
    color: #E6ECF8;
    border: 1px solid #243350;
    selection-background-color: #2D8CFF;
    selection-color: #FFFFFF;
}

/* Checkboxes */
QCheckBox {
    spacing: 8px;
    color: #E6ECF8;
}
QCheckBox::indicator {
    width: 17px;
    height: 17px;
    border-radius: 4px;
    border: 1px solid #3B4F75;
    background-color: #0F1626;
}
QCheckBox::indicator:checked {
    background-color: #2D8CFF;
    border-color: #2D8CFF;
}
QCheckBox::indicator:hover {
    border-color: #2D8CFF;
}

/* Radio Buttons */
QRadioButton {
    spacing: 8px;
    color: #E6ECF8;
}
QRadioButton::indicator {
    width: 16px;
    height: 16px;
    border-radius: 8px;
    border: 1px solid #3B4F75;
    background-color: #0F1626;
}
QRadioButton::indicator:checked {
    background-color: #2D8CFF;
    border: 3px solid #162036;
}

/* Sliders */
QSlider::groove:horizontal {
    height: 6px;
    background: #243350;
    border-radius: 3px;
}
QSlider::sub-page:horizontal {
    background: #2D8CFF;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #E6ECF8;
    border: 2px solid #2D8CFF;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 8px;
}
QSlider::handle:horizontal:hover {
    background: #2D8CFF;
}
QSlider::handle:horizontal:disabled {
    background: #5C6B8A;
    border-color: #334155;
}

/* Tables & Lists */
QTableWidget {
    background-color: #0F1626;
    alternate-background-color: #162036;
    gridline-color: #243350;
    border: 1px solid #243350;
    border-radius: 8px;
    selection-background-color: #1D4ED8;
    selection-color: #FFFFFF;
}
QHeaderView::section {
    background-color: #162036;
    color: #9AA8C4;
    font-weight: 600;
    padding: 6px;
    border: none;
    border-bottom: 1px solid #243350;
    border-right: 1px solid #243350;
}
QListWidget {
    background-color: #162036;
    border: 1px solid #243350;
    border-radius: 8px;
    outline: none;
}
QListWidget::item {
    padding: 8px 12px;
    border-radius: 6px;
    margin: 2px 4px;
    color: #9AA8C4;
}
QListWidget::item:selected {
    background-color: #2D8CFF;
    color: #FFFFFF;
    font-weight: 600;
}
QListWidget::item:hover:!selected {
    background-color: #1D2A44;
    color: #E6ECF8;
}

/* ScrollAreas & Scrollbars */
QScrollArea {
    background-color: #0F1626;
    border: none;
}
QScrollBar:vertical {
    background: #0F1626;
    width: 8px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: #243350;
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover {
    background: #2D8CFF;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

/* Tabs */
QTabWidget::pane {
    border: 1px solid #243350;
    border-radius: 8px;
    background-color: #162036;
}
QTabBar::tab {
    background-color: #0F1626;
    color: #9AA8C4;
    padding: 7px 14px;
    border: 1px solid #243350;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    margin-right: 3px;
}
QTabBar::tab:selected {
    background-color: #162036;
    color: #E6ECF8;
    font-weight: 600;
    border-top: 2px solid #2D8CFF;
}

/* Status Bar & Progress Bar */
QStatusBar {
    background-color: #162036;
    border-top: 1px solid #243350;
    color: #9AA8C4;
    padding: 2px 8px;
}
QProgressBar {
    background-color: #0F1626;
    border: 1px solid #243350;
    border-radius: 6px;
    text-align: center;
    color: #E6ECF8;
    font-size: 8.5pt;
    min-height: 18px;
}
QProgressBar::chunk {
    background-color: #2D8CFF;
    border-radius: 5px;
}

/* ToolBar & Extension Button (fallback safety if any QToolBar is used) */
QToolBar {
    background-color: #162036;
    border: 1px solid #243350;
    spacing: 4px;
    padding: 3px;
}
QToolButton#qt_toolbar_ext_button {
    background-color: #1D2A44;
    color: #E6ECF8;
    border: 1px solid #2D8CFF;
    border-radius: 6px;
    padding: 2px 6px;
    min-width: 24px;
    min-height: 24px;
    qproperty-text: "⋯";
}
QToolButton#qt_toolbar_ext_button:hover {
    background-color: #2D8CFF;
    color: #FFFFFF;
}

/* Splitters */
QSplitter::handle {
    background-color: #243350;
    border-radius: 2px;
    margin: 2px 1px;
}
QSplitter::handle:hover {
    background-color: #2D8CFF;
}
"""


def get_active_screen(widget: Optional[QWidget] = None):
    """Return the QScreen best matching widget, parent, or primary display."""
    if widget is not None:
        try:
            wh = widget.windowHandle()
            if wh is not None and wh.screen() is not None:
                return wh.screen()
            if hasattr(widget, "screen") and widget.screen() is not None:
                return widget.screen()
            pos = widget.frameGeometry().center()
            scr = QGuiApplication.screenAt(pos)
            if scr is not None:
                return scr
        except Exception:
            pass
    return QGuiApplication.primaryScreen()


def is_geometry_on_any_screen(rect: QRect) -> bool:
    """Verify saved window geometry visibly overlaps a currently attached monitor."""
    if not rect.isValid() or rect.width() < 200 or rect.height() < 150:
        return False
    for scr in QGuiApplication.screens():
        inter = scr.availableGeometry().intersected(rect)
        if inter.width() >= 120 and inter.height() >= 80:
            return True
    return False


def clamp_widget_to_screen(widget: QWidget, screen=None, max_ratio: float = 1.0) -> None:
    """Clamp widget size and position strictly inside target screen availableGeometry."""
    scr = screen or get_active_screen(widget)
    if scr is None:
        return
    avail = scr.availableGeometry()
    max_w = max(320, int(avail.width() * max_ratio))
    max_h = max(240, int(avail.height() * max_ratio))
    min_w = min(widget.minimumWidth(), max(280, avail.width() - 20))
    min_h = min(widget.minimumHeight(), max(220, avail.height() - 20))
    if widget.minimumWidth() > min_w or widget.minimumHeight() > min_h:
        widget.setMinimumSize(min_w, min_h)

    cur_w = min(max(widget.width(), min_w), max_w)
    cur_h = min(max(widget.height(), min_h), max_h)
    if cur_w != widget.width() or cur_h != widget.height():
        widget.resize(cur_w, cur_h)

    fg = widget.frameGeometry()
    w = min(fg.width(), avail.width())
    h = min(fg.height(), avail.height())
    x = max(avail.left(), min(fg.left(), avail.right() - w + 1))
    y = max(avail.top(), min(fg.top(), avail.bottom() - h + 1))
    if x != fg.left() or y != fg.top():
        widget.move(x, y)


def fit_dialog_to_screen(dlg: QDialog, pref_w: int, pref_h: int, max_ratio: float = 0.90) -> None:
    """Set dialog maximumSize <= 90% availableGeometry and resize safely within active screen."""
    scr = get_active_screen(dlg.parentWidget() if isinstance(dlg.parentWidget(), QWidget) else dlg)
    if scr is None:
        dlg.resize(pref_w, pref_h)
        return
    avail = scr.availableGeometry()
    max_w = max(360, int(avail.width() * max_ratio))
    max_h = max(280, int(avail.height() * max_ratio))
    dlg.setMaximumSize(max_w, max_h)
    dlg.setMinimumSize(min(360, max_w - 20), min(260, max_h - 20))
    dlg.resize(min(pref_w, max_w), min(pref_h, max_h))


class ToggleSwitch(QCheckBox):
    """Custom pill toggle switch (on/off) replacing cluttered checkboxes for binary switches."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._track_w = 34
        self._track_h = 18

    def sizeHint(self):
        sz = super().sizeHint()
        sz.setWidth(max(sz.width() + 18, self._track_w + 16))
        sz.setHeight(max(sz.height(), 24))
        return sz

    def hitButton(self, pos):
        return self.contentsRect().contains(pos)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        ty = (rect.height() - self._track_h) / 2.0
        track_rect = QRectF(0, ty, self._track_w, self._track_h)

        if not self.isEnabled():
            track_col = QColor("#1E293B")
            knob_col = QColor("#64748B")
        elif self.isChecked():
            track_col = QColor("#2D8CFF")
            knob_col = QColor("#FFFFFF")
        else:
            track_col = QColor("#243350")
            knob_col = QColor("#9AA8C4")

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_col)
        painter.drawRoundedRect(track_rect, self._track_h / 2.0, self._track_h / 2.0)

        knob_d = self._track_h - 4
        kx = (self._track_w - knob_d - 2) if self.isChecked() else 2.0
        ky = ty + 2.0
        painter.setBrush(knob_col)
        painter.drawEllipse(QRectF(kx, ky, knob_d, knob_d))

        if self.text():
            painter.setPen(QColor("#E6ECF8") if self.isEnabled() else QColor("#5C6B8A"))
            text_rect = QRectF(self._track_w + 8, 0, rect.width() - self._track_w - 8, rect.height())
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())


class SegmentedControl(QWidget):
    """Exclusive segmented button bar for small option sets (e.g. Solid/Blur or Speed presets)."""
    selection_changed = Signal(object)

    def __init__(self, options: List[Tuple[str, object]], parent=None):
        super().__init__(parent)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: List[Tuple[QPushButton, object]] = []

        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)

        self.setStyleSheet("""
            SegmentedControl {
                background-color: #0F1626;
                border: 1px solid #243350;
                border-radius: 8px;
            }
        """)

        for idx, (label, val) in enumerate(options):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    color: #9AA8C4;
                    border: none;
                    border-radius: 6px;
                    padding: 4px 8px;
                    font-size: 9pt;
                    font-weight: 600;
                }
                QPushButton:hover {
                    color: #E6ECF8;
                    background-color: #1D2A44;
                }
                QPushButton:checked {
                    background-color: #2D8CFF;
                    color: #FFFFFF;
                }
            """)
            self._group.addButton(btn, idx)
            self._buttons.append((btn, val))
            lay.addWidget(btn)

        self._group.idClicked.connect(self._on_id_clicked)

    def _on_id_clicked(self, idx: int):
        if 0 <= idx < len(self._buttons):
            self.selection_changed.emit(self._buttons[idx][1])

    def set_value(self, value: object, emit: bool = False):
        for btn, val in self._buttons:
            if val == value:
                btn.setChecked(True)
                if emit:
                    self.selection_changed.emit(val)
                return

    def current_value(self) -> Optional[object]:
        for btn, val in self._buttons:
            if btn.isChecked():
                return val
        return self._buttons[0][1] if self._buttons else None
