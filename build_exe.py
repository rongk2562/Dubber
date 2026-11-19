"""
Automated PyInstaller Build Script for AI Movie Translator & Auto Dubbing Studio
Generates a standalone, self-contained Windows .exe distribution (including bundled FFmpeg & assets)
so it runs on any other Windows PC without errors.
"""

import sys
import os
import shutil
import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
MAIN_SCRIPT = ROOT_DIR / "main.py"
VERSION_FILE = ROOT_DIR / "version_info.txt"
APP_NAME = "AI_Movie_Dubbing_Studio"


def ensure_pyinstaller():
    try:
        import PyInstaller  # noqa: F401
        return True
    except ImportError:
        print("[*] Installing PyInstaller...")
        res = subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"])
        return res.returncode == 0


def find_ffmpeg_binaries():
    """Locate ffmpeg.exe and ffprobe.exe on the build PC to bundle for other PCs."""
    candidates = [
        Path(r"C:\ffmpeg\bin"),
        ROOT_DIR / "ffmpeg_bin",
    ]
    which_ffmpeg = shutil.which("ffmpeg")
    if which_ffmpeg:
        candidates.insert(0, Path(which_ffmpeg).resolve().parent)

    for folder in candidates:
        ffmpeg_exe = folder / "ffmpeg.exe"
        ffprobe_exe = folder / "ffprobe.exe"
        if ffmpeg_exe.exists():
            return ffmpeg_exe, (ffprobe_exe if ffprobe_exe.exists() else None)
    return None, None


def post_build_bundle(dist_dir: Path):
    """Copy assets, config files, and FFmpeg binaries alongside the .exe for 100% portability."""
    print("\n[*] Packaging portable resources & FFmpeg for other PCs...")

    # 1. Copy assets folder directly next to .exe as well
    src_assets = ROOT_DIR / "assets"
    if src_assets.exists():
        dst_assets = dist_dir / "assets"
        shutil.copytree(src_assets, dst_assets, dirs_exist_ok=True)
        print(f"    [+] Bundled assets -> {dst_assets}")

    # 2. Copy SRS_KHMER.md and version.json next to .exe
    for doc_name in ("version.json", "SRS_KHMER.md"):
        src_doc = ROOT_DIR / doc_name
        if src_doc.exists():
            shutil.copy2(src_doc, dist_dir / doc_name)

    # 2b. Copy VC++ Runtime DLLs next to the .exe as well as _internal so LoadLibrary(python314.dll)
    # never fails on a clean Windows 10/11 PC without Visual C++ Redistributable installed
    internal_dir = dist_dir / "_internal"
    if internal_dir.exists():
        for dll_name in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "ucrtbase.dll"):
            src_dll = internal_dir / dll_name
            if not src_dll.exists():
                sys_dll = Path(r"C:\Windows\System32") / dll_name
                if sys_dll.exists():
                    shutil.copy2(sys_dll, internal_dir / dll_name)
                    src_dll = internal_dir / dll_name
            if src_dll.exists():
                shutil.copy2(src_dll, dist_dir / dll_name)

    # 3. Create writable workspace subfolders
    for folder_name in ("data", "temp", "projects"):
        (dist_dir / folder_name).mkdir(parents=True, exist_ok=True)

    # 4. Bundle ffmpeg.exe & ffprobe.exe into dist_dir/ffmpeg_bin
    ffmpeg_exe, ffprobe_exe = find_ffmpeg_binaries()
    if ffmpeg_exe:
        ffmpeg_dst_dir = dist_dir / "ffmpeg_bin"
        ffmpeg_dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ffmpeg_exe, ffmpeg_dst_dir / "ffmpeg.exe")
        print(f"    [+] Bundled ffmpeg.exe -> {ffmpeg_dst_dir / 'ffmpeg.exe'}")
        if ffprobe_exe:
            shutil.copy2(ffprobe_exe, ffmpeg_dst_dir / "ffprobe.exe")
            print(f"    [+] Bundled ffprobe.exe -> {ffmpeg_dst_dir / 'ffprobe.exe'}")
    else:
        print("    [!] Warning: ffmpeg.exe not found on build machine; target PC will need FFmpeg in PATH.")

    # 5. Bundle pre-downloaded faster-whisper 'base' model so target PCs work instantly offline!
    hf_base_snapshots = (
        Path.home()
        / ".cache"
        / "huggingface"
        / "hub"
        / "models--Systran--faster-whisper-base"
        / "snapshots"
    )
    if hf_base_snapshots.exists():
        for snap_dir in hf_base_snapshots.iterdir():
            if snap_dir.is_dir() and (snap_dir / "model.bin").exists():
                dst_model_dir = dist_dir / "whisper_models" / "base"
                dst_model_dir.mkdir(parents=True, exist_ok=True)
                for item in snap_dir.iterdir():
                    if item.is_file():
                        shutil.copy2(item, dst_model_dir / item.name)
                print(f"    [+] Bundled offline faster-whisper 'base' model -> {dst_model_dir}")
                break

    # 6. Copy compiled .exe to Update Server downloads directory so v1.0.0 clients can auto-update!
    exe_built = dist_dir / f"{APP_NAME}.exe"
    server_downloads_dir = ROOT_DIR / "server" / "public" / "downloads"
    if exe_built.exists():
        server_downloads_dir.mkdir(parents=True, exist_ok=True)
        dst_update_exe = server_downloads_dir / f"{APP_NAME}.exe"
        shutil.copy2(exe_built, dst_update_exe)
        print(f"    [+] Published update .exe -> {dst_update_exe}")


def build():
    print("==================================================")
    print(f"Building {APP_NAME} Standalone Windows Executable")
    print("==================================================")

    if not ensure_pyinstaller():
        print("[!] Failed to install PyInstaller. Please run: pip install pyinstaller")
        sys.exit(1)

    icon_path = ROOT_DIR / "assets" / "logo.ico"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--onedir",            # Fast startup & full compatibility with ML/Qt DLLs on other PCs
        "--windowed",          # Clean GUI application without black console window
        "--noconfirm",         # Overwrite existing dist
        f"--version-file={str(VERSION_FILE)}",
        f"--add-data={str(ROOT_DIR / 'version.json')};.",
        f"--add-data={str(ROOT_DIR / 'SRS_KHMER.md')};.",
        f"--add-data={str(ROOT_DIR / 'assets')};assets",
    ]
    if icon_path.exists():
        cmd.append(f"--icon={str(icon_path)}")

    cmd.extend([
        "--collect-all", "edge_tts",
        "--collect-all", "pydub",
        "--collect-all", "httpx",
        "--collect-all", "certifi",
        "--collect-all", "faster_whisper",
        "--collect-all", "ctranslate2",
        "--collect-all", "tokenizers",
        "--collect-all", "huggingface_hub",
        "--collect-all", "google.generativeai",
        "--collect-all", "rapidocr_onnxruntime",
        "--collect-all", "opencc",
        "--collect-all", "cryptography",
        "--collect-all", "keyring",
        "--hidden-import", "keyring.backends.Windows",
        "--hidden-import", "PySide6.QtMultimedia",
        "--hidden-import", "PySide6.QtMultimediaWidgets",
        "--hidden-import", "PySide6.QtNetwork",
        "--hidden-import", "packaging",
        "--hidden-import", "packaging.version",
        "--hidden-import", "scipy.signal",
        "--hidden-import", "sqlite3",
        str(MAIN_SCRIPT)
    ])

    print(f"Executing: {' '.join(cmd)}\n")
    res = subprocess.run(cmd, cwd=str(ROOT_DIR))

    if res.returncode == 0:
        dist_dir = ROOT_DIR / "dist" / APP_NAME
        post_build_bundle(dist_dir)
        exe_file = dist_dir / f"{APP_NAME}.exe"
        print("\n==================================================")
        print("[+] Build successful!")
        print(f"    Executable location : {exe_file}")
        print(f"    Portable folder     : {dist_dir}")
        print("    (Includes app .exe, Qt6 UI, Whisper AI, Edge-TTS, Logos, and FFmpeg!)")
        print("==================================================")
    else:
        print(f"\n[!] Build failed with exit code {res.returncode}")
        sys.exit(res.returncode)


if __name__ == "__main__":
    build()
