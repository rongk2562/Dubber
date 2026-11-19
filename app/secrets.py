"""
Secure OS Credential Storage for API Keys.
Primary: Python keyring (Windows Credential Manager).
Fallback: Windows DPAPI (CryptProtectData / CryptUnprotectData) encrypted blob in DATA_DIR.
Never stores API keys in plaintext JSON or logs them.
"""

import base64
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
from typing import Optional

from app.config import DATA_DIR

_SERVICE_NAME = "KhmerAIDubStudio"
_DPAPI_DIR = DATA_DIR / "secrets"


def _dpapi_encrypt(plaintext: str) -> bytes:
    if os.name != "nt":
        raise RuntimeError("DPAPI only available on Windows")

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    raw = plaintext.encode("utf-8")
    in_blob = DATA_BLOB(len(raw), ctypes.create_string_buffer(raw, len(raw)))
    out_blob = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob), "KhmerAIDubStudioSecret", None, None, None, 0x01, ctypes.byref(out_blob)
    ):
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_decrypt(blob: bytes) -> str:
    if os.name != "nt":
        raise RuntimeError("DPAPI only available on Windows")

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    in_blob = DATA_BLOB(len(blob), ctypes.create_string_buffer(blob, len(blob)))
    out_blob = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0x01, ctypes.byref(out_blob)
    ):
        raise OSError("CryptUnprotectData failed")
    try:
        raw = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return raw.decode("utf-8")
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_file(key_name: str) -> Path:
    _DPAPI_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in key_name)
    return _DPAPI_DIR / f"{safe}.dpapi"


def set_secret(key_name: str, value: str) -> bool:
    """Store secret securely in OS credential manager (keyring) or Windows DPAPI."""
    val = (value or "").strip()
    if not val:
        return delete_secret(key_name)

    # 1. Try OS keyring (Windows Credential Manager)
    try:
        import keyring
        keyring.set_password(_SERVICE_NAME, key_name, val)
        # Also remove any old fallback file
        fp = _dpapi_file(key_name)
        if fp.exists():
            fp.unlink(missing_ok=True)
        return True
    except Exception:
        pass

    # 2. Windows DPAPI encrypted local blob fallback
    try:
        enc = _dpapi_encrypt(val)
        _dpapi_file(key_name).write_bytes(base64.b64encode(enc))
        return True
    except Exception:
        return False


def get_secret(key_name: str) -> str:
    """Retrieve secret from OS credential manager or Windows DPAPI fallback."""
    try:
        import keyring
        val = keyring.get_password(_SERVICE_NAME, key_name)
        if val:
            return val.strip()
    except Exception:
        pass

    fp = _dpapi_file(key_name)
    if fp.exists():
        try:
            raw = base64.b64decode(fp.read_bytes())
            return _dpapi_decrypt(raw).strip()
        except Exception:
            return ""
    return ""


def delete_secret(key_name: str) -> bool:
    ok = False
    try:
        import keyring
        keyring.delete_password(_SERVICE_NAME, key_name)
        ok = True
    except Exception:
        pass
    fp = _dpapi_file(key_name)
    if fp.exists():
        try:
            fp.unlink()
            ok = True
        except Exception:
            pass
    return ok


def get_openai_api_key() -> str:
    return get_secret("openai_api_key")


def set_openai_api_key(api_key: str) -> bool:
    return set_secret("openai_api_key", api_key)


def delete_openai_api_key() -> bool:
    return delete_secret("openai_api_key")
