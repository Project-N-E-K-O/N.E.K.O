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
from ..core.contracts_public import public_int, public_text
from ..core.viewer_preferences import (
    infer_viewer_preferences,
    merge_preference_counts,
    safe_preference_counts,
    viewer_profile_projection,
)

_STORE_FILE = "viewer_profiles.json"
_MAX_PROFILE_TEXT = 240


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
                profiles: dict[str, dict[str, Any]] = {}
                for key, value in data.items():
                    if not isinstance(value, dict):
                        continue
                    uid = _safe_profile_uid(value.get("uid")) or _safe_profile_uid(key)
                    if not uid:
                        continue
                    profiles[uid] = _safe_profile_item(value, fallback_uid=uid)
                return profiles
        return {}

    async def _save_all(self, profiles: dict[str, dict[str, Any]]) -> bool:
        file, custom = self._resolve_file()
        if await asyncio.to_thread(self._write_json, file, profiles):
            self._active_fallback_file = None
            return True
        # 自定义目录写失败 → 回退默认目录（只告警一次，避免刷屏）。
        if custom:
            fallback = self._default_dir() / _STORE_FILE
            if await asyncio.to_thread(self._write_json, fallback, profiles):
                self._active_fallback_file = fallback
                if not self._fallback_warned:
                    self._audit("viewer_store_fallback", f"自定义目录不可写，已回退默认目录：{fallback.parent}")
                    self._fallback_warned = True
                return True
        self._audit("viewer_store_save_failed", f"档案写入失败：{file}")
        return False

    # ── 公共 API（行为与原 KV 版一致，仅底层换成 JSON）──────────────

    async def upsert_identity(self, identity: ViewerIdentity) -> ViewerProfile:
        async with self._lock:
            return await self._upsert_identity_locked(identity)

    async def _upsert_identity_locked(self, identity: ViewerIdentity) -> ViewerProfile:
        profiles = await self._load_all()
        now = utc_now_iso()
        uid = _safe_profile_uid(identity.uid)
        nickname = _safe_profile_text(identity.nickname)
        avatar_url = _safe_profile_text(identity.avatar_url)
        if not uid:
            return ViewerProfile(uid="", nickname=nickname, avatar_url=avatar_url)
        existing = profiles.get(uid)
        if existing:
            profile = ViewerProfile(
                uid=uid,
                nickname=nickname or _safe_profile_text(existing.get("nickname")) or uid,
                avatar_url=avatar_url or _safe_profile_text(existing.get("avatar_url")),
                first_seen_at=_safe_profile_text(existing.get("first_seen_at")) or now,
                last_seen_at=now,
                roast_count=public_int(existing.get("roast_count"), default=0, minimum=0),
                last_roast_at=_safe_profile_text(existing.get("last_roast_at")),
                last_result=_safe_profile_text(existing.get("last_result")),
                danmaku_count=public_int(existing.get("danmaku_count"), default=0, minimum=0),
                preference_tags=safe_preference_counts(existing.get("preference_tags")),
                favorite_topics=safe_preference_counts(existing.get("favorite_topics")),
                running_jokes=safe_preference_counts(existing.get("running_jokes")),
                interaction_style=_safe_profile_text(existing.get("interaction_style")),
                response_preference=_safe_profile_text(existing.get("response_preference")),
                last_interaction_summary=_safe_profile_text(existing.get("last_interaction_summary")),
                impression_summary=_safe_profile_text(existing.get("impression_summary")),
                avoid_guidance=_safe_profile_text(existing.get("avoid_guidance")),
                last_interaction_at=_safe_profile_text(existing.get("last_interaction_at")),
            )
        else:
            profile = ViewerProfile(
                uid=uid,
                nickname=nickname or uid,
                avatar_url=avatar_url,
                first_seen_at=now,
                last_seen_at=now,
            )
        profiles[uid] = profile.to_dict()
        await self._save_all(profiles)
        return profile

    async def record_live_danmaku(
        self,
        identity: ViewerIdentity,
        danmaku_text: str,
    ) -> ViewerProfile:
        async with self._lock:
            profiles = await self._load_all()
            now = utc_now_iso()
            uid = _safe_profile_uid(identity.uid)
            nickname = _safe_profile_text(identity.nickname)
            avatar_url = _safe_profile_text(identity.avatar_url)
            if not uid:
                return ViewerProfile(uid="", nickname=nickname, avatar_url=avatar_url)
            item = _safe_profile_item(profiles.get(uid) or {"uid": uid}, fallback_uid=uid)
            inference = infer_viewer_preferences(danmaku_text)
            profile = ViewerProfile(
                uid=uid,
                nickname=nickname or _safe_profile_text(item.get("nickname")) or uid,
                avatar_url=avatar_url or _safe_profile_text(item.get("avatar_url")),
                first_seen_at=_safe_profile_text(item.get("first_seen_at")) or now,
                last_seen_at=now,
                roast_count=public_int(item.get("roast_count"), default=0, minimum=0),
                last_roast_at=_safe_profile_text(item.get("last_roast_at")),
                last_result=_safe_profile_text(item.get("last_result")),
                danmaku_count=public_int(item.get("danmaku_count"), default=0, minimum=0) + 1,
                preference_tags=merge_preference_counts(
                    item.get("preference_tags"),
                    [str(tag) for tag in inference.get("tags", []) if str(tag).strip()],
                ),
                favorite_topics=merge_preference_counts(
                    item.get("favorite_topics"),
                    [str(tag) for tag in inference.get("favorite_topics", []) if str(tag).strip()],
                ),
                running_jokes=merge_preference_counts(
                    item.get("running_jokes"),
                    [str(tag) for tag in inference.get("running_jokes", []) if str(tag).strip()],
                ),
                interaction_style=_safe_profile_text(inference.get("interaction_style"))
                or _safe_profile_text(item.get("interaction_style")),
                response_preference=_safe_profile_text(inference.get("response_preference"))
                or _safe_profile_text(item.get("response_preference")),
                last_interaction_summary=_safe_profile_text(inference.get("summary"))
                or _safe_profile_text(item.get("last_interaction_summary")),
                impression_summary=_safe_profile_text(inference.get("impression_summary"))
                or _safe_profile_text(item.get("impression_summary")),
                avoid_guidance=_safe_profile_text(inference.get("avoid_guidance"))
                or _safe_profile_text(item.get("avoid_guidance")),
                last_interaction_at=now,
            )
            profiles[uid] = profile.to_dict()
            await self._save_all(profiles)
            return profile

    async def mark_roasted(self, uid: str, output: str) -> None:
        async with self._lock:
            await self._mark_roasted_locked(uid, output)

    async def _mark_roasted_locked(self, uid: str, output: str) -> None:
        profiles = await self._load_all()
        safe_uid = _safe_profile_uid(uid)
        if not safe_uid:
            return
        item = _safe_profile_item(profiles.get(safe_uid) or {"uid": safe_uid}, fallback_uid=safe_uid)
        item["roast_count"] = public_int(item.get("roast_count"), default=0, minimum=0) + 1
        item["last_roast_at"] = utc_now_iso()
        item["last_result"] = _safe_profile_text(output)
        profiles[safe_uid] = _safe_profile_item(item, fallback_uid=safe_uid)
        await self._save_all(profiles)

    async def has_roasted(self, uid: str) -> bool:
        profiles = await self._load_all()
        safe_uid = _safe_profile_uid(uid)
        item = profiles.get(safe_uid)
        return bool(item and public_int(item.get("roast_count"), default=0, minimum=0) > 0)

    async def recent_profiles(self, limit: int = 30) -> list[dict[str, Any]]:
        profiles = await self._load_all()
        ordered = sorted(profiles.values(), key=lambda item: str(item.get("last_seen_at") or ""), reverse=True)
        result: list[dict[str, Any]] = []
        for item in ordered[:limit]:
            safe_item = _safe_profile_item(item, fallback_uid=item.get("uid"))
            if safe_item:
                result.append({**safe_item, **viewer_profile_projection(safe_item)})
        return result

    async def clear_profiles(self) -> dict[str, Any]:
        async with self._lock:
            profiles = await self._load_all()
            cleared = len(profiles)
            persisted = await self._save_all({})
            file, _custom = self._resolve_file()
            if self._active_fallback_file is not None:
                file = self._active_fallback_file
            return {
                "cleared": cleared if persisted else 0,
                "applied": persisted,
                "path": str(file),
            }

    async def delete_profile(self, uid: str) -> dict[str, Any]:
        key = _safe_profile_uid(uid)
        if not key:
            raise ValueError("uid is required")
        async with self._lock:
            profiles = await self._load_all()
            found = key in profiles
            profiles.pop(key, None)
            persisted = await self._save_all(profiles)
            file, _custom = self._resolve_file()
            if self._active_fallback_file is not None:
                file = self._active_fallback_file
            return {
                "uid": key,
                "deleted": bool(found and persisted),
                "applied": persisted,
                "path": str(file),
            }

    async def reset_profile_impression(self, uid: str) -> dict[str, Any]:
        key = _safe_profile_uid(uid)
        if not key:
            raise ValueError("uid is required")
        impression_fields = (
            "preference_tags",
            "favorite_topics",
            "running_jokes",
            "interaction_style",
            "response_preference",
            "last_interaction_summary",
            "impression_summary",
            "avoid_guidance",
            "last_interaction_at",
        )
        async with self._lock:
            profiles = await self._load_all()
            item = profiles.get(key)
            found = isinstance(item, dict)
            persisted = False
            if found:
                for field in impression_fields:
                    if field in ("preference_tags", "favorite_topics", "running_jokes"):
                        item[field] = {}
                    else:
                        item[field] = ""
                profiles[key] = item
                persisted = await self._save_all(profiles)
            file, _custom = self._resolve_file()
            if self._active_fallback_file is not None:
                file = self._active_fallback_file
            return {
                "uid": key,
                "reset": bool(found and persisted),
                "applied": persisted,
                "path": str(file),
                "preserved_first_appearance": bool(
                    found
                    and persisted
                    and public_int(item.get("roast_count"), default=0, minimum=0) > 0
                ),
            }


def _safe_profile_uid(value: Any) -> str:
    text = public_text(value, max_len=120)
    if "[redacted]" in text:
        return ""
    return text


def _safe_profile_text(value: Any) -> str:
    return public_text(value, max_len=_MAX_PROFILE_TEXT)


def _safe_profile_item(value: dict[str, Any], *, fallback_uid: Any) -> dict[str, Any]:
    uid = _safe_profile_uid(value.get("uid")) or _safe_profile_uid(fallback_uid)
    if not uid:
        return {}
    return ViewerProfile(
        uid=uid,
        nickname=_safe_profile_text(value.get("nickname")) or uid,
        avatar_url=_safe_profile_text(value.get("avatar_url")),
        first_seen_at=_safe_profile_text(value.get("first_seen_at")),
        last_seen_at=_safe_profile_text(value.get("last_seen_at")),
        roast_count=public_int(value.get("roast_count"), default=0, minimum=0),
        last_roast_at=_safe_profile_text(value.get("last_roast_at")),
        last_result=_safe_profile_text(value.get("last_result")),
        danmaku_count=public_int(value.get("danmaku_count"), default=0, minimum=0),
        preference_tags=safe_preference_counts(value.get("preference_tags")),
        favorite_topics=safe_preference_counts(value.get("favorite_topics")),
        running_jokes=safe_preference_counts(value.get("running_jokes")),
        interaction_style=_safe_profile_text(value.get("interaction_style")),
        response_preference=_safe_profile_text(value.get("response_preference")),
        last_interaction_summary=_safe_profile_text(value.get("last_interaction_summary")),
        impression_summary=_safe_profile_text(value.get("impression_summary")),
        avoid_guidance=_safe_profile_text(value.get("avoid_guidance")),
        last_interaction_at=_safe_profile_text(value.get("last_interaction_at")),
    ).to_dict()
