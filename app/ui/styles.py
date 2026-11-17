"""
Application Dark Theme Stylesheet and Kantumruy Pro Typography
"""

DARK_THEME_QSS = """
* {
    font-family: 'Kantumruy Pro', 'Khmer OS', 'Leelawadee UI', 'Segoe UI', sans-serif;
    font-size: 13px;
    color: #e2e8f0;
}

QMainWindow, QDialog {
    background-color: #0f172a;
}

QWidget#centralWidget {
    background-color: #0f172a;
}

/* Menu Bar & Menus */
QMenuBar {
    background-color: #1e293b;
    color: #f8fafc;
    border-bottom: 1px solid #334155;
    padding: 2px;
}
QMenuBar::item {
    background: transparent;
    padding: 6px 12px;
    border-radius: 4px;
}
QMenuBar::item:selected {
    background-color: #334155;
}
QMenu {
    background-color: #1e293b;
    color: #f8fafc;
    border: 1px solid #475569;
    border-radius: 6px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 24px;
    border-radius: 4px;
}
QMenu::item:selected {
    background-color: #0284c7;
    color: #ffffff;
}

/* ToolBar */
QToolBar {
    background-color: #1e293b;
    border-bottom: 1px solid #334155;
    padding: 6px;
    spacing: 8px;
}

/* Buttons */
QPushButton {
    background-color: #1e293b;
    color: #f8fafc;
    border: 1px solid #475569;
    border-radius: 6px;
    padding: 6px 16px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #334155;
    border-color: #64748b;
}
QPushButton:pressed {
    background-color: #0f172a;
}
QPushButton:disabled {
    background-color: #1e293b;
    color: #64748b;
    border-color: #334155;
}

/* Primary Action Buttons */
QPushButton.primary-btn {
    background-color: #0284c7;
    color: #ffffff;
    border: 1px solid #38bdf8;
    font-weight: 600;
}
QPushButton.primary-btn:hover {
    background-color: #0369a1;
}

/* Success Buttons */
QPushButton.success-btn {
    background-color: #059669;
    color: #ffffff;
    border: 1px solid #34d399;
    font-weight: 600;
}
QPushButton.success-btn:hover {
    background-color: #047857;
}

/* Danger Buttons */
QPushButton.danger-btn {
    background-color: #dc2626;
    color: #ffffff;
    border: 1px solid #f87171;
}
QPushButton.danger-btn:hover {
    background-color: #b91c1c;
}

/* Tables */
QTableWidget {
    background-color: #111827;
    alternate-background-color: #1f2937;
    gridline-color: #374151;
    border: 1px solid #374151;
    border-radius: 8px;
    selection-background-color: #0369a1;
    selection-color: #ffffff;
}
QHeaderView::section {
    background-color: #1f2937;
    color: #93c5fd;
    font-weight: 600;
    font-size: 12px;
    padding: 7px 10px;
    border: none;
    border-right: 1px solid #374151;
    border-bottom: 2px solid #0284c7;
}

/* Text Inputs */
QLineEdit, QTextEdit, QPlainTextEdit {
    background-color: #111827;
    border: 1px solid #4b5563;
    border-radius: 6px;
    padding: 6px 10px;
    color: #f9fafb;
    selection-background-color: #0284c7;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {
    border: 1px solid #38bdf8;
}

/* Combo boxes */
QComboBox {
    background-color: #1e293b;
    border: 1px solid #475569;
    border-radius: 6px;
    padding: 6px 12px;
    color: #f8fafc;
}
QComboBox:hover {
    border-color: #38bdf8;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background-color: #1e293b;
    border: 1px solid #475569;
    selection-background-color: #0284c7;
    selection-color: #ffffff;
}

/* Sliders */
QSlider::groove:horizontal {
    height: 6px;
    background: #334155;
    border-radius: 3px;
}
QSlider::sub-page:horizontal {
    background: #0284c7;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #38bdf8;
    width: 16px;
    margin-top: -5px;
    margin-bottom: -5px;
    border-radius: 8px;
}
QSlider::handle:horizontal:hover {
    background: #7dd3fc;
}

/* Progress Bar */
QProgressBar {
    background-color: #1e293b;
    border: 1px solid #334155;
    border-radius: 6px;
    text-align: center;
    color: #f8fafc;
    font-weight: bold;
    height: 18px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284c7, stop:1 #38bdf8);
    border-radius: 5px;
}

/* Status Bar */
QStatusBar {
    background-color: #1e293b;
    color: #94a3b8;
    border-top: 1px solid #334155;
}

/* ToolBar & ToolButtons */
QToolBar {
    background-color: #1e293b;
    border-bottom: 1px solid #334155;
    padding: 5px 10px;
    spacing: 8px;
}
QToolButton {
    background-color: transparent;
    color: #f1f5f9;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 500;
}
QToolButton:hover {
    background-color: #334155;
    border-color: #475569;
}
QToolButton:pressed {
    background-color: #0f172a;
}

/* Modern Tab Widget & Tab Bar */
QTabWidget::pane {
    border: 1px solid #334155;
    background-color: #0f172a;
    border-radius: 6px;
    top: -1px;
}
QTabBar::tab {
    background-color: #1e293b;
    color: #94a3b8;
    padding: 6px 14px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
    font-weight: 600;
    font-size: 12px;
    border: 1px solid #334155;
    border-bottom: none;
}
QTabBar::tab:selected {
    background-color: #0f172a;
    color: #38bdf8;
    border-color: #38bdf8;
    border-bottom: 2px solid #0f172a;
}
QTabBar::tab:hover:!selected {
    background-color: #334155;
    color: #f8fafc;
}

/* Splitter */
QSplitter::handle:horizontal {
    background-color: #1e293b;
    width: 6px;
}
QSplitter::handle:horizontal:hover {
    background-color: #0284c7;
}

/* Checkboxes */
QCheckBox {
    spacing: 8px;
    color: #e2e8f0;
    font-size: 12px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid #475569;
    background-color: #1e293b;
}
QCheckBox::indicator:checked {
    background-color: #0284c7;
    border-color: #38bdf8;
}

/* Group Boxes / Cards */
QGroupBox {
    background-color: #1e293b;
    border: 1px solid #334155;
    border-radius: 8px;
    margin-top: 10px;
    padding-top: 14px;
    font-weight: bold;
    color: #38bdf8;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    left: 10px;
}

/* ScrollBars */
QScrollBar:vertical {
    background: #0f172a;
    width: 8px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: #334155;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: #475569;
}
"""
