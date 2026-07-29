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

"""Startup migration mixin.

Config/memory file migration into the runtime root, localized default
characters source selection, default card-face backfill and the soft
migration of legacy Documents memory directories.
"""
import os
import shutil
import sys
import uuid
from pathlib import Path

from config import CONFIG_FILES, DEFAULT_CONFIG_DATA


class MigrationsMixin:
    """One-shot startup migrations into the runtime root."""

    def migrate_default_card_faces(self):
        """Backfill built-in default card faces without overwriting user-created ones."""
        source_dir = self.project_config_dir.parent / "static" / "default" / "card_faces"
        if not source_dir.exists():
            return
        if not self.ensure_card_faces_directory():
            return

        try:
            source_files = list(source_dir.glob("*.png"))
        except Exception as e:
            self._log(f"Warning: Failed to scan default card faces: {e}")
            return

        for source_path in source_files:
            target_path = self.card_faces_dir / source_path.name
            if not target_path.exists():
                try:
                    shutil.copy2(source_path, target_path)
                    self._log(f"[ConfigManager] Migrated default card face: {source_path.name}")
                except Exception as e:
                    self._log(f"Warning: Failed to migrate default card face {source_path.name}: {e}")

            source_meta_path = source_path.with_suffix(".json")
            target_meta_path = self.card_face_meta_path(source_path.stem)
            if source_meta_path.exists() and not target_meta_path.exists():
                try:
                    shutil.copy2(source_meta_path, target_meta_path)
                    self._log(f"[ConfigManager] Migrated default card face meta: {source_meta_path.name}")
                except Exception as e:
                    self._log(f"Warning: Failed to migrate default card face meta {source_meta_path.name}: {e}")

    def _get_localized_characters_source(self):
        """Get the localized characters.json source file path based on user language.
        
        Returns:
            Path | None: localized file path, or None when language detection fails or the file does not exist (fall back to default)
        """
        try:
            from utils.language_utils import _get_steam_language, _get_system_language, normalize_language_code
            
            # 优先使用 Steam 语言，其次系统语言
            raw_lang = _get_steam_language()
            if not raw_lang:
                raw_lang = _get_system_language()
            if not raw_lang:
                return None
            
            lang = normalize_language_code(raw_lang, format='full')
        except Exception as e:
            self._log(f"[ConfigManager] Failed to detect language for characters config: {e}")
            return None
        
        if not lang:
            return None
        
        # 映射语言代码到文件后缀
        lang_lower = lang.lower()
        if lang_lower in ('zh-cn', 'zh'):
            suffix = 'zh-CN'
        elif 'tw' in lang_lower or 'hk' in lang_lower:
            suffix = 'zh-TW'
        elif lang_lower.startswith('ja'):
            suffix = 'ja'
        elif lang_lower.startswith('en'):
            suffix = 'en'
        elif lang_lower.startswith('ko'):
            suffix = 'ko'
        elif lang_lower.startswith('ru'):
            suffix = 'ru'
        elif lang_lower.startswith('es'):
            suffix = 'es'
        elif lang_lower.startswith('pt'):
            suffix = 'pt'
        else:
            # 未知语言，回退
            return None

        localized_path = self.project_config_dir / 'characters' / f"{suffix}.json"
        return localized_path if localized_path.exists() else None
    
    def migrate_config_files(self):
        """
        Migrate config files to Documents
        
        Strategy:
        1. Check the config folder under Documents; create it if missing
        2. For each config file:
           - if present under Documents, skip
           - if absent under Documents:
             - characters.json: pick the localized version by language, falling back to default
             - other files: copy from the project config
           - if neither exists, do nothing (defaults are created later)
        """
        # 确保目录存在
        if not self.ensure_config_directory():
            print("Warning: Cannot create config directory, using project config", file=sys.stderr)
            return
        
        # 显示项目配置目录位置（调试用）
        self._log(f"[ConfigManager] Project config directory: {self.project_config_dir}")
        self._log(f"[ConfigManager] User config directory: {self.config_dir}")
        
        # 迁移每个配置文件
        for filename in CONFIG_FILES:
            docs_config_path = self.config_dir / filename
            project_config_path = self.project_config_dir / filename
            
            # 如果我的文档下已有，跳过
            if docs_config_path.exists():
                self._log(f"[ConfigManager] Config already exists: {filename}")
                continue
            
            # 对 characters.json 特殊处理：根据语言选择本地化版本
            if filename == 'characters.json':
                lang_source = self._get_localized_characters_source()
                if lang_source:
                    try:
                        shutil.copy2(lang_source, docs_config_path)
                        self._log(f"[ConfigManager] ✓ Migrated localized config: {lang_source.name} -> {docs_config_path}")
                        continue
                    except Exception as e:
                        self._log(f"Warning: Failed to migrate localized {lang_source.name}: {e}")
                        # 继续走默认拷贝逻辑
            
            # 如果项目config下有，复制过去
            if project_config_path.exists():
                try:
                    shutil.copy2(project_config_path, docs_config_path)
                    self._log(f"[ConfigManager] ✓ Migrated config: {filename} -> {docs_config_path}")
                except Exception as e:
                    self._log(f"Warning: Failed to migrate {filename}: {e}")
            else:
                if filename in DEFAULT_CONFIG_DATA:
                    self._log(f"[ConfigManager] ~ Using in-memory default for {filename}")
                else:
                    self._log(f"[ConfigManager] ✗ Source config not found: {project_config_path}")
    
    def migrate_memory_files(self):
        """
        Migrate memory files to Documents
        
        Strategy:
        1. Check the memory folder under Documents; create it if missing
        2. Migrate all memory files and directories
        """
        # 确保目录存在
        if not self.ensure_memory_directory():
            self._log("Warning: Cannot create memory directory, using project memory")
            return
        
        # 如果项目memory/store目录不存在，跳过
        if not self.project_memory_dir.exists():
            return
        
        # 迁移所有记忆文件
        try:
            for item in self.project_memory_dir.iterdir():
                dest_path = self.memory_dir / item.name
                
                # 如果目标已存在，跳过
                if dest_path.exists():
                    continue
                
                # 复制文件或目录
                if item.is_file():
                    shutil.copy2(item, dest_path)
                    print(f"Migrated memory file: {item.name}")
                elif item.is_dir():
                    shutil.copytree(item, dest_path)
                    print(f"Migrated memory directory: {item.name}")
        except Exception as e:
            print(f"Warning: Failed to migrate memory files: {e}", file=sys.stderr)

    def migrate_legacy_documents_memory(self):
        """
        At startup, perform only a **soft migration** of ``memory/`` under legacy roots
        (``Documents\\N.E.K.O`` / original CFA read-only paths, etc.): move character
        directories still present in ``characters.json[猫娘]`` to the current runtime
        ``memory_dir``; if the runtime already has a directory of the same name, keep
        the legacy copy and print a warning — never overwrite.

        **Unlinked entries** (orphan memory whose directory name is not in
        ``characters.json[猫娘]``) are out of scope here; they are handled entirely by
        the Workshop page's "clean up legacy memory" button via
        ``/api/memory/legacy/scan`` + ``purge`` with explicit user selection.

        This method should be called after ``migrate_config_files`` /
        ``migrate_memory_files``, when ``characters.json`` is in place. Any failure is
        only logged, never raised — startup must not be blocked.
        """  # noqa: DOCSTRING_CJK
        try:
            # get_legacy_app_root_candidates 已排除当前 app_docs_dir，且去重
            legacy_roots = list(self.get_legacy_app_root_candidates() or [])
        except Exception as exc:
            self._log(
                f"[ConfigManager] migrate_legacy_documents_memory: 获取 legacy roots 失败: {exc}"
            )
            return

        # CFA 回退场景：_readable_docs_dir 是只读原 Documents，也要纳入。
        # 只读根意味着 rmtree 永远失败、target 永远存在，下面会基于
        # readonly_legacy_roots 跳过 rmtree 并静默 target_exists 噪音，
        # 避免每次启动都打"清理失败/已存在"的重复日志。
        readonly_legacy_roots: set[str] = set()
        readable_docs = getattr(self, "_readable_docs_dir", None)
        if readable_docs:
            try:
                extra = Path(readable_docs) / self.app_name
                extra_str = str(extra)
                if all(extra_str != str(existing) for existing in legacy_roots):
                    legacy_roots.append(extra)
                readonly_legacy_roots.add(extra_str)
            except Exception:
                pass

        if not legacy_roots:
            return

        try:
            characters = self.load_characters()
        except Exception as exc:
            self._log(
                f"[ConfigManager] migrate_legacy_documents_memory: 加载 characters.json 失败: {exc}"
            )
            return

        # characters.json 是用户可写边界；"猫娘" 字段若被损坏成 list / 字符串等
        # 非空但非 dict 的值，.keys() 会抛 AttributeError 并被外层吞掉。
        catgirl_map = characters.get("猫娘")
        if not isinstance(catgirl_map, dict):
            if catgirl_map is not None:
                self._log(
                    f"[ConfigManager] migrate_legacy_documents_memory: "
                    f"characters.json 中猫娘字段类型异常 "
                    f"({type(catgirl_map).__name__})，跳过本次软迁移"
                )
            else:
                self._log(
                    "[ConfigManager] migrate_legacy_documents_memory: "
                    "characters.json 中无猫娘字段，跳过本次软迁移"
                )
            return

        known_characters = set(catgirl_map.keys())
        if not known_characters:
            # characters.json 异常/为空时无从判断哪些应当迁移，直接退出。
            self._log(
                "[ConfigManager] migrate_legacy_documents_memory: "
                "characters.json 中无角色，跳过本次软迁移"
            )
            return

        # 分项计数便于运维排查"到底为什么没迁"。隐藏/下划线前缀、未关联角色
        # 这两类 skip 是正常 no-op，不单独计数。
        migrated_count = 0
        target_exists_count = 0  # runtime 已存在同名目录，保留 legacy 副本
        non_dir_count = 0  # 命中角色名但条目不是目录（反常，需关注）
        failed_count = 0  # copytree/rename 失败

        def _legacy_error_summary(exc: BaseException) -> str:
            """
            Squash the exception into a sanitized string: keep only the class name +
            errno + strerror, never printing the filename argument carried by
            OSError/PermissionError (it would expose the Documents username +
            character directory name).
            """
            if isinstance(exc, OSError):
                parts = [type(exc).__name__]
                if exc.errno is not None:
                    parts.append(f"errno={exc.errno}")
                strerror = getattr(exc, "strerror", None)
                if strerror:
                    parts.append(f"reason={strerror}")
                return " ".join(parts)
            return type(exc).__name__

        # 日志脱敏策略：所有 self._log 绝不包含完整 legacy 路径 / 角色目录名 /
        # 用户 Documents 路径，只打 root 序号 + 计数 + 条目类型。这些日志可能
        # 被收集到日志文件或遥测，泄露用户本地信息不值当。
        for legacy_root_index, legacy_root in enumerate(legacy_roots, start=1):
            source_is_readonly = str(legacy_root) in readonly_legacy_roots
            try:
                legacy_memory = Path(legacy_root) / "memory"
            except Exception:
                continue
            if not legacy_memory.exists() or not legacy_memory.is_dir():
                continue
            # 保护：绝不处理 runtime memory 自身（防御性重复检查）
            try:
                if legacy_memory.resolve() == Path(self.memory_dir).resolve():
                    continue
            except Exception:
                pass

            # Per-root 兜底：权限错误或 I/O 错误不应中断后续 legacy roots 的迁移
            try:
                legacy_entries = list(legacy_memory.iterdir())
            except Exception as exc:
                self._log(
                    f"[ConfigManager] 枚举 legacy memory 根 #{legacy_root_index} "
                    f"失败，跳过该根: {_legacy_error_summary(exc)}"
                )
                continue

            for entry in legacy_entries:
                try:
                    entry_name = entry.name
                    # 只过滤真正的隐藏条目（dot-file），其它形态的合法性交给
                    # known_characters 裁定——用户如果把角色命名为 "_foo"，
                    # 之前的 "_" 前缀黑名单会直接把它当临时条目静默跳过。
                    if entry_name.startswith("."):
                        continue

                    # 未关联条目交给手动清理按钮，此处不做任何操作
                    if entry_name not in known_characters:
                        continue

                    # runtime 角色记忆期望是目录结构（memory_dir/{name}/time_indexed.db
                    # 等）；同名普通文件会占位并阻断后续写入，必须跳过。
                    if not entry.is_dir():
                        non_dir_count += 1
                        self._log(
                            f"[ConfigManager] legacy memory 根 #{legacy_root_index}: "
                            f"命中角色名的条目不是目录（类型异常），跳过自动软迁移"
                        )
                        continue

                    target = self.memory_dir / entry_name
                    # target.exists() 对断链软链接返回 False（跟随软链找不到目标），
                    # 但 os.replace 会直接覆盖该软链接，违反"绝不覆盖 runtime 已有
                    # 目标"的语义。is_symlink() 不跟随，把断链也当成"已存在"。
                    if target.exists() or target.is_symlink():
                        # 只读根（如 CFA _readable_docs_dir）上的源永远删不掉，
                        # target 存在是上一次成功迁移后的常态；静默跳过以免每次
                        # 启动都打"已存在"日志噪音。可写根仍正常计数 + 打日志。
                        if not source_is_readonly:
                            target_exists_count += 1
                            self._log(
                                f"[ConfigManager] legacy memory 根 #{legacy_root_index}: "
                                f"目标已存在于 runtime，保留 legacy 副本避免覆盖"
                            )
                        continue
                    # 跨盘 shutil.move 退化为 copy 时若半途失败，target 可能已
                    # 存在但不完整，下次启动会被 target.exists() 跳过。改为
                    # "复制到同父级临时路径 → 原子 rename → best-effort 清源"。
                    temp_target = target.parent / f".{entry_name}.migrating-{uuid.uuid4().hex}"
                    try:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        # symlinks=False：跟随 legacy 源里的软链，把实际内容拷到
                        # runtime。若保留软链（symlinks=True），legacy 里用户手动
                        # 创建的、指向 memory_dir 外部的链接会让 runtime 的
                        # memory_dir/{name}/time_indexed.db 写入逃出边界。
                        shutil.copytree(str(entry), str(temp_target), symlinks=False)
                        os.replace(str(temp_target), str(target))
                        # 只读根（CFA _readable_docs_dir）上根本不可写，rmtree
                        # 永远会抛 PermissionError。成功迁移后直接跳过清源，
                        # 避免每次启动都打一遍"legacy 源清理失败"日志。
                        if not source_is_readonly:
                            try:
                                shutil.rmtree(str(entry))
                            except Exception as cleanup_exc:
                                self._log(
                                    f"[ConfigManager] legacy memory 根 #{legacy_root_index}: "
                                    f"已复制到 runtime，但 legacy 源清理失败，保留 legacy 副本: "
                                    f"{_legacy_error_summary(cleanup_exc)}"
                                )
                        migrated_count += 1
                        self._log(
                            f"[ConfigManager] legacy memory 根 #{legacy_root_index}: "
                            f"已迁移 1 个条目到 runtime"
                        )
                    except Exception as exc:
                        failed_count += 1
                        # 清理可能残留的临时目录/文件，避免下次启动误判
                        try:
                            if temp_target.exists():
                                if temp_target.is_dir():
                                    shutil.rmtree(str(temp_target), ignore_errors=True)
                                else:
                                    temp_target.unlink()
                        except Exception:
                            pass
                        self._log(
                            f"[ConfigManager] legacy memory 根 #{legacy_root_index}: "
                            f"迁移条目失败: {_legacy_error_summary(exc)}"
                        )
                except Exception as exc:
                    failed_count += 1
                    self._log(
                        f"[ConfigManager] legacy memory 根 #{legacy_root_index}: "
                        f"处理条目时出错: {_legacy_error_summary(exc)}"
                    )

        if migrated_count or target_exists_count or non_dir_count or failed_count:
            self._log(
                f"[ConfigManager] legacy memory 软迁移汇总: "
                f"迁移 {migrated_count} 个, "
                f"目标已存在跳过 {target_exists_count} 个, "
                f"非目录跳过 {non_dir_count} 个, "
                f"失败 {failed_count} 个"
            )
