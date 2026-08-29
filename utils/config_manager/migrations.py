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
import tempfile
import sys
import uuid
from pathlib import Path

from config import CONFIG_FILES, DEFAULT_CONFIG_DATA


# Staging lives in ONE directory this migration owns. The sentinel inside it
# is what proves ownership -- a dot-prefixed character name is legal, so a
# name match alone would have deleted a real character's memory.
_MIGRATION_STAGING_DIR = ".migrating-staging"
_MIGRATION_STAGING_SENTINEL = ".staging-owner"


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
    
    def _prepare_migration_staging_root(self):
        """Return a staging directory this migration OWNS, proven by a sentinel.

        Sweeping every ``.migrating-*`` entry in the memory root was
        destructive: a dot-prefixed character name is accepted by the
        runtime, so a real character called ``.migrating-Carol`` had its live
        memory recursively deleted and, if no seed of that name existed,
        lost outright. Ownership is proven by a file we wrote, never
        inferred from the name.

        Everything staged goes inside this one directory, so a run killed
        outright leaves exactly one thing behind and the next run reclaims
        it. If the name is already taken by something that is not ours, a
        unique one is used and the stranger is left untouched.
        """
        base = self.memory_dir / _MIGRATION_STAGING_DIR
        # A symlink is never ours, whatever it points at. rmtree leaves a
        # directory symlink in place, and mkdir(exist_ok=True) then succeeds
        # through it -- so without this we would stage into whatever it
        # targets, outside memory_dir entirely.
        # The PROJECT side matters too. If it holds an entry of this name, our
        # staging root occupies the very destination that entry would migrate
        # to: the loop then sees dest_path already present and skips it, the
        # finally removes the staging root, and that entry has silently never
        # migrated while looking as though it had.
        taken_by_project = (
            self.project_memory_dir / _MIGRATION_STAGING_DIR
        ).exists()
        if base.is_symlink() or taken_by_project or (
            base.exists() and not (base / _MIGRATION_STAGING_SENTINEL).exists()
        ):
            # Reclaim OUR earlier fallback roots before minting another one.
            # Each run on this path picks a fresh uuid, so a run killed after
            # creating one leaves it referenced by nobody: the next run mints
            # a different name and the old copy sits there forever. Repeat
            # that and full staging copies accumulate. Same ownership rule as
            # the base name -- a sentinel we wrote, and never a symlink.
            for previous in self.memory_dir.glob(
                _MIGRATION_STAGING_DIR + "-*"
            ):
                if previous.is_symlink() or not previous.is_dir():
                    continue
                if (previous / _MIGRATION_STAGING_SENTINEL).exists():
                    shutil.rmtree(previous, ignore_errors=True)
            base = self.memory_dir / (
                _MIGRATION_STAGING_DIR + "-" + uuid.uuid4().hex
            )
        shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True, exist_ok=True)
        (base / _MIGRATION_STAGING_SENTINEL).write_text("", encoding="utf-8")
        return base

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
        
        # 一次未完成的拷贝不能留下半份目录。旧写法直接 copytree 到目标位置，
        # 中途断电/进程被杀就会留下一个残缺的 memory/<name>/——而顶层跳过
        # 看到目录已存在就再也不管它，那些文件从此进不来。没有读者会去看
        # 项目根（facts、persona、settings、三个 sidecar、时间索引全部经由
        # ensure_character_dir(memory_dir, name) 解析，项目根只在枚举时被
        # 扫到），所以那个角色会被列进选择器却分析出一片空白。
        #
        # 先拷进同盘的暂存目录、成功后整体 rename 就位：要么目标完整存在，
        # 要么根本不存在，没有中间态。中断后下次启动因为目标不存在会重新
        # 完整拷一遍，比事后逐个补空缺更彻底——补空缺无法识别「被截断的
        # 文件」，它同样满足 exists()。
        #
        # 反过来也重要：目标已存在时一律不碰。「文件不在运行时根」不等于
        # 「它没拷过来」——云端导入会有意删掉受管文件并 unlink，用户也会
        # 删。往里补会在每次启动复活它们，拿数据搁浅换数据删不掉，是更糟
        # 的一边。
        # INSIDE the try. A full disk, a read-only root or a permission problem
        # makes creating the staging root raise, and this runs on the startup
        # path -- outside the handler it would take get_config_manager() down
        # with it and migrate nothing. A migration that cannot start is a
        # migration skipped, not a broken launch.
        staging_root = None
        try:
            staging_root = self._prepare_migration_staging_root()
            for item in self.project_memory_dir.iterdir():
                # 每个条目单独兜底。这个 try 原本只包在整个循环外面，于是
                # 第一个失败的条目会把它后面所有角色和散文件一起留在项目
                # 根里——比这次要修的那个缺口更糟。
                try:
                    dest_path = self.memory_dir / item.name

                    # 目标已存在（任何类型、包括坏掉的符号链接）就不碰。
                    # lexists 而不是 exists：断链的 exists() 是 False，
                    # 而 copy2 会顺着它写到 memory_dir 外面去。
                    if dest_path.is_symlink() or dest_path.exists():
                        continue

                    if item.is_symlink():
                        # 种子目录里的链接不跟：拷它的目标等于把 memory_dir
                        # 外面的东西搬进来。
                        print(
                            f"Warning: skip {item.name}: project entry is a symlink",
                            file=sys.stderr,
                        )
                        continue

                    if item.is_file():
                        # 同样要原子化。散文件这一支原先是裸 copy2，中断会
                        # 在目标留下一个被截断的文件——而「目标已存在就跳过」
                        # 会把它永久当成迁移完成，正是目录那支要避免的东西。
                        # mkstemp, NOT a name built from item.name: a source
                        # already close to the filesystem name limit pushes
                        # the staged name over it and the copy fails with
                        # ENAMETOOLONG.
                        handle, staged_name = tempfile.mkstemp(
                            dir=str(staging_root)
                        )
                        os.close(handle)
                        staged_file = Path(staged_name)
                        try:
                            shutil.copy2(item, staged_file)
                            os.replace(staged_file, dest_path)
                        finally:
                            if staged_file.exists():
                                try:
                                    staged_file.unlink()
                                except OSError:
                                    # Best effort, and deliberately silent:
                                    # this runs while an earlier failure is
                                    # propagating, so raising here would
                                    # replace the real cause with a cleanup
                                    # error. What is left behind is inside
                                    # the staging root, which comes down
                                    # wholesale in the outer finally and is
                                    # reclaimed by the next run regardless.
                                    pass
                        print(f"Migrated memory file: {item.name}")
                        continue

                    if not item.is_dir():
                        continue

                    # 同盘暂存 → 原子 rename。暂存目录用点前缀，且只在这
                    # 一小段时间内存在；角色枚举只看目录名与已知文件模式，
                    # 不会把它当成角色。
                    staging = Path(
                        tempfile.mkdtemp(dir=str(staging_root))
                    ) / item.name
                    try:
                        shutil.copytree(item, staging)
                        staging.rename(dest_path)
                        print(f"Migrated memory directory: {item.name}")
                    finally:
                        shutil.rmtree(staging.parent, ignore_errors=True)
                except Exception as exc:
                    print(
                        f"Warning: Failed to migrate memory entry {item.name}: {exc}",
                        file=sys.stderr,
                    )
        except Exception as e:
            print(f"Warning: Failed to migrate memory files: {e}", file=sys.stderr)
        finally:
            if staging_root is not None:
                shutil.rmtree(staging_root, ignore_errors=True)

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
