# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Persistence methods for the memory manager."""

from __future__ import annotations

import asyncio

import json

import os


from datetime import datetime




from utils.cloudsave_runtime import assert_cloudsave_writable


from utils.file_utils import (
    atomic_write_json,
    atomic_write_json_async,
    read_json_async,
)






from memory._reflection.schema import (
    make_archive_stamper,
    normalize_reflection,
    prepare_save_reflections,
)




from ._shared import (
    logger,
    REFLECTION_TERMINAL_STATUSES,
)

class PersistenceMixin:
    def _reflections_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'reflections.json')

    def _reflections_archive_dir(self, name: str) -> str:
        """Sharded archive directory (RFC §3.5.4).

        Replaces the legacy flat ``reflections_archive.json`` file. The
        directory is created lazily by `aappend_to_shard` on first
        archive event so an idle character never carries an empty dir.
        """
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            'reflection_archive',
        )

    def _reflections_legacy_archive_path(self, name: str) -> str:
        """Legacy flat archive path (pre-RFC §3.5.4). Kept for the
        one-shot migration in `aone_shot_archive_migration` and to keep
        the migration sentinel test-discoverable. NOT referenced by any
        write path."""
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            'reflections_archive.json',
        )

    def _surfaced_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'surfaced.json')

    def _synth_backoff_path(self, name: str) -> str:
        """Per-character sidecar tracking per-rid synthesis failure counts.

        When the LLM fails, synthesize_reflections has no entity to hang
        attempts on (the reflection doesn't exist yet), so failure counts land
        in this ``{rid: attempts}`` map. rid is determined by source_fact_ids
        (deterministic) — once the facts change the rid changes and attempts
        naturally reset, so a persistently failing fact batch gets
        dead-lettered while new input retries immediately.
        """
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            'synth_backoff.json',
        )

    async def _aload_synth_backoff_from_disk(self, name: str) -> dict:
        """Read the {key: {"n": attempts(int), "at": last_fail_iso|None}} map from disk;
        missing / corrupt file → empty dict. Compatible with the old format
        {key: int} (at backfilled as None)."""
        path = self._synth_backoff_path(name)
        if not await asyncio.to_thread(os.path.exists, path):
            return {}
        try:
            data = await read_json_async(path)
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        out: dict[str, dict] = {}
        for k, v in data.items():
            if isinstance(v, dict):
                try:
                    out[str(k)] = {"n": int(v.get("n", 0)), "at": v.get("at")}
                except (TypeError, ValueError):
                    continue
            else:
                # 旧格式 {key: int}
                try:
                    out[str(k)] = {"n": int(v), "at": None}
                except (TypeError, ValueError):
                    continue
        return out

    async def _aload_synth_backoff(self, name: str) -> dict:
        """Return the failure-backoff map. Prefers the in-process mirror (the freshest
        working copy — counts survive even if the last disk write failed);
        loads from disk and caches on first access for a character."""
        cached = self._synth_backoff_mem.get(name)
        if cached is not None:
            return cached
        loaded = await self._aload_synth_backoff_from_disk(name)
        self._synth_backoff_mem[name] = loaded
        return loaded

    async def _asave_synth_backoff(self, name: str, backoff: dict) -> None:
        """Update the in-process mirror first (authoritative within the session, so a
        failed disk write never loses the throttle), then best-effort persist.
        A write failure WARNs without raising — the mirror is already in effect
        and the dead-letter gate keeps working; only a restart reads from disk,
        and that is a different recovery context anyway (Codex P2)."""
        self._synth_backoff_mem[name] = backoff
        try:
            await atomic_write_json_async(
                self._synth_backoff_path(name), backoff,
                indent=2, ensure_ascii=False,
            )
        except Exception as e:
            logger.warning(
                f"[Reflection] {name}: synth_backoff 写盘失败（进程内镜像仍生效，"
                f"throttle 不受影响）: {e}"
            )

    async def _abump_synth_backoff(self, name: str, key: str, reason: str) -> int:
        """Record one synthesis failure for key; returns the cumulative count and
        stamps the failure time (for time-based self-healing). The caller holds
        no lock (failure branches all run outside the post-LLM lock), so this
        takes _get_alock itself for the read-modify-write."""
        async with self._get_alock(name):
            # 拷贝一份再改：_aload_synth_backoff 返回的是进程内镜像引用，
            # 由 _asave_synth_backoff 统一回写，避免半改状态被并发读看到。
            backoff = dict(await self._aload_synth_backoff(name))
            attempts = backoff.get(key, {}).get("n", 0) + 1
            entry = {"n": attempts, "at": datetime.now().isoformat()}
            backoff[key] = entry
            # 防失败 key 长期累积：超阈值时只留 attempts 最高的若干条
            # （dead-letter 候选优先保活，被丢的低分 key 下次失败重新计起）。
            if len(backoff) > 64:
                backoff = dict(
                    sorted(backoff.items(), key=lambda kv: -kv[1].get("n", 0))[:64]
                )
                backoff[key] = entry  # 确保当前 key 不被裁掉
            await self._asave_synth_backoff(name, backoff)
        logger.debug(
            f"[Reflection] {name}: key {key} 合成失败退避 → {attempts} ({reason})"
        )
        return attempts

    async def _aclear_synth_backoff(self, name: str, key: str) -> None:
        """Clear a key's failure record after it persists successfully. Caller holds no lock."""
        async with self._get_alock(name):
            backoff = dict(await self._aload_synth_backoff(name))
            if key in backoff:
                del backoff[key]
                await self._asave_synth_backoff(name, backoff)

    @staticmethod
    def _normalize_reflection(entry: dict) -> dict:
        """Fill current schema defaults in-place (compatibility seam)."""
        return normalize_reflection(entry)

    @classmethod
    def _filter_reflections(cls, data, include_archived: bool, path: str) -> list[dict]:
        if not isinstance(data, list):
            logger.warning(f"[Reflection] reflections 文件不是列表，忽略: {path}")
            return []
        items = [
            cls._normalize_reflection(item)
            for item in data if isinstance(item, dict) and 'id' in item
        ]
        if not include_archived:
            # Hides every terminal status (promoted / denied / merged /
            # archived / promote_blocked) from active reads — reading from
            # the shared constant so PR-3's promote_blocked dead-letter
            # state is excluded without an additional edit here.
            items = [
                r for r in items
                if r.get('status') not in REFLECTION_TERMINAL_STATUSES
            ]
        return items

    def load_reflections(self, name: str, include_archived: bool = False) -> list[dict]:
        path = self._reflections_path(name)
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                return self._filter_reflections(data, include_archived, path)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[Reflection] 加载失败: {e}")
        return []

    async def aload_reflections(self, name: str, include_archived: bool = False) -> list[dict]:
        path = self._reflections_path(name)
        if not await asyncio.to_thread(os.path.exists, path):
            return []
        try:
            data = await read_json_async(path)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[Reflection] 加载失败: {e}")
            return []
        return self._filter_reflections(data, include_archived, path)

    def _prepare_save_reflections(
        self, name: str, reflections: list[dict], all_on_disk: list[dict],
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """Compute archive split without I/O (compatibility seam)."""
        del name  # retained in the method signature for existing callers
        return prepare_save_reflections(reflections, all_on_disk)

    @staticmethod
    def _make_archive_stamper(now_iso: str):
        """Build the per-shard archive-stamping callback."""
        return make_archive_stamper(now_iso)

    def save_reflections(self, name: str, reflections: list[dict]) -> None:
        """Save reflections, archiving stale promoted/denied entries to shards.

        promoted/denied entries older than _REFLECTION_ARCHIVE_DAYS are moved
        automatically into the sharded archive directory (RFC §3.5.4). This path covers the EXISTING age-based archival —
        score-driven `sub_zero_days >= EVIDENCE_ARCHIVE_DAYS` archival uses
        the dedicated `aarchive_reflection` event-sourced path instead.
        """
        from memory.archive_shards import append_to_shard_sync
        assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/reflections.json",
        )
        path = self._reflections_path(name)
        all_on_disk = []
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    all_on_disk = [r for r in data if isinstance(r, dict)]
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[Reflection] {name}: 读取现有 reflections 失败，中止保存以保护归档数据: {e}")
                return

        merged, to_archive, _ = self._prepare_save_reflections(name, reflections, all_on_disk)

        if to_archive:
            archive_dir = self._reflections_archive_dir(name)
            try:
                stamper = self._make_archive_stamper(datetime.now().isoformat())
                shard_path = append_to_shard_sync(
                    archive_dir, list(to_archive), stamper=stamper,
                )
                logger.info(
                    f"[Reflection] {name}: 归档 {len(to_archive)} 条旧 reflections → {os.path.basename(shard_path)}"
                )
            except OSError as e:
                # Fall-back: keep the entries in main file rather than lose
                # them. Same posture as the old flat-file path.
                logger.warning(
                    f"[Reflection] {name}: 写归档分片失败，回滚到 main 保留: {e}"
                )
                merged = merged + to_archive

        atomic_write_json(path, merged, indent=2, ensure_ascii=False)

    async def asave_reflections(self, name: str, reflections: list[dict]) -> None:
        from memory.archive_shards import aappend_to_shard
        assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/reflections.json",
        )
        path = self._reflections_path(name)
        all_on_disk = []
        if await asyncio.to_thread(os.path.exists, path):
            try:
                data = await read_json_async(path)
                if isinstance(data, list):
                    all_on_disk = [r for r in data if isinstance(r, dict)]
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[Reflection] {name}: 读取现有 reflections 失败，中止保存以保护归档数据: {e}")
                return

        merged, to_archive, _ = self._prepare_save_reflections(name, reflections, all_on_disk)

        if to_archive:
            archive_dir = self._reflections_archive_dir(name)
            try:
                stamper = self._make_archive_stamper(datetime.now().isoformat())
                shard_path = await aappend_to_shard(
                    archive_dir, list(to_archive), stamper=stamper,
                )
                logger.info(
                    f"[Reflection] {name}: 归档 {len(to_archive)} 条旧 reflections → {os.path.basename(shard_path)}"
                )
            except OSError as e:
                logger.warning(
                    f"[Reflection] {name}: 写归档分片失败，回滚到 main 保留: {e}"
                )
                merged = merged + to_archive

        await atomic_write_json_async(path, merged, indent=2, ensure_ascii=False)

    def load_surfaced(self, name: str) -> list[dict]:
        """Load the list of reflections that were surfaced in proactive chat."""
        path = self._surfaced_path(name)
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return [item for item in data if isinstance(item, dict)]
                logger.warning(f"[Reflection] surfaced 文件不是列表，忽略: {path}")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[Reflection] 加载 surfaced 失败: {e}")
        return []

    async def aload_surfaced(self, name: str) -> list[dict]:
        path = self._surfaced_path(name)
        if not await asyncio.to_thread(os.path.exists, path):
            return []
        try:
            data = await read_json_async(path)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[Reflection] 加载 surfaced 失败: {e}")
            return []
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        logger.warning(f"[Reflection] surfaced 文件不是列表，忽略: {path}")
        return []

    def save_surfaced(self, name: str, surfaced: list[dict]) -> None:
        assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/surfaced.json",
        )
        atomic_write_json(self._surfaced_path(name), surfaced, indent=2, ensure_ascii=False)

    async def asave_surfaced(self, name: str, surfaced: list[dict]) -> None:
        assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/surfaced.json",
        )
        await atomic_write_json_async(self._surfaced_path(name), surfaced, indent=2, ensure_ascii=False)
