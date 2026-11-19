import base64
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from app.notifications import (
    NotificationManager,
    parse_and_filter_feed,
    verify_ed25519_signature,
    sanitize_plain_text,
    render_markdown_lite_safe,
    is_allowed_link,
)


class TestNotificationsSystem(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if QCoreApplication.instance() is None:
            cls._app = QApplication([])

    def test_ed25519_signature_verification_and_tamper_detection(self):
        priv = Ed25519PrivateKey.generate()
        pub_raw = priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        pub_b64 = base64.b64encode(pub_raw).decode("ascii")

        payload = b'{"version": 1, "items": []}'
        sig_b64 = base64.b64encode(priv.sign(payload)).decode("ascii").encode("ascii")

        self.assertTrue(verify_ed25519_signature(payload, sig_b64, pub_b64))
        # Tampered payload must fail
        self.assertFalse(verify_ed25519_signature(b'{"version": 1, "items": [{"id": "hack"}]}', sig_b64, pub_b64))
        # Invalid signature must fail
        self.assertFalse(verify_ed25519_signature(payload, b"invalid_sig", pub_b64))

    def test_parse_and_filter_feed_time_version_xss_and_links(self):
        feed = {
            "version": 1,
            "items": [
                {
                    "id": "valid-notice",
                    "type": "warning",
                    "title": {"km": "<b>សំខាន់</b><script>alert(1)</script>", "en": "Important"},
                    "body": {"km": "សារធម្មតា **ដិត**", "en": "Plain text"},
                    "link": {"url": "https://github.com/rongk2562/Dubber/releases", "label": {"km": "មើល"}},
                    "publish_at": "2026-11-01T00:00:00Z",
                    "expire_at": "2026-12-01T00:00:00Z",
                    "min_app_version": "1.0.0",
                    "max_app_version": "1.3.1",
                },
                {
                    "id": "expired-notice",
                    "type": "news",
                    "title": {"km": "ហួសកំណត់"},
                    "body": {"km": "Expired"},
                    "publish_at": "2026-01-01T00:00:00Z",
                    "expire_at": "2026-10-01T00:00:00Z",
                },
                {
                    "id": "wrong-version-notice",
                    "type": "update",
                    "title": {"km": "សម្រាប់តែ 2.0"},
                    "body": {"km": "Only >= 2.0"},
                    "min_app_version": "2.0.0",
                },
                {
                    "id": "unsafe-link-notice",
                    "type": "promo",
                    "title": {"km": "Unsafe link"},
                    "body": {"km": "Should strip non-allowlist link"},
                    "link": {"url": "http://evil-site.example.com/payload.exe"},
                },
            ],
            "latest_version": {
                "version": "1.3.2",
                "notes": {"km": "កំណែថ្មី 1.3.2"},
                "url": "https://github.com/rongk2562/Dubber/releases",
            },
        }
        raw = json.dumps(feed).encode("utf-8")
        now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
        items, latest = parse_and_filter_feed(raw, app_version="1.3.1", lang="km", now_utc=now)

        ids = [it.id for it in items]
        self.assertIn("valid-notice", ids)
        self.assertIn("unsafe-link-notice", ids)
        self.assertNotIn("expired-notice", ids)
        self.assertNotIn("wrong-version-notice", ids)

        valid = next(it for it in items if it.id == "valid-notice")
        self.assertNotIn("<script>", valid.title)
        self.assertNotIn("<b>", sanitize_plain_text(valid.title))
        self.assertEqual(valid.link_url, "https://github.com/rongk2562/Dubber/releases")
        self.assertIn("<b>ដិត</b>", render_markdown_lite_safe(valid.body))

        unsafe = next(it for it in items if it.id == "unsafe-link-notice")
        self.assertEqual(unsafe.link_url, "")  # Blocked untrusted http link

        self.assertIsNotNone(latest)
        self.assertEqual(latest.version, "1.3.2")
        self.assertFalse(is_allowed_link("http://github.com/test"))
        self.assertFalse(is_allowed_link("https://evil.com/test"))
        self.assertTrue(is_allowed_link("https://raw.githubusercontent.com/rongk2562/Dubber/main/notifications.json"))

    def test_oversized_feed_rejected(self):
        oversized = b"x" * (262144 + 10)
        items, latest = parse_and_filter_feed(oversized, app_version="1.3.1", lang="km")
        self.assertEqual(items, [])
        self.assertIsNone(latest)

    def test_manager_dedupe_read_dismiss_and_version_compare(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = NotificationManager(state_dir=Path(tmp), app_version="1.3.1", lang="km")

            # Dedupe check within 5s
            first = mgr.notify("success", "Export រួចរាល់", "Saved out.mp4", persist=True)
            second = mgr.notify("success", "Export រួចរាល់", "Saved out.mp4", persist=True)
            third = mgr.notify("success", "Export រួចរាល់", "Saved out2.mp4", persist=True)
            self.assertTrue(first)
            self.assertFalse(second)
            self.assertTrue(third)
            self.assertEqual(mgr.unread_count(), 2)

            # Mark all read -> unread becomes 0
            mgr.mark_all_read()
            self.assertEqual(mgr.unread_count(), 0)

            # Remote feed apply + version check + dismiss persistence
            feed = {
                "version": 1,
                "items": [
                    {
                        "id": "r1",
                        "type": "news",
                        "title": {"km": "ដំណឹងទី១"},
                        "body": {"km": "ខ្លឹមសារ"},
                        "publish_at": "2026-11-10T00:00:00Z",
                    }
                ],
                "latest_version": {"version": "1.3.2", "notes": {"km": "New"}, "url": "https://github.com/rongk2562/Dubber/releases"},
            }
            items, latest = parse_and_filter_feed(json.dumps(feed).encode("utf-8"), app_version="1.3.1", lang="km")
            mgr._apply_parsed_remote(items, latest, emit_alerts=False)
            self.assertTrue(mgr.is_update_available())
            self.assertEqual(mgr.unread_count(), 1)

            mgr.dismiss_item("r1")
            self.assertEqual(mgr.unread_count(), 0)

            # Verify persisted dismissed state survives reload
            mgr2 = NotificationManager(state_dir=Path(tmp), app_version="1.3.1", lang="km")
            mgr2._apply_parsed_remote(items, latest, emit_alerts=False)
            self.assertEqual(len(mgr2.get_visible_items()), 0)


if __name__ == "__main__":
    unittest.main()
