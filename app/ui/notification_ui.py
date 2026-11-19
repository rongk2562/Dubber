"""
Notification UI Components:
- Non-blocking bottom-right Toast popup (auto-dismisses in 7s, queued)
- Update Banner for new app versions ("ទាញយក" & "ក្រោយ 24h")
- Notification Center dialog (newest first, type icons, unread badge, mark all read, hide/dismiss, safe link confirmation)
"""

import os
from datetime import datetime
from typing import Optional
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QMessageBox
)
from PySide6.QtCore import Qt, QTimer, Signal, QUrl
from PySide6.QtGui import QDesktopServices

from app.notifications import (
    NotificationItem,
    LatestVersionInfo,
    NotificationManager,
    TYPE_ICONS,
    is_allowed_link,
    render_markdown_lite_safe,
)
from app.ui.theme import fit_dialog_to_screen, clamp_widget_to_screen, get_active_screen


def confirm_and_open_safe_url(url: str, parent=None) -> bool:
    """Validate HTTPS + domain allowlist and show real URL before opening browser."""
    if not is_allowed_link(url):
        QMessageBox.warning(
            parent,
            "តំណមិនត្រូវបានអនុញ្ញាត (Blocked Link)",
            f"តំណភ្ជាប់នេះមិនស្ថិតក្នុងបញ្ជី domain ដែលទុកចិត្ត (HTTPS allowlist) ទេ៖\n{url}"
        )
        return False

    ans = QMessageBox.question(
        parent,
        "បើកតំណភ្ជាប់ក្នុង Browser?",
        f"តើអ្នកចង់បើកតំណភ្ជាប់ខាងក្រោមក្នុងកម្មវិធី Browser ឬទេ?\n\n🔗 {url}",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    if ans == QMessageBox.StandardButton.Yes:
        QDesktopServices.openUrl(QUrl(url))
        return True
    return False


class NotificationToastWidget(QFrame):
    """
    Non-blocking toast card positioned at the bottom-right corner of MainWindow.
    Auto-closes after 7 seconds. Clicking opens the Notification Center (or runs action).
    """
    closed = Signal()
    clicked = Signal(object)

    def __init__(self, item: NotificationItem, parent: QWidget):
        super().__init__(parent)
        self.item = item
        self.setWindowFlags(Qt.WindowType.SubWindow)
        self.setMinimumWidth(260)
        self.setMaximumWidth(min(360, max(260, parent.width() - 32)))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        accent = {
            "critical": "#ef4444",
            "error": "#ef4444",
            "warning": "#f59e0b",
            "update": "#0284c7",
            "success": "#10b981",
            "promo": "#a855f7",
        }.get(item.type, "#38bdf8")

        self.setStyleSheet(f"""
            QFrame {{
                background-color: #0f172a;
                border: 2px solid {accent};
                border-radius: 8px;
                color: #f8fafc;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        hdr = QHBoxLayout()
        icon = TYPE_ICONS.get(item.type, "ℹ️")
        lbl_title = QLabel(f"{icon}  <b>{render_markdown_lite_safe(item.title)}</b>")
        lbl_title.setStyleSheet("border: none; font-size: 9.75pt; color: #f8fafc;")
        lbl_title.setWordWrap(True)
        hdr.addWidget(lbl_title, stretch=1)

        btn_x = QPushButton("✕")
        btn_x.setFixedSize(22, 22)
        btn_x.setStyleSheet("border: none; background: transparent; color: #94a3b8; font-weight: bold;")
        btn_x.clicked.connect(self._close_toast)
        hdr.addWidget(btn_x)
        layout.addLayout(hdr)

        if item.body:
            lbl_body = QLabel(render_markdown_lite_safe(item.body[:180] + ("..." if len(item.body) > 180 else "")))
            lbl_body.setStyleSheet("border: none; font-size: 9pt; color: #cbd5e1;")
            lbl_body.setWordWrap(True)
            layout.addWidget(lbl_body)

        if item.action_label or item.action_path:
            row_act = QHBoxLayout()
            row_act.addStretch()
            btn_act = QPushButton(item.action_label or "បើក")
            btn_act.setStyleSheet(f"background-color: {accent}; color: white; border: none; border-radius: 4px; padding: 4px 10px; font-weight: bold; font-size: 8.5pt;")
            btn_act.clicked.connect(self._trigger_action)
            row_act.addWidget(btn_act)
            layout.addLayout(row_act)

        self.adjustSize()
        self._reposition()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._close_toast)
        self._timer.start(7000)

    def _reposition(self):
        if self.parentWidget():
            pw = self.parentWidget().width()
            ph = self.parentWidget().height()
            self.move(max(10, pw - self.width() - 20), max(10, ph - self.height() - 36))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.item)
            self._close_toast()

    def _trigger_action(self):
        if self.item.action_cb:
            try:
                self.item.action_cb()
            except Exception as e:
                print(f"[Toast] Action error: {e}")
        elif self.item.action_path and os.path.exists(self.item.action_path):
            folder = self.item.action_path if os.path.isdir(self.item.action_path) else os.path.dirname(self.item.action_path)
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        self._close_toast()

    def _close_toast(self):
        if self._timer.isActive():
            self._timer.stop()
        self.hide()
        self.deleteLater()
        self.closed.emit()


class UpdateBannerWidget(QFrame):
    """Top non-blocking banner showing available version update with Download & Later (24h)."""
    def __init__(self, manager: NotificationManager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self._info: Optional[LatestVersionInfo] = None
        self.setVisible(False)
        self.setStyleSheet("""
            QFrame {
                background-color: #0c4a6e;
                border: 1px solid #0284c7;
                border-radius: 6px;
                color: #f8fafc;
            }
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(10)

        self.lbl_msg = QLabel("")
        self.lbl_msg.setStyleSheet("border: none; font-size: 9pt;")
        self.lbl_msg.setWordWrap(True)
        lay.addWidget(self.lbl_msg, stretch=1)

        self.btn_dl = QPushButton("⬇️ ទាញយក")
        self.btn_dl.setStyleSheet("background-color: #059669; color: white; font-weight: bold; padding: 4px 12px; border-radius: 4px;")
        self.btn_dl.clicked.connect(self._on_download)
        lay.addWidget(self.btn_dl)

        self.btn_later = QPushButton("ក្រោយ (24h)")
        self.btn_later.setStyleSheet("background-color: #334155; color: #cbd5e1; padding: 4px 10px; border-radius: 4px;")
        self.btn_later.clicked.connect(self._on_later)
        lay.addWidget(self.btn_later)

    def show_update(self, info: LatestVersionInfo):
        self._info = info
        notes_preview = f" — {info.notes[:100]}" if info.notes else ""
        self.lbl_msg.setText(f"🎉 <b>មានជំនាន់ថ្មី v{info.version}</b>{render_markdown_lite_safe(notes_preview)}")
        self.btn_dl.setVisible(bool(info.url))
        self.setVisible(True)

    def _on_download(self):
        if self._info and self._info.url:
            confirm_and_open_safe_url(self._info.url, parent=self)

    def _on_later(self):
        self.manager.snooze_update_24h()
        self.setVisible(False)


class NotificationCenterDialog(QDialog):
    """
    Dark-themed Notification Center popover/dialog anchored under the Bell button,
    showing developer announcements & local notifications newest first.
    """
    def __init__(self, manager: NotificationManager, lang: str = "km", anchor_widget: Optional[QWidget] = None, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.lang = lang
        self.anchor_widget = anchor_widget
        self.setWindowTitle("មជ្ឈមណ្ឌលដំណឹង" if lang == "km" else "Notification Center")
        if anchor_widget is not None:
            self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        fit_dialog_to_screen(self, 440, 480)
        self.setStyleSheet("""
            NotificationCenterDialog {
                background-color: #0F1626;
                border: 1px solid #243350;
                border-radius: 10px;
            }
            QScrollArea, QScrollArea > QWidget > QWidget {
                background-color: #0F1626;
            }
        """)
        self._init_ui()
        self._populate()
        if anchor_widget is not None:
            self._position_below_anchor()

    def _position_below_anchor(self):
        if not self.anchor_widget:
            return
        pt = self.anchor_widget.mapToGlobal(self.anchor_widget.rect().bottomRight())
        x = pt.x() - self.width()
        y = pt.y() + 6
        self.move(x, y)
        clamp_widget_to_screen(self, get_active_screen(self.anchor_widget), max_ratio=0.90)

    def _init_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        # Header controls
        hdr = QHBoxLayout()
        self.lbl_count = QLabel("ដំណឹងទាំងអស់" if self.lang == "km" else "Notifications")
        self.lbl_count.setStyleSheet("font-weight: bold; font-size: 9.75pt; color: #2D8CFF;")
        hdr.addWidget(self.lbl_count)
        hdr.addStretch()

        btn_refresh = QPushButton("🔄 ពិនិត្យឥឡូវនេះ" if self.lang == "km" else "🔄 Refresh")
        btn_refresh.clicked.connect(lambda: self.manager.poll_remote_async(force=True))
        hdr.addWidget(btn_refresh)

        btn_mark_all = QPushButton("✓ សម្គាល់ថាបានអានទាំងអស់" if self.lang == "km" else "✓ Mark All Read")
        btn_mark_all.setProperty("variant", "primary")
        btn_mark_all.clicked.connect(self._mark_all_read)
        hdr.addWidget(btn_mark_all)
        lay.addLayout(hdr)

        # Scroll area for cards (explicit dark viewport)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll_body = QWidget()
        self.scroll_body.setObjectName("scrollBody")
        self.cards_layout = QVBoxLayout(self.scroll_body)
        self.cards_layout.setSpacing(8)
        self.cards_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.scroll_body)
        lay.addWidget(self.scroll, stretch=1)

        # Bottom close
        row_btm = QHBoxLayout()
        row_btm.addStretch()
        btn_close = QPushButton("បិទ" if self.lang == "km" else "Close")
        btn_close.clicked.connect(self.accept)
        row_btm.addWidget(btn_close)
        lay.addLayout(row_btm)

        self.manager.items_changed.connect(self._populate)

    def _populate(self):
        while self.cards_layout.count():
            child = self.cards_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        items = self.manager.get_visible_items()
        unread = sum(1 for it in items if not it.read)
        self.lbl_count.setText(f"ដំណឹង ({unread} មិនទាន់អាន)" if self.lang == "km" else f"Notifications ({unread} unread)")

        if not items:
            empty_box = QWidget()
            eb_lay = QVBoxLayout(empty_box)
            eb_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            eb_lay.setContentsMargins(20, 48, 20, 48)
            lbl_icon = QLabel("🔔")
            lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_icon.setStyleSheet("font-size: 24pt; color: #5C6B8A;")
            eb_lay.addWidget(lbl_icon)
            empty = QLabel("មិនមានដំណឹងថ្មី" if self.lang == "km" else "No new notifications")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #9AA8C4; font-size: 9.75pt;")
            eb_lay.addWidget(empty)
            self.cards_layout.addWidget(empty_box)
            return

        for it in items:
            self.cards_layout.addWidget(self._build_card(it))

    def _build_card(self, it: NotificationItem) -> QFrame:
        card = QFrame()
        border_col = "#38bdf8" if not it.read else "#334155"
        bg_col = "#1e293b" if not it.read else "#0f172a"
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {bg_col};
                border: 1px solid {border_col};
                border-radius: 6px;
            }}
        """)
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)

        row_top = QHBoxLayout()
        icon = TYPE_ICONS.get(it.type, "ℹ️")
        unread_dot = "🔵 " if not it.read else ""
        pin_tag = "📌 " if it.pinned else ""
        lbl_t = QLabel(f"{unread_dot}{pin_tag}{icon} <b>{render_markdown_lite_safe(it.title)}</b>")
        lbl_t.setStyleSheet("border: none; color: #f8fafc; font-size: 9.75pt;")
        lbl_t.setWordWrap(True)
        row_top.addWidget(lbl_t, stretch=1)

        dt_str = it.publish_at[:10] if it.publish_at else datetime.fromtimestamp(it.created_ts).strftime("%Y-%m-%d %H:%M")
        lbl_dt = QLabel(dt_str)
        lbl_dt.setStyleSheet("border: none; color: #64748b; font-size: 8.5pt;")
        row_top.addWidget(lbl_dt)
        v.addLayout(row_top)

        if it.body:
            lbl_b = QLabel(render_markdown_lite_safe(it.body))
            lbl_b.setStyleSheet("border: none; color: #cbd5e1; font-size: 9pt;")
            lbl_b.setWordWrap(True)
            v.addWidget(lbl_b)

        row_act = QHBoxLayout()
        if it.link_url:
            btn_lnk = QPushButton(f"🔗 {it.link_label or 'បើកតំណ'}")
            btn_lnk.setStyleSheet("background-color: #0369a1; color: white; border: none; border-radius: 4px; padding: 4px 10px; font-size: 8.5pt;")
            btn_lnk.clicked.connect(lambda checked=False, u=it.link_url, iid=it.id: (self.manager.mark_read(iid), confirm_and_open_safe_url(u, self)))
            row_act.addWidget(btn_lnk)

        if it.action_path and os.path.exists(it.action_path):
            btn_open = QPushButton(it.action_label or "📂 បើកថត")
            btn_open.setStyleSheet("background-color: #047857; color: white; border: none; border-radius: 4px; padding: 4px 10px; font-size: 8.5pt;")
            btn_open.clicked.connect(lambda checked=False, p=it.action_path: QDesktopServices.openUrl(QUrl.fromLocalFile(p if os.path.isdir(p) else os.path.dirname(p))))
            row_act.addWidget(btn_open)

        row_act.addStretch()

        if not it.read:
            btn_read = QPushButton("✓ អានហើយ")
            btn_read.setStyleSheet("background-color: #334155; color: #e2e8f0; border: none; border-radius: 4px; padding: 4px 8px; font-size: 8.5pt;")
            btn_read.clicked.connect(lambda checked=False, iid=it.id: self.manager.mark_read(iid))
            row_act.addWidget(btn_read)

        btn_hide = QPushButton("🗑️ លាក់")
        btn_hide.setStyleSheet("background-color: #450a0a; color: #fca5a5; border: none; border-radius: 4px; padding: 4px 8px; font-size: 8.5pt;")
        btn_hide.clicked.connect(lambda checked=False, iid=it.id: self.manager.dismiss_item(iid))
        row_act.addWidget(btn_hide)

        v.addLayout(row_act)
        return card

    def _mark_all_read(self):
        self.manager.mark_all_read()
