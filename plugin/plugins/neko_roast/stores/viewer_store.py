"""Single writer for viewer profiles（本地 JSON 持久化，存储目录可由主播配置）。

不走宿主 PluginStore：其 ``store.enabled`` 在插件构造期被冻结、且 ``store.db`` 路径焊死不可配
（见 docs/devlog.md）。改为直接读写一个本地 JSON 文件，从根上绕开那个 bug，并让"改存储位置"成为可能：
- 存储目录优先用配置的 ``viewer_store_dir``（``dir_provider`` 提供），留空则用插件数据目录 ``plugin.data_path()``；
- 配置目录不可写时回退默认目录并记一次 audit；
- 原子写（tmp + os.replace）+ asyncio 锁，避免并发 upsert/mark_roasted 互相覆盖（lost update）。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable

from ..core.contracts import ViewerIdentity, ViewerProfile, utc_now_iso

_STORE_FILE = "viewer_profiles.json"


class ViewerStore:
    def __init__(self, plugin: Any, audit: Any, dir_provider: Callable[[], str] | None = None) -> None:
        self.plugin = plugin
        self.audit = audit
        self._dir_provider = dir_provider
        # 串行化读改写，避免并发 upsert/mark_roasted 互相覆盖（lost update）。
        self._lock = asyncio.Lock()
        self._fallback_warned = False
        self._active_fallback_file: Path | None = None

    # ── 存储路径解析 ──────────────────────────────────────────────

    def _audit(self, op: str, message: str, level: str = "warning") -> None:
        if self.audit is not None:
            try:
                self.audit.record(op, message, level=level)
            except Exception:  # noqa: BLE001 — 记录失败不能反过来炸存储
                pass

    def _default_dir(self) -> Path:
        try:
            base = self.plugin.data_path()
            if base:
                return Path(base)
        except Exception:  # noqa: BLE001
            pass
        # 兜底：绝不写进 cwd（会污染工作目录/仓库）；退到进程临时目录。
        # 生产中 data_path() 必然可用、不会走到这，仅防御损坏的宿主/测试桩。
        return Path(tempfile.gettempdir()) / "neko_roast"

    def _configured_dir(self) -> str:
        if not self._dir_provider:
            return ""
        try:
            return str(self._dir_provider() or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    def _resolve_file(self) -> tuple[Path, bool]:
        """返回 (档案文件路径, 是否用了自定义目录)。纯解析、无副作用（不建目录）。"""
        configured = self._configured_dir()
        if configured:
            return Path(configured) / _STORE_FILE, True
        return self._default_dir() / _STORE_FILE, False

    def storage_status(self) -> dict[str, Any]:
        """给面板看的存储状态：当前文件路径 + 目录能否写 + 是否自定义。"""
        file, custom = self._resolve_file()
        directory = file.parent
        probe = directory if directory.exists() else directory.parent
        try:
            writable = probe.exists() and os.access(str(probe), os.W_OK)
        except Exception:  # noqa: BLE001
            writable = False
        return {
            "path": str(file),
            "dir": str(directory),
            "writable": bool(writable),
            "exists": file.exists(),
            "using_custom": custom,
        }

    def _write_json(self, file: Path, profiles: dict[str, dict[str, Any]]) -> bool:
        """原子写（tmp + os.replace）；成功 True，失败 False（不抛）。"""
        try:
            file.parent.mkdir(parents=True, exist_ok=True)
            tmp = file.with_suffix(file.suffix + ".tmp")
            tmp.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(str(tmp), str(file))
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _load_all(self) -> dict[str, dict[str, Any]]:
        file, custom = self._resolve_file()
        candidates: list[Path] = []
        if self._active_fallback_file is not None:
            candidates.append(self._active_fallback_file)
        candidates.append(file)
        if custom:
            fallback = self._default_dir() / _STORE_FILE
            if fallback not in candidates:
                candidates.append(fallback)
        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                text = await asyncio.to_thread(candidate.read_text, encoding="utf-8")
                data = json.loads(text)
            except Exception as exc:  # noqa: BLE001
                self._audit("viewer_store_load_failed", f"{type(exc).__name__}: {exc}")
                continue
            if isinstance(data, dict):
                if candidate != file:
                    self._active_fallback_file = candidate
                return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}
        return {}

    async def _save_all(self, profiles: dict[str, dict[str, Any]]) -> None:
        file, custom = self._resolve_file()
        if await asyncio.to_thread(self._write_json, file, profiles):
            self._active_fallback_file = None
            return
        # 自定义目录写失败 → 回退默认目录（只告警一次，避免刷屏）。
        if custom:
            fallback = self._default_dir() / _STORE_FILE
            if await asyncio.to_thread(self._write_json, fallback, profiles):
                self._active_fallback_file = fallback
                if not self._fallback_warned:
                    self._audit("viewer_store_fallback", f"自定义目录不可写，已回退默认目录：{fallback.parent}")
                    self._fallback_warned = True
                return
        self._audit("viewer_store_save_failed", f"档案写入失败：{file}")

    # ── 公共 API（行为与原 KV 版一致，仅底层换成 JSON）──────────────

    async def upsert_identity(self, identity: ViewerIdentity) -> ViewerProfile:
        async with self._lock:
            return await self._upsert_identity_locked(identity)

    async def _upsert_identity_locked(self, identity: ViewerIdentity) -> ViewerProfile:
        profiles = await self._load_all()
        now = utc_now_iso()
        existing = profiles.get(identity.uid)
        if existing:
            profile = ViewerProfile(
                uid=identity.uid,
                nickname=identity.nickname or str(existing.get("nickname") or identity.uid),
                avatar_url=identity.avatar_url or str(existing.get("avatar_url") or ""),
                first_seen_at=str(existing.get("first_seen_at") or now),
                last_seen_at=now,
                roast_count=int(existing.get("roast_count") or 0),
                last_roast_at=str(existing.get("last_roast_at") or ""),
                last_result=str(existing.get("last_result") or ""),
            )
        else:
            profile = ViewerProfile(
                uid=identity.uid,
                nickname=identity.nickname or identity.uid,
                avatar_url=identity.avatar_url,
                first_seen_at=now,
                last_seen_at=now,
            )
        profiles[identity.uid] = profile.to_dict()
        await self._save_all(profiles)
        return profile

    async def mark_roasted(self, uid: str, output: str) -> None:
        async with self._lock:
            await self._mark_roasted_locked(uid, output)

    async def _mark_roasted_locked(self, uid: str, output: str) -> None:
        profiles = await self._load_all()
        item = dict(profiles.get(uid) or {"uid": uid})
        item["roast_count"] = int(item.get("roast_count") or 0) + 1
        item["last_roast_at"] = utc_now_iso()
        item["last_result"] = output
        profiles[uid] = item
        await self._save_all(profiles)

    async def has_roasted(self, uid: str) -> bool:
        profiles = await self._load_all()
        item = profiles.get(uid)
        return bool(item and int(item.get("roast_count") or 0) > 0)

    async def recent_profiles(self, limit: int = 30) -> list[dict[str, Any]]:
        profiles = await self._load_all()
        ordered = sorted(profiles.values(), key=lambda item: str(item.get("last_seen_at") or ""), reverse=True)
        return [dict(item) for item in ordered[:limit]]
