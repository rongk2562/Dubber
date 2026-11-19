"""
Application File Logger with Automatic Secret Redaction.
Writes rotating UTF-8 logs to DATA_DIR/logs/app.log.
"""

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from app.config import DATA_DIR


_SECRET_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9_\-]{8,})"),
    re.compile(r"(AIza[0-9A-Za-z\-_]{10,})"),
    re.compile(r"(Bearer\s+[A-Za-z0-9\-_\.]{8,})", re.IGNORECASE),
    re.compile(r"(x-api-key[\"']?\s*[:=]\s*[\"']?)([^\"'\s,}]+)", re.IGNORECASE),
]


def redact_secrets(text: str) -> str:
    """Mask API keys or auth tokens before writing any message to disk or UI."""
    if not text:
        return ""
    res = str(text)
    res = _SECRET_PATTERNS[0].sub("sk-***REDACTED***", res)
    res = _SECRET_PATTERNS[1].sub("AIza***REDACTED***", res)
    res = _SECRET_PATTERNS[2].sub("Bearer ***REDACTED***", res)
    res = _SECRET_PATTERNS[3].sub(r"\1***REDACTED***", res)
    return res


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            record.msg = redact_secrets(msg)
            record.args = ()
        except Exception:
            pass
        return True


_LOG_INITIALIZED = False


def get_log_file_path() -> Path:
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "app.log"


def setup_logging() -> logging.Logger:
    global _LOG_INITIALIZED
    logger = logging.getLogger("khmer_dub_studio")
    if _LOG_INITIALIZED:
        return logger

    logger.setLevel(logging.INFO)
    log_file = get_log_file_path()
    try:
        handler = RotatingFileHandler(
            str(log_file), maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        handler.setFormatter(fmt)
        handler.addFilter(RedactingFilter())
        logger.addHandler(handler)
    except Exception:
        pass

    _LOG_INITIALIZED = True
    return logger


def get_logger(name: str = "app") -> logging.Logger:
    setup_logging()
    return logging.getLogger(f"khmer_dub_studio.{name}")
