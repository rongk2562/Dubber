"""
Update Dialog Widget: Check for updates, view changelog, download, and auto-restart replacement
"""

import sys
import webbrowser
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QTextBrowser, QFrame, QMessageBox, QApplication
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor

from app.config import APP_VERSION, APP_BUILD_DATE, GITHUB_RELEASES_URL
from app.updater import UpdateInfo, CheckUpdateWorker, DownloadUpdateWorker, apply_update_and_restart


class UpdateDialog(QDialog):
    def __init__(self, update_info: UpdateInfo = None, manifest_url: str = None, lang: str = "km", parent=None):
        super().__init__(parent)
        self.update_info = update_info
        self.manifest_url = manifest_url
        self.lang = lang
        self._download_worker = None
        self._check_worker = None
        
        self.setWindowTitle("🔄 ពិនិត្យមើលកំណែថ្មី (Software Update)" if lang == "km" else "Software Update")
        self.resize(540, 480)
        self.setMinimumSize(480, 420)
        self._init_ui()

        if self.update_info is None:
            self._start_check()
        else:
            self._render_update_state()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # Header Banner
        header = QHBoxLayout()
        self.lbl_icon = QLabel("🚀")
        self.lbl_icon.setStyleSheet("font-size: 36px;")
        header.addWidget(self.lbl_icon)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(2)
        self.lbl_title = QLabel("ពិនិត្យមើលកំណែថ្មី (Check for Updates)" if self.lang == "km" else "Check for Updates")
        self.lbl_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #f8fafc;")
        title_vbox.addWidget(self.lbl_title)

        self.lbl_subtitle = QLabel(f"កំណែបច្ចុប្បន្ន៖ v{APP_VERSION} (Build: {APP_BUILD_DATE})")
        self.lbl_subtitle.setStyleSheet("font-size: 11px; color: #94a3b8;")
        title_vbox.addWidget(self.lbl_subtitle)
        header.addLayout(title_vbox, stretch=1)
        layout.addLayout(header)

        # Version Compare Badge Frame
        self.badge_frame = QFrame()
        self.badge_frame.setStyleSheet("""
            QFrame {
                background-color: #0f172a;
                border: 1px solid #1e293b;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        badge_layout = QHBoxLayout(self.badge_frame)
        badge_layout.setContentsMargins(12, 8, 12, 8)

        self.lbl_badge_curr = QLabel(f"📦 របស់អ្នក៖ v{APP_VERSION}")
        self.lbl_badge_curr.setStyleSheet("color: #cbd5e1; font-weight: bold; font-size: 12px;")
        badge_layout.addWidget(self.lbl_badge_curr)

        badge_layout.addStretch()

        self.lbl_badge_latest = QLabel("កំពុងពិនិត្យ...")
        self.lbl_badge_latest.setStyleSheet("color: #38bdf8; font-weight: bold; font-size: 12px;")
        badge_layout.addWidget(self.lbl_badge_latest)

        layout.addWidget(self.badge_frame)

        # Changelog / Information Text Browser
        lbl_notes = QLabel("📝 កំណត់ត្រាការផ្លាស់ប្ដូរ (Changelog):" if self.lang == "km" else "Release Notes:")
        lbl_notes.setStyleSheet("font-weight: bold; color: #e2e8f0; font-size: 12px;")
        layout.addWidget(lbl_notes)

        self.txt_notes = QTextBrowser()
        self.txt_notes.setStyleSheet("""
            QTextBrowser {
                background-color: #090d16;
                color: #e2e8f0;
                border: 1px solid #1e293b;
                border-radius: 6px;
                padding: 10px;
                font-family: 'Kantumruy Pro', 'Segoe UI', sans-serif;
                font-size: 12px;
                line-height: 1.5;
            }
        """)
        self.txt_notes.setPlaceholderText("ព័ត៌មានអំពីកំណែថ្មីនឹងបង្ហាញនៅទីនេះ...")
        layout.addWidget(self.txt_notes, stretch=1)

        # Progress Bar (hidden by default)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 6px;
                height: 18px;
                text-align: center;
                color: white;
                font-size: 10px;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284c7, stop:1 #38bdf8);
                border-radius: 5px;
            }
        """)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.lbl_progress_status = QLabel("")
        self.lbl_progress_status.setStyleSheet("color: #38bdf8; font-size: 11px;")
        self.lbl_progress_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_progress_status.setVisible(False)
        layout.addWidget(self.lbl_progress_status)

        # Buttons Bar
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(8)

        self.btn_open_browser = QPushButton("🌐 ទាញយកតាម Browser" if self.lang == "km" else "Open in Browser")
        self.btn_open_browser.setStyleSheet("background-color: #1e293b; color: #cbd5e1; border: 1px solid #334155; padding: 6px 12px;")
        self.btn_open_browser.clicked.connect(self._open_in_browser)
        btn_bar.addWidget(self.btn_open_browser)

        btn_bar.addStretch()

        self.btn_close = QPushButton("បិទ (Close)" if self.lang == "km" else "Close")
        self.btn_close.setStyleSheet("background-color: #334155; color: white; padding: 6px 14px;")
        self.btn_close.clicked.connect(self.close)
        btn_bar.addWidget(self.btn_close)

        self.btn_update = QPushButton("⚡ Update ឥឡូវនេះ" if self.lang == "km" else "Update Now")
        self.btn_update.setStyleSheet("""
            QPushButton {
                background-color: #0284c7;
                color: white;
                font-weight: bold;
                border-radius: 4px;
                padding: 6px 18px;
            }
            QPushButton:hover {
                background-color: #0369a1;
            }
            QPushButton:disabled {
                background-color: #1e293b;
                color: #64748b;
            }
        """)
        self.btn_update.clicked.connect(self._start_download)
        btn_bar.addWidget(self.btn_update)

        layout.addLayout(btn_bar)

    def _start_check(self):
        self.lbl_title.setText("កំពុងពិនិត្យមើលកំណែថ្មី..." if self.lang == "km" else "Checking for updates...")
        self.lbl_badge_latest.setText("កំពុងទាញយកព័ត៌មាន...")
        self.btn_update.setEnabled(False)

        self._check_worker = CheckUpdateWorker(self.manifest_url)
        self._check_worker.update_found.connect(self._on_update_found)
        self._check_worker.no_update.connect(self._on_no_update)
        self._check_worker.check_failed.connect(self._on_check_failed)
        self._check_worker.start()

    def _on_update_found(self, info: UpdateInfo):
        self.update_info = info
        self._render_update_state()

    def _on_no_update(self, curr_v: str):
        self.lbl_icon.setText("✅")
        self.lbl_title.setText("អ្នកកំពុងប្រើប្រាស់កំណែចុងក្រោយបំផុត!" if self.lang == "km" else "You are up to date!")
        self.lbl_badge_latest.setText(f"✨ ចុងក្រោយបំផុត៖ v{curr_v}")
        self.lbl_badge_latest.setStyleSheet("color: #10b981; font-weight: bold; font-size: 12px;")
        
        self.txt_notes.setHtml(
            f"<div style='color: #94a3b8; padding: 10px; text-align: center;'>"
            f"<h3 style='color: #10b981;'>កម្មវិធីរបស់អ្នកគឺជាកំណែថ្មីបំផុតហើយ (v{curr_v})</h3>"
            f"<p>គ្មានការអាប់ដេតថ្មីនៅឡើយទេ។ សូមអរគុណដែលបានប្រើប្រាស់!</p>"
            f"</div>"
        )
        self.btn_update.setVisible(False)

    def _on_check_failed(self, err_msg: str):
        self.lbl_icon.setText("⚠️")
        self.lbl_title.setText("មិនអាចពិនិត្យកំណែថ្មីបានទេ" if self.lang == "km" else "Could not check for updates")
        self.lbl_badge_latest.setText("បរាជ័យ (Offline)")
        self.lbl_badge_latest.setStyleSheet("color: #f43f5e; font-weight: bold; font-size: 12px;")
        self.txt_notes.setPlainText(
            f"ដំណើរការពិនិត្យមើលកំណែថ្មីមិនជោគជ័យ៖\n{err_msg}\n\n"
            f"សូមពិនិត្យមើលការតភ្ជាប់អ៊ីនធឺណិត ឬទាញយកដោយផ្ទាល់តាមរយៈទំព័រ GitHub Releases។"
        )
        self.btn_update.setEnabled(False)

    def _render_update_state(self):
        info = self.update_info
        if not info:
            return

        if info.is_update_available:
            self.lbl_icon.setText("🎉")
            self.lbl_title.setText(f"មានកំណែថ្មី v{info.latest_version} រួចរាល់!" if self.lang == "km" else f"Version {info.latest_version} Available!")
            self.lbl_badge_latest.setText(f"🚀 កំណែថ្មី៖ v{info.latest_version}")
            self.lbl_badge_latest.setStyleSheet("color: #38bdf8; font-weight: bold; font-size: 12px;")

            notes = info.changelog_km or info.changelog_en or "ការកែលម្អទូទៅ និងការជួសជុលបញ្ហា (General improvements & bug fixes)"
            formatted_notes = notes.replace("\n", "<br>")

            self.txt_notes.setHtml(
                f"<div style='color: #f1f5f9;'>"
                f"<b style='color: #38bdf8; font-size: 13px;'>កំណែថ្មី Version {info.latest_version}</b> "
                f"<span style='color: #94a3b8;'>({info.release_date})</span><br><br>"
                f"{formatted_notes}"
                f"</div>"
            )
            self.btn_update.setEnabled(bool(info.download_url))
        else:
            self._on_no_update(info.current_version)

    def _open_in_browser(self):
        url = (self.update_info.download_url if self.update_info and self.update_info.download_url else GITHUB_RELEASES_URL)
        webbrowser.open(url)

    def _start_download(self):
        if not self.update_info or not self.update_info.download_url:
            self._open_in_browser()
            return

        self.btn_update.setEnabled(False)
        self.btn_open_browser.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.lbl_progress_status.setVisible(True)
        self.lbl_progress_status.setText("កំពុងចាប់ផ្ដើមទាញយក...")

        self._download_worker = DownloadUpdateWorker(self.update_info.download_url)
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.finished.connect(self._on_download_finished)
        self._download_worker.failed.connect(self._on_download_failed)
        self._download_worker.start()

    def _on_download_progress(self, pct: int, msg: str):
        self.progress_bar.setValue(pct)
        self.lbl_progress_status.setText(msg)

    def _on_download_finished(self, new_exe_path: str):
        self.progress_bar.setValue(100)
        self.lbl_progress_status.setText("ទាញយករួចរាល់ ១០០%!")

        is_frozen = getattr(sys, "frozen", False)
        if is_frozen:
            box = QMessageBox(self)
            box.setWindowTitle("អាប់ដេតរួចរាល់ (Update Ready)")
            box.setText(
                f"ឯកសារកំណែថ្មី v{self.update_info.latest_version} ត្រូវបានទាញយករួចរាល់!\n\n"
                f"តើអ្នកចង់ចាប់ផ្ដើមឡើងវិញ (Restart) ដើម្បីប្រើប្រាស់កំណែថ្មីឥឡូវនេះទេ?"
            )
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if box.exec() == QMessageBox.StandardButton.Yes:
                apply_update_and_restart(new_exe_path)
                QApplication.quit()
        else:
            QMessageBox.information(
                self,
                "ទាញយករួចរាល់ (Download Complete)",
                f"ឯកសារកំណែថ្មីត្រូវបានរក្សាទុកនៅ៖\n{new_exe_path}\n\n"
                f"(ដោយសារអ្នកកំពុងដំណើរការក្នុង Python Dev Mode សូមជំនួសឯកសារ ឬ run ឯកសារនេះដោយផ្ទាល់)"
            )
            self.accept()

    def _on_download_failed(self, err_msg: str):
        self.progress_bar.setVisible(False)
        self.lbl_progress_status.setVisible(False)
        self.btn_update.setEnabled(True)
        self.btn_open_browser.setEnabled(True)
        QMessageBox.warning(self, "Download Failed", f"{err_msg}\n\nសូមសាកល្បងចុច 'ទាញយកតាម Browser' ជំនួសវិញ។")
