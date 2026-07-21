"""Namespaced encrypted credential store for live-platform auth.

Credential payloads are Fernet-encrypted under the per-plugin data directory.
The default ``bili`` namespace keeps the legacy ``bili_credential.*`` filenames;
other providers get isolated ``{namespace}_credential.*`` files. Secrets must
never be written to audit / log / config / UI.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

_KEY_FILE = "bili_credential.key"
_CRED_FILE = "bili_credential.enc"
_FIELDS = ("SESSDATA", "bili_jct", "DedeUserID", "buvid3")
_NAMESPACE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


class CredentialStore:
    """Encrypted local credential store. save/load/delete run in a worker thread."""

    def __init__(
        self,
        plugin: Any,
        audit: Any = None,
        *,
        namespace: str = "bili",
        fields: tuple[str, ...] = _FIELDS,
    ) -> None:
        self.plugin = plugin
        self.audit = audit
        self.namespace = str(namespace or "bili").strip()
        if not _NAMESPACE_RE.fullmatch(self.namespace):
            raise ValueError("credential namespace must use ASCII letters, digits, '_' or '-'")
        self.fields = tuple(fields or _FIELDS)
        self.audit_identity_field = (
            "DedeUserID"
            if "DedeUserID" in self.fields
            else "uid"
            if "uid" in self.fields
            else ""
        )
        self._lock = asyncio.Lock()

    def _data_dir(self) -> Path:
        return Path(self.plugin.data_path())

    def _key_file(self) -> str:
        return _KEY_FILE if self.namespace == "bili" else f"{self.namespace}_credential.key"

    def _cred_file(self) -> str:
        return _CRED_FILE if self.namespace == "bili" else f"{self.namespace}_credential.enc"

    def _audit_op(self, suffix: str) -> str:
        return f"{self.namespace}_credential_{suffix}"

    @staticmethod
    def _chmod600(path: Path) -> None:
        if sys.platform != "win32":
            try:
                os.chmod(str(path), 0o600)
            except OSError:
                pass

    def _get_fernet(self):
        from cryptography.fernet import Fernet

        data_dir = self._data_dir()
        key_path = data_dir / self._key_file()
        if key_path.exists():
            return Fernet(key_path.read_bytes())
        key = Fernet.generate_key()
        data_dir.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(key)
            self._chmod600(key_path)
            return Fernet(key)
        except FileExistsError:
            return Fernet(key_path.read_bytes())

    # ── 同步实现（在 to_thread 里跑） ──────────────────────────

    def _save_sync(self, payload: dict) -> bool:
        try:
            cred = {key: str(payload.get(key) or "") for key in self.fields}
            fernet = self._get_fernet()
            enc = fernet.encrypt(json.dumps(cred, ensure_ascii=False).encode("utf-8"))
            cred_path = self._data_dir() / self._cred_file()
            cred_path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{cred_path.name}.",
                suffix=".tmp",
                dir=cred_path.parent,
            )
            temp_path = Path(temp_name)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(enc)
                    handle.flush()
                    os.fsync(handle.fileno())
                self._chmod600(temp_path)
                os.replace(temp_path, cred_path)
            finally:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            self._chmod600(cred_path)
            return True
        except Exception:
            return False

    def _load_sync(self) -> dict | None:
        try:
            data_dir = self._data_dir()
            cred_path = data_dir / self._cred_file()
            key_path = data_dir / self._key_file()
            if not cred_path.exists() or not key_path.exists():
                return None
            from cryptography.fernet import Fernet

            fernet = Fernet(key_path.read_bytes())
            dec = fernet.decrypt(cred_path.read_bytes()).decode("utf-8")
            data = json.loads(dec)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _delete_sync(self) -> list[str]:
        removed: list[str] = []
        data_dir = self._data_dir()
        for name in (self._cred_file(), self._key_file()):
            path = data_dir / name
            try:
                if path.exists():
                    path.unlink()
                    removed.append(name)
            except OSError:
                pass
        return removed

    # ── 异步接口（喂给 bili_auth_service 的回调契约） ─────────────

    async def save(self, payload: dict) -> bool:
        async with self._lock:
            ok = await asyncio.to_thread(self._save_sync, payload)
        if self.audit is not None:
            if ok:
                identity = (
                    " ".join(str(payload.get(self.audit_identity_field) or "").split())
                    if self.audit_identity_field
                    else ""
                )
                self.audit.record(
                    self._audit_op("saved"),
                    "credential saved (encrypted)"
                    if identity
                    else "credential saved (encrypted; identity unavailable)",
                    level="info" if identity else "warning",
                    detail={"uid": identity} if identity else {"identity_status": "unidentified"},
                )
            else:
                self.audit.record(self._audit_op("save_failed"), "encrypt/save failed", level="warning")
        return ok

    async def load(self) -> dict | None:
        async with self._lock:
            return await asyncio.to_thread(self._load_sync)

    async def delete(self) -> list[str]:
        async with self._lock:
            removed = await asyncio.to_thread(self._delete_sync)
        if self.audit is not None and removed:
            self.audit.record(self._audit_op("deleted"), "credential files removed", detail={"files": removed})
        return removed

    def has_credential(self) -> bool:
        try:
            data_dir = self._data_dir()
            return (data_dir / self._cred_file()).exists() and (data_dir / self._key_file()).exists()
        except Exception:
            return False

    async def build_credential(self):
        """构建 `bilibili_api.Credential` 供身份/连接/查询用；无凭据或缺 SESSDATA 返回 None。"""
        data = await self.load()
        if not data or not data.get("SESSDATA"):
            return None
        try:
            from bilibili_api import Credential

            return Credential(
                sessdata=data.get("SESSDATA", ""),
                bili_jct=data.get("bili_jct", ""),
                dedeuserid=str(data.get("DedeUserID", "") or ""),
                buvid3=data.get("buvid3", ""),
            )
        except Exception:
            return None
