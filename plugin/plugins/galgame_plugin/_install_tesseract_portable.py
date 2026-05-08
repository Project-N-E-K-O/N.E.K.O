"""Install Tesseract without admin rights using /CURRENTUSER Inno Setup flag."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

INSTALLER_URL = (
    "https://ghproxy.com/https://github.com/UB-Mannheim/tesseract/"
    "releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe"
)
INSTALLER_NAME = "tesseract-ocr-w64-setup-5.4.0.20240606.exe"
INSTALLER_SHA256 = "c885fff6998e0608ba4bb8ab51436e1c6775c2bafc2559a19b423e18678b60c9"
TESSDATA_COMMIT = "87416418657359cb625c412a48b6e1d6d41c29bd"
TESSDATA_BASE_URL = (
    "https://cdn.jsdelivr.net/gh/tesseract-ocr/"
    f"tessdata_fast@{TESSDATA_COMMIT}"
)
LANGUAGES = ["chi_sim", "jpn", "eng"]
LANGUAGE_SHA256 = {
    "chi_sim": "a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730",
    "jpn": "1f5de9236d2e85f5fdf4b3c500f2d4926f8d9449f28f5394472d9e8d83b91b4d",
    "eng": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
}
TARGET_DIR = Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\N.E.K.O\Tesseract-OCR"))


def _normalize_sha256(value: object) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("sha256:"):
        text = text.split(":", 1)[1].strip()
    if len(text) == 64 and all(char in "0123456789abcdef" for char in text):
        return text
    return ""


def _verify_file_sha256(path: Path, expected_sha256: str) -> None:
    expected = _normalize_sha256(expected_sha256)
    if not expected:
        return
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            if not chunk:
                break
            hasher.update(chunk)
    actual = hasher.hexdigest()
    if actual != expected:
        path.unlink(missing_ok=True)
        raise RuntimeError(
            f"checksum mismatch for {path.name}: expected sha256 {expected}, got {actual}"
        )
    print(f"Verified sha256 for {path.name}: {actual}")


def _language_sha256_from_env(language: str) -> str:
    key = f"TESSERACT_{language.upper()}_SHA256"
    return _normalize_sha256(os.getenv(key, "")) or LANGUAGE_SHA256.get(language, "")


def download_file(
    url: str,
    destination: Path,
    timeout: float = 300.0,
    *,
    expected_sha256: str = "",
) -> None:
    print(f"Downloading {url} ...")
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length", 0))
            downloaded = 0
            with destination.open("wb") as f:
                for chunk in response.iter_bytes(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = downloaded / total * 100
                            print(f"  {pct:.1f}% ({downloaded}/{total} bytes)", end="\r")
    print()
    _verify_file_sha256(destination, expected_sha256)


def main() -> None:
    print("=" * 60)
    print("Tesseract Portable Installer (no admin required)")
    print("=" * 60)

    # Check if already installed
    exe_path = TARGET_DIR / "tesseract.exe"
    if exe_path.exists():
        print(f"Tesseract already exists at: {exe_path}")
        # Still ensure languages are present
    else:
        tmp_dir = Path(tempfile.gettempdir()) / "neko-tesseract-install"
        tmp_dir.mkdir(exist_ok=True)
        installer_path = tmp_dir / INSTALLER_NAME

        if not installer_path.exists() or installer_path.stat().st_size < 1_000_000:
            try:
                download_file(
                    INSTALLER_URL,
                    installer_path,
                    expected_sha256=os.getenv("TESSERACT_INSTALLER_SHA256", "")
                    or INSTALLER_SHA256,
                )
            except Exception as exc:
                print(f"Download failed: {exc}")
                sys.exit(1)
        else:
            print(f"Using cached installer: {installer_path}")
            try:
                _verify_file_sha256(
                    installer_path,
                    os.getenv("TESSERACT_INSTALLER_SHA256", "") or INSTALLER_SHA256,
                )
            except Exception as exc:
                print(f"Cached installer verification failed: {exc}")
                sys.exit(1)

        print("\nRunning installer (no-admin mode)...")
        cmd = [
            str(installer_path),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/SP-",
            "/CURRENTUSER",
            f"/DIR={TARGET_DIR}",
        ]
        try:
            subprocess.run(cmd, check=True, timeout=300)
            print("Installer completed.")
        except subprocess.CalledProcessError as exc:
            print(f"Installer failed with code {exc.returncode}")
            sys.exit(1)
        except Exception as exc:
            print(f"Installer failed: {exc}")
            sys.exit(1)

    # Ensure tessdata dir and languages
    tessdata_dir = TARGET_DIR / "tessdata"
    tessdata_dir.mkdir(parents=True, exist_ok=True)

    for lang in LANGUAGES:
        data_file = tessdata_dir / f"{lang}.traineddata"
        if data_file.exists():
            try:
                _verify_file_sha256(data_file, _language_sha256_from_env(lang))
            except Exception:
                print(f"  {lang}: cached file corrupted, will re-download")

    missing_langs = []
    for lang in LANGUAGES:
        data_file = tessdata_dir / f"{lang}.traineddata"
        if not data_file.exists():
            missing_langs.append(lang)

    if missing_langs:
        print(f"\nDownloading language files: {missing_langs}")
        for lang in missing_langs:
            url = f"{TESSDATA_BASE_URL}/{lang}.traineddata"
            dest = tessdata_dir / f"{lang}.traineddata"
            try:
                download_file(url, dest, expected_sha256=_language_sha256_from_env(lang))
                print(f"  {lang}: OK")
            except Exception as exc:
                print(f"  {lang}: FAILED ({exc})")
                sys.exit(1)
    else:
        print("All required language files are present.")

    # Verify
    exe_path = TARGET_DIR / "tesseract.exe"
    if exe_path.exists():
        print(f"\nTesseract installed at: {exe_path}")
        print("Installation complete.")
    else:
        print(f"\nERROR: tesseract.exe not found at {exe_path}")
        sys.exit(1)


if __name__ == "__main__":
    main()
