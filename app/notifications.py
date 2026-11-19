"""
Secure Notification Manager (Remote Ed25519 Signed Announcements + Local In-App Toasts & Tray).
- Anonymous one-way HTTPS JSON feed fetch with ETag / If-Modified-Since & 8s timeout
- Ed25519 cryptographic signature verification (protects users even if server/repo is compromised)
- Plain-text / Markdown-lite sanitization (zero HTML/script execution) & HTTPS allowlist links
- Singleton NotificationManager with 5s dedupe, toast queue, local persistence & Windows Tray support
"""

import base64
import html
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from PySide6.QtCore import QObject, QThread, QTimer, Signal

from app.config import (
    APP_VERSION,
    DATA_DIR,
    NOTIFICATION_FEED_URL,
    NOTIFICATION_LINK_ALLOWLIST,
    NOTIFICATION_MAX_BYTES,
    NOTIFICATION_MAX_ITEMS,
    NOTIFICATION_PUBLIC_KEY_B64,
    NOTIFICATION_SIG_URL,
)
from app.updater import parse_ver


TYPE_ICONS = {
    "news": "ℹ️",
    "update": "⬆️",
    "warning": "⚠️",
    "promo": "🎁",
    "critical": "‼️",
    "info": "ℹ️",
    "success": "✅",
    "error": "❌",
}


@dataclass
class NotificationItem:
    id: str
    type: str = "news"           # news | update | warning | promo | critical | info | success | error
    title: str = ""
    body: str = ""
    link_url: str = ""
    link_label: str = ""
    publish_at: str = ""
    expire_at: str = ""
    min_app_version: str = ""
    max_app_version: str = ""
    pinned: bool = False
    show_once: bool = True
    is_local: bool = False
    read: bool = False
    dismissed: bool = False
    created_ts: float = field(default_factory=time.time)
    action_label: str = ""
    action_path: str = ""
    action_cb: Optional[Callable[[], None]] = None


@dataclass
class LatestVersionInfo:
    version: str
    notes: str = ""
    url: str = ""


def verify_ed25519_signature(
    payload_bytes: bytes,
    sig_raw_or_b64: bytes,
    pubkey_b64: str = NOTIFICATION_PUBLIC_KEY_B64,
) -> bool:
    """
    Verify Ed25519 signature over raw payload_bytes.
    Supports raw 64-byte signature or ASCII base64-encoded signature.
    Returns True only if valid; False on any tamper, empty signature, or error.
    """
    if not payload_bytes or not sig_raw_or_b64 or not pubkey_b64:
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pub_bytes = base64.b64decode(pubkey_b64.strip())
        pub_key = Ed25519PublicKey.from_public_bytes(pub_bytes)

        sig_bytes = sig_raw_or_b64
        if len(sig_bytes) != 64:
            # Try base64 decode
            sig_text = sig_raw_or_b64.decode("utf-8", errors="ignore").strip()
            sig_bytes = base64.b64decode(sig_text)
        if len(sig_bytes) != 64:
            return False

        pub_key.verify(sig_bytes, payload_bytes)
        return True
    except Exception:
        return False


def sanitize_plain_text(raw: Any) -> str:
    """Strip all HTML/script/style tags completely and return safe plain/Markdown-lite text."""
    if raw is None:
        return ""
    s = str(raw)
    s = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", s, flags=re.IGNORECASE | re.DOTALL)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return s.strip()


def render_markdown_lite_safe(text: str) -> str:
    """
    Safely convert Markdown-lite (**bold**, newlines) into HTML for QLabel
    after escaping all HTML special characters (prevents XSS/injection).
    """
    safe = html.escape(sanitize_plain_text(text))
    safe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)
    safe = safe.replace("\n", "<br>")
    return safe


def is_allowed_link(url: str, allowlist: Tuple[str, ...] = NOTIFICATION_LINK_ALLOWLIST) -> bool:
    """Allow ONLY https:// URLs whose hostname matches the configured domain allowlist."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            return False
        host = parsed.hostname.lower()
        for allowed in allowlist:
            dom = allowed.lower().strip()
            if host == dom or host.endswith("." + dom):
                return True
        return False
    except Exception:
        return False


def parse_iso_utc(dt_str: str) -> Optional[datetime]:
    """Parse ISO-8601 timestamp string into timezone-aware UTC datetime."""
    if not dt_str or not isinstance(dt_str, str):
        return None
    s = dt_str.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _pick_localized(val: Any, lang: str = "km") -> str:
    """Resolve localized string from dict {'km': ..., 'en': ...} with fallback en -> km."""
    if isinstance(val, str):
        return sanitize_plain_text(val)
    if isinstance(val, dict):
        chosen = val.get(lang) or val.get("en") or val.get("km") or ""
        if not chosen:
            for v in val.values():
                if v:
                    chosen = v
                    break
        return sanitize_plain_text(chosen)
    return ""


def _version_in_range(app_ver: str, min_v: str, max_v: str) -> bool:
    try:
        cur = parse_ver(app_ver)
        if min_v and cur < parse_ver(min_v):
            return False
        if max_v and cur > parse_ver(max_v):
            return False
        return True
    except Exception:
        return True


def parse_and_filter_feed(
    raw_bytes: bytes,
    app_version: str = APP_VERSION,
    lang: str = "km",
    now_utc: Optional[datetime] = None,
    allowlist: Tuple[str, ...] = NOTIFICATION_LINK_ALLOWLIST,
) -> Tuple[List[NotificationItem], Optional[LatestVersionInfo]]:
    """
    Safely parse JSON feed (max 256KB, max 50 items), filter by time window,
    app version bounds, sanitize text, validate HTTPS allowlisted links, and sort.
    """
    if not raw_bytes or len(raw_bytes) > NOTIFICATION_MAX_BYTES:
        return [], None

    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except Exception:
        return [], None

    if not isinstance(data, dict):
        return [], None

    now = now_utc or datetime.now(timezone.utc)
    raw_items = data.get("items", [])
    if not isinstance(raw_items, list):
        raw_items = []
    raw_items = raw_items[:NOTIFICATION_MAX_ITEMS]

    items: List[NotificationItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item_id = sanitize_plain_text(raw.get("id", ""))
        if not item_id:
            continue

        pub_str = str(raw.get("publish_at") or "").strip()
        exp_str = str(raw.get("expire_at") or "").strip()
        pub_dt = parse_iso_utc(pub_str)
        exp_dt = parse_iso_utc(exp_str)
        if pub_dt and now < pub_dt:
            continue
        if exp_dt and now >= exp_dt:
            continue

        min_v = str(raw.get("min_app_version") or "").strip()
        max_v = str(raw.get("max_app_version") or "").strip()
        if not _version_in_range(app_version, min_v, max_v):
            continue

        n_type = str(raw.get("type") or "news").lower().strip()
        if n_type not in ("news", "update", "warning", "promo", "critical"):
            n_type = "news"

        title = _pick_localized(raw.get("title"), lang=lang)
        body = _pick_localized(raw.get("body"), lang=lang)
        if not title and not body:
            continue

        link_url = ""
        link_label = ""
        link_obj = raw.get("link")
        if isinstance(link_obj, dict):
            cand_url = str(link_obj.get("url") or "").strip()
            if is_allowed_link(cand_url, allowlist=allowlist):
                link_url = cand_url
                link_label = _pick_localized(link_obj.get("label"), lang=lang) or "បើកតំណ (Open Link)"

        items.append(
            NotificationItem(
                id=item_id,
                type=n_type,
                title=title,
                body=body,
                link_url=link_url,
                link_label=link_label,
                publish_at=pub_str,
                expire_at=exp_str,
                min_app_version=min_v,
                max_app_version=max_v,
                pinned=bool(raw.get("pinned", False)),
                show_once=bool(raw.get("show_once", True)),
                is_local=False,
                created_ts=(pub_dt.timestamp() if pub_dt else time.time()),
            )
        )

    # Sort pinned first, then newest publish_at descending
    items.sort(key=lambda x: (1 if x.pinned else 0, x.created_ts), reverse=True)

    # Parse latest_version announcement
    latest_info: Optional[LatestVersionInfo] = None
    lv = data.get("latest_version")
    if isinstance(lv, dict):
        ver_str = sanitize_plain_text(lv.get("version", ""))
        notes_str = _pick_localized(lv.get("notes"), lang=lang)
        url_str = str(lv.get("url") or "").strip()
        if url_str and not is_allowed_link(url_str, allowlist=allowlist):
            url_str = ""
        if ver_str:
            latest_info = LatestVersionInfo(version=ver_str, notes=notes_str, url=url_str)

    return items, latest_info


class FetchNotificationsWorker(QThread):
    """Background worker fetching signed notifications.json + notifications.sig over HTTPS."""
    fetched = Signal(bytes, str, str)  # raw_json_bytes, new_etag, new_last_modified
    not_modified = Signal()
    failed_silently = Signal(str)

    def __init__(
        self,
        feed_url: str = NOTIFICATION_FEED_URL,
        sig_url: str = NOTIFICATION_SIG_URL,
        etag: str = "",
        last_modified: str = "",
        pubkey_b64: str = NOTIFICATION_PUBLIC_KEY_B64,
    ):
        super().__init__()
        self.feed_url = feed_url
        self.sig_url = sig_url
        self.etag = etag
        self.last_modified = last_modified
        self.pubkey_b64 = pubkey_b64

    def run(self):
        if not self.feed_url.startswith("https://") or not self.sig_url.startswith("https://"):
            self.failed_silently.emit("Non-HTTPS notification URL blocked")
            return

        req_headers = {"User-Agent": f"AI-Movie-Dubbing-Studio/{APP_VERSION}"}
        if self.etag:
            req_headers["If-None-Match"] = self.etag
        if self.last_modified:
            req_headers["If-Modified-Since"] = self.last_modified

        for attempt in range(2):
            try:
                with httpx.Client(timeout=8.0, follow_redirects=False) as client:
                    resp = client.get(self.feed_url, headers=req_headers)
                    if resp.status_code == 304:
                        self.not_modified.emit()
                        return
                    if resp.status_code != 200:
                        self.failed_silently.emit(f"HTTP {resp.status_code}")
                        return
                    raw_bytes = resp.content
                    if len(raw_bytes) > NOTIFICATION_MAX_BYTES:
                        self.failed_silently.emit("Feed exceeds 256KB limit")
                        return

                    sig_resp = client.get(self.sig_url, headers={"User-Agent": f"AI-Movie-Dubbing-Studio/{APP_VERSION}"})
                    if sig_resp.status_code != 200:
                        self.failed_silently.emit(f"Signature fetch HTTP {sig_resp.status_code}")
                        return

                    if not verify_ed25519_signature(raw_bytes, sig_resp.content, self.pubkey_b64):
                        print("[Notifications] Warning: Invalid Ed25519 signature — remote feed rejected.")
                        self.failed_silently.emit("Invalid signature")
                        return

                    new_etag = resp.headers.get("etag", "")
                    new_lm = resp.headers.get("last-modified", "")
                    self.fetched.emit(raw_bytes, new_etag, new_lm)
                    return
            except Exception as e:
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                self.failed_silently.emit(str(e))
                return


class NotificationManager(QObject):
    """
    Singleton managing both remote signed developer notifications and local in-app toasts/tray alerts.
    Features:
      - 5-second toast deduplication
      - Sequential toast queue (no overlapping popups)
      - Persistent read/dismissed/snooze JSON state in DATA_DIR
      - Periodic polling timer (1/6/24 hours)
    """
    _instance: Optional["NotificationManager"] = None

    items_changed = Signal()
    toast_requested = Signal(object)         # NotificationItem
    critical_requested = Signal(object)      # NotificationItem
    update_banner_requested = Signal(object) # LatestVersionInfo
    tray_requested = Signal(str, str)        # title, message

    def __init__(self, state_dir: Optional[Path] = None, app_version: str = APP_VERSION, lang: str = "km"):
        super().__init__()
        self.state_dir = Path(state_dir) if state_dir else DATA_DIR
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.state_dir / "notifications_cache.json"
        self.state_file = self.state_dir / "notifications_state.json"

        self.app_version = app_version
        self.lang = lang
        self.feed_url = NOTIFICATION_FEED_URL
        self.sig_url = NOTIFICATION_SIG_URL

        self.remote_items: List[NotificationItem] = []
        self.local_items: List[NotificationItem] = []
        self.latest_version: Optional[LatestVersionInfo] = None

        self._etag: str = ""
        self._last_modified: str = ""
        self._read_ids: set = set()
        self._dismissed_ids: set = set()
        self._shown_once_ids: set = set()
        self._update_snooze_until: float = 0.0

        self._recent_toasts: Dict[Tuple[str, str, str], float] = {}
        self._toast_queue: List[NotificationItem] = []
        self._toast_active: bool = False
        self._fetch_worker: Optional[FetchNotificationsWorker] = None

        self.notify_enabled: bool = True
        self.show_toast_enabled: bool = True
        self.tray_enabled: bool = True
        self.sound_enabled: bool = True

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(lambda: self.poll_remote_async(force=True))

        self._load_state_and_cache()

    @classmethod
    def instance(cls, state_dir: Optional[Path] = None) -> "NotificationManager":
        if cls._instance is None:
            cls._instance = cls(state_dir=state_dir)
        return cls._instance

    def configure(
        self,
        enabled: bool = True,
        show_toast: bool = True,
        tray_enabled: bool = True,
        interval_hours: int = 6,
        sound_enabled: bool = True,
        lang: str = "km",
    ):
        self.notify_enabled = enabled
        self.show_toast_enabled = show_toast
        self.tray_enabled = tray_enabled
        self.sound_enabled = sound_enabled
        self.lang = lang

        self._poll_timer.stop()
        if enabled and interval_hours > 0:
            self._poll_timer.start(int(interval_hours * 3600 * 1000))

    def _load_state_and_cache(self):
        if self.state_file.exists():
            try:
                st = json.loads(self.state_file.read_text(encoding="utf-8"))
                self._read_ids = set(st.get("read_ids", []))
                self._dismissed_ids = set(st.get("dismissed_ids", []))
                self._shown_once_ids = set(st.get("shown_once_ids", []))
                self._update_snooze_until = float(st.get("update_snooze_until", 0.0))
                self._etag = str(st.get("etag", ""))
                self._last_modified = str(st.get("last_modified", ""))
            except Exception:
                pass

        if self.cache_file.exists():
            try:
                raw_bytes = self.cache_file.read_bytes()
                items, latest = parse_and_filter_feed(raw_bytes, app_version=self.app_version, lang=self.lang)
                self._apply_parsed_remote(items, latest, emit_alerts=False)
            except Exception:
                pass

    def _save_state(self):
        try:
            payload = {
                "read_ids": sorted(self._read_ids),
                "dismissed_ids": sorted(self._dismissed_ids),
                "shown_once_ids": sorted(self._shown_once_ids),
                "update_snooze_until": self._update_snooze_until,
                "etag": self._etag,
                "last_modified": self._last_modified,
            }
            self.state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[Notifications] State save warning: {e}")

    def poll_remote_async(self, force: bool = False):
        if not self.notify_enabled and not force:
            return
        if self._fetch_worker and self._fetch_worker.isRunning():
            return
        etag = "" if force else self._etag
        lm = "" if force else self._last_modified
        worker = FetchNotificationsWorker(
            feed_url=self.feed_url,
            sig_url=self.sig_url,
            etag=etag,
            last_modified=lm,
        )
        worker.fetched.connect(self._on_remote_fetched)
        worker.not_modified.connect(self._on_remote_not_modified)
        worker.failed_silently.connect(lambda err: print(f"[Notifications] Offline/skip: {err}"))
        self._fetch_worker = worker
        worker.start()

    def _on_remote_not_modified(self):
        # Re-filter existing cache (time/version) silently
        if self.cache_file.exists():
            items, latest = parse_and_filter_feed(self.cache_file.read_bytes(), app_version=self.app_version, lang=self.lang)
            self._apply_parsed_remote(items, latest, emit_alerts=True)

    def _on_remote_fetched(self, raw_bytes: bytes, new_etag: str, new_lm: str):
        try:
            self.cache_file.write_bytes(raw_bytes)
        except Exception:
            pass
        self._etag = new_etag
        self._last_modified = new_lm
        self._save_state()

        items, latest = parse_and_filter_feed(raw_bytes, app_version=self.app_version, lang=self.lang)
        self._apply_parsed_remote(items, latest, emit_alerts=True)

    def _apply_parsed_remote(
        self,
        items: List[NotificationItem],
        latest: Optional[LatestVersionInfo],
        emit_alerts: bool = True,
    ):
        for it in items:
            it.read = it.id in self._read_ids
            it.dismissed = it.id in self._dismissed_ids
        self.remote_items = items
        self.latest_version = latest
        self.items_changed.emit()

        if emit_alerts and self.notify_enabled:
            # Check update banner
            if self.is_update_available() and time.time() >= self._update_snooze_until:
                self.update_banner_requested.emit(self.latest_version)

            # Emit critical modal or toast for unseen remote notifications
            state_dirty = False
            for it in self.remote_items:
                if it.dismissed or it.read:
                    continue
                if it.show_once and it.id in self._shown_once_ids:
                    continue
                self._shown_once_ids.add(it.id)
                state_dirty = True

                if it.type == "critical":
                    self.critical_requested.emit(it)
                elif self.show_toast_enabled:
                    self._enqueue_toast(it)

            if state_dirty:
                self._save_state()

    def is_update_available(self) -> bool:
        if not self.latest_version or not self.latest_version.version:
            return False
        try:
            return parse_ver(self.latest_version.version) > parse_ver(self.app_version)
        except Exception:
            return False

    def snooze_update_24h(self):
        self._update_snooze_until = time.time() + (24 * 3600)
        self._save_state()

    def notify(
        self,
        level: str,
        title: str,
        message: str,
        action_label: str = "",
        action_path: str = "",
        persist: bool = False,
        action_cb: Optional[Callable[[], None]] = None,
        tray: bool = False,
    ) -> bool:
        """
        Dispatch an in-app notification from any module.
        Deduplicates identical (level, title, message) within 5 seconds.
        Returns True if dispatched, False if deduplicated.
        """
        clean_title = sanitize_plain_text(title)
        clean_msg = sanitize_plain_text(message)
        key = (level.lower(), clean_title, clean_msg)
        now = time.time()

        # Dedupe within 5 seconds
        last_t = self._recent_toasts.get(key)
        if last_t is not None and (now - last_t) < 5.0:
            return False
        self._recent_toasts[key] = now

        item = NotificationItem(
            id=f"local-{uuid.uuid4().hex[:10]}",
            type=level.lower(),
            title=clean_title,
            body=clean_msg,
            is_local=True,
            read=not persist,
            created_ts=now,
            action_label=action_label,
            action_path=action_path,
            action_cb=action_cb,
        )

        if persist:
            self.local_items.insert(0, item)
            self.local_items = self.local_items[:30]
            self.items_changed.emit()

        if item.type == "critical":
            self.critical_requested.emit(item)
        elif self.show_toast_enabled:
            self._enqueue_toast(item)

        if tray and self.tray_enabled:
            self.tray_requested.emit(clean_title, clean_msg)

        return True

    def _enqueue_toast(self, item: NotificationItem):
        self._toast_queue.append(item)
        if not self._toast_active:
            self._dispatch_next_toast()

    def _dispatch_next_toast(self):
        if not self._toast_queue:
            self._toast_active = False
            return
        self._toast_active = True
        nxt = self._toast_queue.pop(0)
        self.toast_requested.emit(nxt)

    def notify_toast_finished(self):
        """Called by UI when current toast closes, triggering next queued toast."""
        self._toast_active = False
        self._dispatch_next_toast()

    def get_visible_items(self) -> List[NotificationItem]:
        combined = [
            it for it in (self.local_items + self.remote_items)
            if not it.dismissed and it.id not in self._dismissed_ids
        ]
        combined.sort(key=lambda x: (1 if x.pinned else 0, x.created_ts), reverse=True)
        return combined

    def unread_count(self) -> int:
        return sum(1 for it in self.get_visible_items() if not it.read)

    def mark_read(self, item_id: str):
        for it in self.local_items + self.remote_items:
            if it.id == item_id:
                it.read = True
        self._read_ids.add(item_id)
        self._save_state()
        self.items_changed.emit()

    def mark_all_read(self):
        for it in self.get_visible_items():
            it.read = True
            self._read_ids.add(it.id)
        self._save_state()
        self.items_changed.emit()

    def dismiss_item(self, item_id: str):
        for it in self.local_items + self.remote_items:
            if it.id == item_id:
                it.dismissed = True
                it.read = True
        self._dismissed_ids.add(item_id)
        self._read_ids.add(item_id)
        self._save_state()
        self.items_changed.emit()
