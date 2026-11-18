"""
Application Versioning & Auto-Update Engine
Handles remote version checking, downloading, and in-place replacement for frozen Windows .exe
"""

import os
import sys
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Callable
import httpx
from PySide6.QtCore import QThread, Signal

from app.config import APP_NAME, APP_VERSION, APP_BUILD_DATE, DEFAULT_UPDATE_URL, LOCAL_UPDATE_URL, TEMP_DIR

try:
    from packaging import version as pkg_version
except ImportError:
    pkg_version = None


@dataclass
class UpdateInfo:
    current_version: str
    latest_version: str
    release_date: str
    download_url: str
    changelog_km: str
    changelog_en: str
    is_update_available: bool
    mandatory: bool = False

    @property
    def changelog(self) -> str:
        return self.changelog_km if self.changelog_km else self.changelog_en


def parse_ver(v_str: str):
    """Normalize and parse version string into a comparable object."""
    clean = v_str.strip().lstrip("vV").strip()
    if pkg_version:
        try:
            return pkg_version.parse(clean)
        except Exception:
            pass
    # Fallback to integer tuple comparison
    parts = []
    for chunk in clean.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer_version(remote_v: str, current_v: str = APP_VERSION) -> bool:
    """Return True if remote_v is strictly newer than current_v."""
    try:
        return parse_ver(remote_v) > parse_ver(current_v)
    except Exception:
        return False


def check_for_updates(manifest_url: Optional[str] = None) -> Tuple[bool, Optional[UpdateInfo], str]:
    """
    Fetch remote update manifest JSON (GitHub or Local/LAN Server) and compare against APP_VERSION.
    Returns: (is_update_available, update_info_obj, status_message)
    """
    candidate_urls = []
    if manifest_url:
        candidate_urls.append(manifest_url)
    for fallback_u in (DEFAULT_UPDATE_URL, LOCAL_UPDATE_URL):
        if fallback_u and fallback_u not in candidate_urls:
            candidate_urls.append(fallback_u)

    headers = {
        "User-Agent": "AI-Movie-Dubbing-Studio/1.1",
        "Bypass-Tunnel-Reminder": "true"
    }
    last_err = "Update check failed"
    for url in candidate_urls:
        try:
            with httpx.Client(timeout=8.0, follow_redirects=True, headers=headers) as client:
                resp = client.get(url)
                if resp.status_code != 200:
                    last_err = f"HTTP Error {resp.status_code} ({url})"
                    continue

                data = resp.json()
            # Support wrapped JSON responses from Mongoose/Express (e.g. { data: {...} } or [ {...} ])
            if isinstance(data, list) and len(data) > 0:
                data = data[0]
            elif isinstance(data, dict):
                if "data" in data and isinstance(data["data"], dict):
                    data = data["data"]
                elif "result" in data and isinstance(data["result"], dict):
                    data = data["result"]

            remote_version = str(data.get("version") or data.get("latest_version") or "").strip()
            if not remote_version:
                return False, None, "Invalid manifest: missing 'version' field"

            download_url = str(data.get("download_url") or data.get("downloadUrl") or data.get("url") or "").strip()
            release_date = str(data.get("release_date") or data.get("releaseDate") or "").strip()
            changelog_km = str(data.get("changelog_km") or data.get("changelogKm") or data.get("changelog") or "").strip()
            changelog_en = str(data.get("changelog_en") or data.get("changelogEn") or data.get("notes") or "").strip()

            # Smart URL resolution for LAN / Remote Internet deployment:
            # If server returned a relative path or a 'localhost' URL, adapt it to the remote server's actual host
            if download_url:
                from urllib.parse import urljoin, urlparse
                if download_url.startswith("/"):
                    download_url = urljoin(url, download_url)
                else:
                    parsed_base = urlparse(url)
                    parsed_dl = urlparse(download_url)
                    if parsed_dl.hostname in ("localhost", "127.0.0.1") and parsed_base.hostname not in ("localhost", "127.0.0.1"):
                        download_url = parsed_dl._replace(scheme=parsed_base.scheme, netloc=parsed_base.netloc).geturl()

            has_update = is_newer_version(remote_version, APP_VERSION)
            info = UpdateInfo(
                current_version=APP_VERSION,
                latest_version=remote_version,
                release_date=release_date,
                download_url=download_url,
                changelog_km=changelog_km,
                changelog_en=changelog_en,
                is_update_available=has_update,
                mandatory=bool(data.get("mandatory", False))
            )

            msg = "New version available" if has_update else "Application is up to date"
            return has_update, info, msg
        except Exception as e:
            last_err = f"Update check failed ({url}): {e}"
            continue
    return False, None, last_err


def download_update_file(
    download_url: str,
    dest_path: str,
    progress_cb: Optional[Callable[[int, str], None]] = None
) -> bool:
    """Download update file with streaming progress callback."""
    p_dest = Path(dest_path)
    p_dest.parent.mkdir(parents=True, exist_ok=True)
    temp_part = dest_path + ".part"

    headers = {
        "User-Agent": "AI-Movie-Dubbing-Studio/1.0",
        "Bypass-Tunnel-Reminder": "true"
    }
    try:
        with httpx.Client(timeout=45.0, follow_redirects=True, headers=headers) as client:
            with client.stream("GET", download_url) as response:
                if response.status_code != 200:
                    return False

                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0

                with open(temp_part, "wb") as f:
                    for chunk in response.iter_bytes(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_cb and total_size > 0:
                                pct = int((downloaded / total_size) * 100)
                                mb_down = downloaded / (1024 * 1024)
                                mb_total = total_size / (1024 * 1024)
                                progress_cb(pct, f"ទាញយកបាន {mb_down:.1f} MB / {mb_total:.1f} MB ({pct}%)")

                if os.path.exists(temp_part) and os.path.getsize(temp_part) > 1024:
                    if os.path.exists(dest_path):
                        os.remove(dest_path)
                    os.replace(temp_part, dest_path)
                    return True
    except Exception as e:
        print(f"[Updater] Download error: {e}")
        if os.path.exists(temp_part):
            try:
                os.remove(temp_part)
            except Exception:
                pass
    return False


def apply_update_and_restart(new_exe_path: str) -> bool:
    """
    Apply update to the running Windows .exe application and restart cleanly.
    1. Performs an immediate NTFS atomic rename of the running executable (current.exe -> current.exe.old)
       and copies the new executable into place while still running.
    2. Launches a background runner script (using ping delays instead of timeout.exe, which fails
       in headless/detached processes) with a 15-retry fallback loop to clean up and relaunch.
    """
    import shutil
    is_frozen = getattr(sys, "frozen", False)
    current_exe = os.path.abspath(sys.executable)
    new_exe_abs = os.path.abspath(new_exe_path)

    if not is_frozen:
        print(f"[Updater] Dev mode: new update saved to {new_exe_abs}")
        return False

    old_exe_backup = current_exe + ".old"
    swapped_in_place = False
    try:
        if os.path.exists(old_exe_backup):
            try:
                os.remove(old_exe_backup)
            except Exception:
                pass
        # Windows NTFS allows renaming a running .exe file in-place!
        os.replace(current_exe, old_exe_backup)
        shutil.copy2(new_exe_abs, current_exe)
        swapped_in_place = True
    except Exception as e:
        print(f"[Updater] Note during in-place NTFS rename: {e}")

    current_pid = os.getpid()
    bat_path = Path(TEMP_DIR) / "updater_runner.bat"

    bat_content = f"""@echo off
setlocal EnableDelayedExpansion
set "CURRENT_EXE={current_exe}"
set "OLD_EXE={old_exe_backup}"
set "NEW_EXE={new_exe_abs}"
set "PID={current_pid}"

ping 127.0.0.1 -n 2 >nul

:wait_loop
tasklist /fi "pid eq %PID%" 2>nul | find "%PID%" >nul
if not errorlevel 1 (
    ping 127.0.0.1 -n 2 >nul
    goto wait_loop
)

for /L %%i in (1,1,10) do (
    if exist "%NEW_EXE%" (
        move /y "%CURRENT_EXE%" "%OLD_EXE%" >nul 2>&1
        copy /y "%NEW_EXE%" "%CURRENT_EXE%" >nul 2>&1
        if not errorlevel 1 (
            del /f /q "%NEW_EXE%" >nul 2>&1
            goto launch_new
        )
        ping 127.0.0.1 -n 2 >nul
    ) else (
        goto launch_new
    )
)

REM If standard copy into C:\Program Files was blocked by Windows UAC, elevate via PowerShell RunAs:
if exist "%NEW_EXE%" (
    powershell -NoProfile -Command "Start-Process cmd.exe -ArgumentList '/c move /y ""%CURRENT_EXE%"" ""%OLD_EXE%"" >nul 2>&1 & copy /y ""%NEW_EXE%"" ""%CURRENT_EXE%"" >nul 2>&1 & del /f /q ""%NEW_EXE%"" >nul 2>&1 & del /f /q ""%OLD_EXE%"" >nul 2>&1' -Verb RunAs -WindowStyle Hidden -Wait"
)

:launch_new
del /f /q "%OLD_EXE%" >nul 2>&1
start "" "%CURRENT_EXE%"
(goto) 2>nul & del "%~f0"
"""

    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(bat_content)

    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NO_WINDOW = 0x08000000
    subprocess.Popen(
        ["cmd.exe", "/c", str(bat_path)],
        creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        close_fds=True
    )
    return True


# --- Asynchronous Worker Threads for Qt ---
class CheckUpdateWorker(QThread):
    update_found = Signal(object)    # UpdateInfo
    no_update = Signal(str)          # current version
    check_failed = Signal(str)       # error message

    def __init__(self, manifest_url: Optional[str] = None):
        super().__init__()
        self.manifest_url = manifest_url

    def run(self):
        has_update, info, msg = check_for_updates(self.manifest_url)
        if has_update and info:
            self.update_found.emit(info)
        elif not has_update and info:
            self.no_update.emit(info.current_version)
        else:
            self.check_failed.emit(msg)


class DownloadUpdateWorker(QThread):
    progress = Signal(int, str)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, download_url: str):
        super().__init__()
        self.download_url = download_url
        self.dest_path = str(Path(TEMP_DIR) / "app_update_new.exe")

    def run(self):
        success = download_update_file(
            self.download_url,
            self.dest_path,
            progress_cb=self.progress.emit
        )
        if success and os.path.exists(self.dest_path):
            self.finished.emit(self.dest_path)
        else:
            self.failed.emit("មិនអាចទាញយកឯកសារ Update បានទេ។ សូមពិនិត្យមើលការតភ្ជាប់អ៊ីនធឺណិត!")
