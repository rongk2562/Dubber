"""
AI Movie Translator & Auto Dubbing Studio
Entry point for the application (Standalone .exe & Dev ready)
"""

import sys
import os
import io
import subprocess
import multiprocessing
from pathlib import Path

# 1. Ensure root directory is in sys.path
APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# 2. Environment safeguards for standalone Windows .exe on any PC
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"


class _SafeNullStream(io.TextIOBase):
    """Fallback stream when running as a windowed .exe where sys.stdout/stderr are None."""
    encoding = "utf-8"

    def write(self, s):
        return len(s) if s else 0

    def flush(self):
        pass

    def isatty(self):
        return False


if sys.stdout is None:
    sys.stdout = _SafeNullStream()
elif hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if sys.stderr is None:
    sys.stderr = _SafeNullStream()
elif hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 3. Configure SSL CA Bundle for frozen .exe HTTPS requests (edge-tts, httpx, faster-whisper)
try:
    import certifi
    ca_path = certifi.where()
    if ca_path and os.path.exists(ca_path):
        os.environ.setdefault("SSL_CERT_FILE", ca_path)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", ca_path)
except Exception:
    pass

# 4. Suppress flashing console windows when invoking ffmpeg/ffprobe on Windows
if os.name == "nt":
    _orig_popen = subprocess.Popen

    class _NoWindowPopen(_orig_popen):
        def __init__(self, *args, **kwargs):
            if "creationflags" not in kwargs:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    subprocess.Popen = _NoWindowPopen

from app.config import get_resource_path
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QFont, QIcon
from PySide6.QtCore import Qt

from app.ui.main_window import MainWindow
from app.ui.styles import DARK_THEME_QSS
from app.logger import setup_logging


def main():
    setup_logging()
    # Clean up any leftover .exe.old backup from a previous in-place self-update
    if getattr(sys, "frozen", False):
        old_bak = Path(sys.executable).resolve().with_name(Path(sys.executable).name + ".old")
        if old_bak.exists():
            try:
                old_bak.unlink()
            except Exception:
                pass

    # Enable high DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("AI Movie Translator & Auto Dubbing")
    app.setOrganizationName("KhmerAIDubStudio")

    # Set application window & taskbar icon
    for icon_rel in ("assets/logo.ico", "assets/logo.png", "assets/logo.svg"):
        icon_path = get_resource_path(icon_rel)
        if icon_path.exists():
            app.setWindowIcon(QIcon(str(icon_path)))
            break

    # Apply Kantumruy Pro or fallback font
    font = QFont("Kantumruy Pro", 10)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    app.setFont(font)

    # Apply modern dark theme QSS
    app.setStyleSheet(DARK_THEME_QSS)

    try:
        window = MainWindow()
        if "--self-test" in sys.argv:
            from PySide6.QtCore import QTimer
            from app.utils import is_ffmpeg_available
            if not is_ffmpeg_available():
                sys.exit(2)
            QTimer.singleShot(800, app.quit)
        else:
            window.show()
        sys.exit(app.exec())
    except Exception as exc:
        import traceback
        err_details = traceback.format_exc()
        QMessageBox.critical(
            None,
            "Application Startup Error",
            f"An unexpected error occurred while starting the application:\n\n{exc}\n\n{err_details}"
        )
        sys.exit(1)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
