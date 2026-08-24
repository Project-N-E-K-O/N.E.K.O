"""Plugin-local encrypted storage for allowlisted NetEase cookies."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_COOKIE_FILE = "netease_credentials.bin"
_KEY_FILE = "netease_credentials.key"
_MAX_COOKIE_LENGTH = 4096
_MAX_COOKIE_INPUT_LENGTH = 8192
_COOKIE_NAMES = {
    "MUSIC_U": "MUSIC_U",
    "MUSIC_A": "MUSIC_A",
    "NMTID": "NMTID",
    "__CSRF": "__csrf",
}


class CredentialError(RuntimeError):
    """The plugin-local credential could not be safely read or written."""


def _normalize_cookie_value(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip()
    if not normalized or len(normalized) > _MAX_COOKIE_LENGTH:
        return ""
    if any(
        char.isspace() or char == ";" or ord(char) < 32 or ord(char) == 127
        for char in normalized
    ):
        return ""
    return normalized


def normalize_netease_cookies(
    value: object,
    *,
    nmtid: object = "",
) -> dict[str, str]:
    """Normalize a Cookie header/dict while retaining only NetEase auth fields."""

    raw_cookies: dict[str, object] = {}
    if isinstance(value, Mapping):
        raw_cookies = {
            str(key): candidate
            for key, candidate in value.items()
            if isinstance(key, str)
        }
    elif isinstance(value, str):
        raw = value.strip()
        if not raw or len(raw) > _MAX_COOKIE_INPUT_LENGTH:
            return {}
        first_name, separator, _first_value = raw.partition("=")
        is_cookie_header = ";" in raw or (
            bool(separator) and first_name.strip().upper() in _COOKIE_NAMES
        )
        if not is_cookie_header:
            raw_cookies["MUSIC_U"] = raw
        else:
            for item in raw.split(";"):
                if "=" not in item:
                    continue
                key, candidate = item.strip().split("=", 1)
                raw_cookies[key.strip()] = candidate.strip()
    else:
        return {}

    if nmtid:
        raw_cookies["NMTID"] = nmtid

    normalized: dict[str, str] = {}
    for key, candidate in raw_cookies.items():
        canonical = _COOKIE_NAMES.get(key.strip().upper())
        if canonical is None:
            continue
        cookie_value = _normalize_cookie_value(candidate)
        if cookie_value:
            normalized[canonical] = cookie_value
    if not normalized.get("MUSIC_U"):
        return {}
    return normalized


def normalize_music_u(value: object) -> str:
    """Accept a MUSIC_U value or Cookie header and return only MUSIC_U."""

    return normalize_netease_cookies(value).get("MUSIC_U", "")


class CredentialStore:
    """Encrypt NetEase cookies inside this plugin's private data directory."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)
        self._cookie_path = self._data_dir / _COOKIE_FILE
        self._key_path = self._data_dir / _KEY_FILE
        self._lock = asyncio.Lock()

    async def configured(self) -> bool:
        return bool((await self.load()).get("MUSIC_U"))

    async def load(self) -> dict[str, str]:
        async with self._lock:
            try:
                return await asyncio.to_thread(self._load_sync)
            except (
                OSError,
                InvalidToken,
                UnicodeError,
                ValueError,
                json.JSONDecodeError,
            ):
                return {}

    async def save(self, value: object, *, nmtid: object = "") -> None:
        cookies = normalize_netease_cookies(value, nmtid=nmtid)
        if not cookies:
            raise CredentialError("MUSIC_U is invalid")
        async with self._lock:
            try:
                await asyncio.to_thread(self._save_sync, cookies)
            except (OSError, ValueError) as exc:
                raise CredentialError("NetEase cookies could not be saved") from exc

    async def clear(self) -> None:
        async with self._lock:
            try:
                await asyncio.to_thread(self._clear_sync)
            except OSError as exc:
                raise CredentialError("MUSIC_U could not be cleared") from exc

    def _load_sync(self) -> dict[str, str]:
        if not self._cookie_path.is_file() or not self._key_path.is_file():
            return {}
        key = self._key_path.read_bytes()
        encrypted = self._cookie_path.read_bytes()
        payload = json.loads(Fernet(key).decrypt(encrypted).decode("utf-8"))
        if not isinstance(payload, dict):
            return {}
        return normalize_netease_cookies(payload)

    def _save_sync(self, cookies: dict[str, str]) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        key = (
            self._key_path.read_bytes()
            if self._key_path.is_file()
            else Fernet.generate_key()
        )
        try:
            fernet = Fernet(key)
        except ValueError:
            key = Fernet.generate_key()
            fernet = Fernet(key)
        encrypted = fernet.encrypt(
            json.dumps(cookies, ensure_ascii=False).encode("utf-8")
        )
        self._atomic_write(self._key_path, key)
        self._atomic_write(self._cookie_path, encrypted)

    def _clear_sync(self) -> None:
        for path in (self._cookie_path, self._key_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            if os.name != "nt":
                path.chmod(0o600)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


__all__ = [
    "CredentialError",
    "CredentialStore",
    "normalize_music_u",
    "normalize_netease_cookies",
]
