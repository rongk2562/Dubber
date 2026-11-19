"""
Developer Publisher Tool for Signed App Notifications (Ed25519).
Usage:
  1. Generate keypair (first time only):
       python tools/publish_notice.py --gen-keys
  2. Interactive announcement publisher:
       python tools/publish_notice.py
  3. Re-sign existing notifications.json:
       python tools/publish_notice.py --sign-only
"""

import argparse
import base64
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
FEED_PATH = ROOT_DIR / "notifications.json"
SIG_PATH = ROOT_DIR / "notifications.sig"
PRIV_KEY_PATH = ROOT_DIR / "tools" / "keys" / "dev_private.pem"


def generate_keypair():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()

    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    pub_b64 = base64.b64encode(pub_raw).decode("ascii")

    PRIV_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    PRIV_KEY_PATH.write_bytes(priv_pem)
    print(f"[+] Saved private key (KEEP SECRET, never commit): {PRIV_KEY_PATH}")
    print(f"[+] Paste this Public Key into app/config.py (NOTIFICATION_PUBLIC_KEY_B64):")
    print(f"    \"{pub_b64}\"")


def sign_feed() -> bool:
    from cryptography.hazmat.primitives import serialization
    if not PRIV_KEY_PATH.exists():
        print(f"[!] Private key not found at {PRIV_KEY_PATH}. Run --gen-keys first.")
        return False
    if not FEED_PATH.exists():
        print(f"[!] Feed file not found at {FEED_PATH}.")
        return False

    priv = serialization.load_pem_private_key(PRIV_KEY_PATH.read_bytes(), password=None)
    payload = FEED_PATH.read_bytes()
    if len(payload) > 262144:
        print(f"[!] Warning: Feed size {len(payload)} bytes exceeds 256KB limit!")
    sig_raw = priv.sign(payload)
    SIG_PATH.write_text(base64.b64encode(sig_raw).decode("ascii") + "\n", encoding="utf-8")
    print(f"[+] Signed {FEED_PATH.name} ({len(payload)} bytes) -> {SIG_PATH.name}")

    # Self-verify against embedded public key in app.config
    sys.path.insert(0, str(ROOT_DIR))
    from app.notifications import verify_ed25519_signature
    from app.config import NOTIFICATION_PUBLIC_KEY_B64
    ok = verify_ed25519_signature(payload, SIG_PATH.read_bytes(), NOTIFICATION_PUBLIC_KEY_B64)
    print(f"[+] Signature verification against app/config.py public key: {'PASSED' if ok else 'FAILED'}")
    return ok


def interactive_publish(auto_push: bool = False):
    if FEED_PATH.exists():
        data = json.loads(FEED_PATH.read_text(encoding="utf-8"))
    else:
        data = {"version": 1, "items": [], "latest_version": {}}

    print("=== បង្កើតដំណឹងថ្មី (Publish Notification) ===")
    n_type = input("ប្រភេទ (news / update / warning / promo / critical) [news]: ").strip().lower() or "news"
    title_km = input("ចំណងជើង (ខ្មែរ): ").strip()
    title_en = input("Title (English) [optional]: ").strip() or title_km
    body_km = input("ខ្លឹមសារ (ខ្មែរ): ").strip()
    body_en = input("Body (English) [optional]: ").strip() or body_km
    days_valid = int(input("ផុតកំណត់ក្នុងប៉ុន្មានថ្ងៃ (Days valid) [30]: ").strip() or "30")
    min_ver = input("Min App Version [1.0.0]: ").strip() or "1.0.0"
    max_ver = input("Max App Version [blank = all]: ").strip()
    link_url = input("Link HTTPS (optional, domain in allowlist): ").strip()
    link_lbl = input("Link Label (km) [មើលលម្អិត]: ").strip() or "មើលលម្អិត"
    pinned = input("Pin លើគេ? (y/N): ").strip().lower() == "y"

    now_utc = datetime.now(timezone.utc)
    exp_utc = now_utc + timedelta(days=days_valid)
    slug = now_utc.strftime("%Y-%m-%d-%H%M")
    new_item = {
        "id": f"{slug}-{n_type}",
        "type": n_type,
        "title": {"km": title_km, "en": title_en},
        "body": {"km": body_km, "en": body_en},
        "publish_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expire_at": exp_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "min_app_version": min_ver,
        "max_app_version": max_ver,
        "pinned": pinned,
        "show_once": True,
    }
    if link_url:
        new_item["link"] = {"url": link_url, "label": {"km": link_lbl, "en": "Open Link"}}

    items = data.get("items", [])
    items.insert(0, new_item)
    data["items"] = items[:50]

    FEED_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not sign_feed():
        sys.exit(1)

    if auto_push or input("Commit & Push notifications.json + notifications.sig to GitHub now? (y/N): ").strip().lower() == "y":
        subprocess.run(["git", "add", "notifications.json", "notifications.sig"], cwd=str(ROOT_DIR), check=True)
        subprocess.run(["git", "commit", "-m", f"Notice: {new_item['id']}"], cwd=str(ROOT_DIR), check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=str(ROOT_DIR), check=True)
        print("[+] Pushed notice to GitHub!")


def main():
    parser = argparse.ArgumentParser(description="Developer Signed Notification Publisher")
    parser.add_argument("--gen-keys", action="store_true", help="Generate new Ed25519 keypair")
    parser.add_argument("--sign-only", action="store_true", help="Re-sign notifications.json")
    parser.add_argument("--push", action="store_true", help="Auto git commit & push after publishing")
    args = parser.parse_args()

    if args.gen_keys:
        generate_keypair()
    elif args.sign_only:
        ok = sign_feed()
        sys.exit(0 if ok else 1)
    else:
        interactive_publish(auto_push=args.push)


if __name__ == "__main__":
    main()
