"""加密的 B站 登录凭据 store（P5 登录态）。

凭据（SESSDATA / bili_jct / DedeUserID / buvid3）用 Fernet 对称加密落盘到 per-plugin data
目录，密钥与密文分别 `chmod 600`（非 Windows）。**凭据绝不写 audit / log / config / UI**——
只回显 uid / 用户名 / 是否登录。登录态供 `bili_identity` 头像抓取、`bili_live_ingest` 弹幕连接、
`lookup` 使用，过登录态绕 B站 -352 风控（匿名 buvid3 不足以过，见 development.md「-352」）。

加密模式移植自旧插件 `bilibili_danmaku`（Fernet + 独立密钥文件），是 P5 复用的既有方案。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

_KEY_FILE = "bili_credential.key"
_CRED_FILE = "bili_credential.enc"
_FIELDS = ("SESSDATA", "bili_jct", "DedeUserID", "buvid3")


class CredentialStore:
    """B站 登录凭据的加密本地 store。save/load/delete 走线程池不阻塞事件循环。"""

    def __init__(self, plugin: Any, audit: Any = None) -> None:
        self.plugin = plugin
        self.audit = audit
        self._lock = asyncio.Lock()

    def _data_dir(self) -> Path:
        return Path(self.plugin.data_path())

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
        key_path = data_dir / _KEY_FILE
        if key_path.exists():
            return Fernet(key_path.read_bytes())
        key = Fernet.generate_key()
        data_dir.mkdir(parents=True, exist_ok=True)
        try:
            with key_path.open("xb") as handle:
                handle.write(key)
            self._chmod600(key_path)
            return Fernet(key)
        except FileExistsError:
            return Fernet(key_path.read_bytes())

    # ── 同步实现（在 to_thread 里跑） ──────────────────────────

    def _save_sync(self, payload: dict) -> bool:
        try:
            cred = {key: str(payload.get(key) or "") for key in _FIELDS}
            fernet = self._get_fernet()
            enc = fernet.encrypt(json.dumps(cred, ensure_ascii=False).encode("utf-8"))
            cred_path = self._data_dir() / _CRED_FILE
            cred_path.parent.mkdir(parents=True, exist_ok=True)
            cred_path.write_bytes(enc)
            self._chmod600(cred_path)
            return True
        except Exception:
            return False

    def _load_sync(self) -> dict | None:
        try:
            data_dir = self._data_dir()
            cred_path = data_dir / _CRED_FILE
            key_path = data_dir / _KEY_FILE
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
        for name in (_CRED_FILE, _KEY_FILE):
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
                self.audit.record(
                    "bili_credential_saved",
                    "credential saved (encrypted)",
                    detail={"uid": str(payload.get("DedeUserID") or "")},
                )
            else:
                self.audit.record("bili_credential_save_failed", "encrypt/save failed", level="warning")
        return ok

    async def load(self) -> dict | None:
        async with self._lock:
            return await asyncio.to_thread(self._load_sync)

    async def delete(self) -> list[str]:
        async with self._lock:
            removed = await asyncio.to_thread(self._delete_sync)
        if self.audit is not None and removed:
            self.audit.record("bili_credential_deleted", "credential files removed", detail={"files": removed})
        return removed

    def has_credential(self) -> bool:
        try:
            data_dir = self._data_dir()
            return (data_dir / _CRED_FILE).exists() and (data_dir / _KEY_FILE).exists()
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
