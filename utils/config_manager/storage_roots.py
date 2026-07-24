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

"""Storage-root resolution and filesystem layout mixin.

``__init__`` path policy decision, runtime/anchor root directories,
ensure_* directory helpers, local (non-cloud) state files and the
config/memory path lookups of :class:`ConfigManager`.
"""
import json
import os
import secrets
import sys
import threading
import uuid
from copy import deepcopy
from pathlib import Path

from config import APP_NAME, CONFIG_FILES
from utils.file_utils import atomic_write_json

from ._shared import LocalStateDirectoryError, logger


class StorageRootsMixin:
    """Storage roots, directory skeleton and local-state files."""

    @property
    def selected_root(self):
        return self.committed_selected_root

    @selected_root.setter
    def selected_root(self, value):
        self.committed_selected_root = value

    def __init__(self, app_name=None):
        """
        Initialize the config manager
        
        Args:
            app_name: application name, defaults to APP_NAME from config
        """
        self.app_name = app_name if app_name is not None else APP_NAME
        # 检测是否在子进程中，子进程静默初始化（通过 main_server 设置的环境变量）
        self._verbose = '_NEKO_MAIN_SERVER_INITIALIZED' not in os.environ
        self.docs_dir = self._get_documents_directory()
        default_app_docs_dir = self.docs_dir / self.app_name

        # CFA (Windows 受控文件夹访问/反勒索防护) 检测：
        # 如果原始 Documents 路径可读但不可写，记住它以便从中读取用户数据（模型等）
        first_readable_non_writable = getattr(self, '_first_non_writable_readable_candidate', None)
        self._cfa_fallback_write_docs_dir = None
        if (
            sys.platform == "win32"
            and first_readable_non_writable is not None
            and first_readable_non_writable != self.docs_dir
        ):
            self._readable_docs_dir = first_readable_non_writable
            self._cfa_fallback_write_docs_dir = self.docs_dir
            print("⚠ WARNING [ConfigManager] 文档目录不可写（可能受Windows安全策略/反勒索防护保护）!", file=sys.stderr)
            print(f"⚠ WARNING [ConfigManager] 原始文档路径(只读): {first_readable_non_writable}", file=sys.stderr)
            print(f"⚠ WARNING [ConfigManager] 回退写入路径: {self.docs_dir}", file=sys.stderr)
            print("⚠ WARNING [ConfigManager] 用户数据将从原始路径读取，写入操作将使用回退路径", file=sys.stderr)
        else:
            self._readable_docs_dir = None

        resolved_app_docs_dir = default_app_docs_dir
        resolved_anchor_root = default_app_docs_dir
        committed_selected_root = default_app_docs_dir
        recovery_committed_root_unavailable = False
        default_anchor_root = None
        try:
            from utils.storage_policy import (
                compute_anchor_root,
                is_runtime_root_available,
                load_storage_policy,
                normalize_runtime_root,
                paths_equal,
            )

            env_selected_root = os.environ.get("NEKO_STORAGE_SELECTED_ROOT", "").strip()
            env_anchor_root = os.environ.get("NEKO_STORAGE_ANCHOR_ROOT", "").strip()
            default_anchor_root = compute_anchor_root(self, current_root=default_app_docs_dir)
            resolved_anchor_root = default_anchor_root
            policy_anchor_root = normalize_runtime_root(env_anchor_root or default_anchor_root)
            policy = load_storage_policy(self, anchor_root=policy_anchor_root)

            if env_selected_root:
                resolved_app_docs_dir = normalize_runtime_root(env_selected_root)
                resolved_anchor_root = normalize_runtime_root(env_anchor_root or default_anchor_root)
                committed_selected_root = resolved_app_docs_dir
                if isinstance(policy, dict):
                    first_run_completed = bool(policy.get("first_run_completed"))
                    selected_root_value = str(policy.get("selected_root") or "").strip()
                    if selected_root_value:
                        committed_selected_root = normalize_runtime_root(selected_root_value)
                    anchor_root_value = str(policy.get("anchor_root") or "").strip()
                    if anchor_root_value and not env_anchor_root:
                        resolved_anchor_root = normalize_runtime_root(anchor_root_value)
                    if (
                        first_run_completed
                        and paths_equal(resolved_app_docs_dir, resolved_anchor_root)
                        and not paths_equal(committed_selected_root, resolved_anchor_root)
                        and not is_runtime_root_available(committed_selected_root)
                    ):
                        recovery_committed_root_unavailable = True
            else:
                if env_anchor_root:
                    resolved_anchor_root = normalize_runtime_root(env_anchor_root)
                if isinstance(policy, dict):
                    first_run_completed = bool(policy.get("first_run_completed"))
                    selected_root_value = str(policy.get("selected_root") or "").strip()
                    if selected_root_value:
                        committed_selected_root = normalize_runtime_root(selected_root_value)
                        resolved_app_docs_dir = committed_selected_root
                        if not env_anchor_root:
                            resolved_anchor_root = normalize_runtime_root(
                                str(policy.get("anchor_root") or "").strip() or default_anchor_root
                            )
                        if (
                            first_run_completed
                            and not paths_equal(committed_selected_root, resolved_anchor_root)
                            and not is_runtime_root_available(committed_selected_root)
                        ):
                            resolved_app_docs_dir = resolved_anchor_root
                            recovery_committed_root_unavailable = True
        except Exception as e:
            logger.warning(
                "Failed to resolve storage policy paths; falling back to default runtime root: %s",
                e,
                exc_info=True,
            )
            resolved_app_docs_dir = default_app_docs_dir
            if default_anchor_root is not None:
                resolved_anchor_root = default_anchor_root
            committed_selected_root = resolved_app_docs_dir

        self.app_docs_dir = resolved_app_docs_dir
        self.committed_selected_root = committed_selected_root
        self.anchor_root = resolved_anchor_root
        self.reported_current_root = (
            self.committed_selected_root if recovery_committed_root_unavailable else self.app_docs_dir
        )
        self.recovery_committed_root_unavailable = recovery_committed_root_unavailable
        self.recovery_committed_root_unavailable_override = False
        self.docs_dir = self.app_docs_dir.parent
        self.config_dir = self.app_docs_dir / "config"
        self.memory_dir = self.app_docs_dir / "memory"
        self.plugins_dir = self.app_docs_dir / "plugins"
        self.live2d_dir = self.app_docs_dir / "live2d"
        # VRM模型存储在用户文档目录下（与Live2D保持一致）
        self.vrm_dir = self.app_docs_dir / "vrm"
        self.vrm_animation_dir = self.vrm_dir / "animation"  # VRMA动画文件目录
        # MMD模型存储在用户文档目录下
        self.mmd_dir = self.app_docs_dir / "mmd"
        self.mmd_animation_dir = self.mmd_dir / "animation"  # VMD动画文件目录
        self.pngtuber_dir = self.app_docs_dir / "pngtuber"
        self.workshop_dir = self.app_docs_dir / "workshop"
        self._steam_workshop_path = None
        self._user_workshop_folder_persisted = False
        self.chara_dir = self.app_docs_dir / "character_cards"
        self.card_faces_dir = self.app_docs_dir / "card_faces"
        self._workshop_config_lock = threading.Lock()

        self._characters_cache: dict | None = None
        self._characters_cache_mtime: float | None = None
        self._characters_cache_path: str | None = None
        self._characters_dirty: bool = False
        self._characters_cache_lock = threading.Lock()
        self._characters_reload_lock = threading.Lock()

        self.project_config_dir = self._get_project_config_directory()
        self.project_memory_dir = self._get_project_memory_directory()

        if self.recovery_committed_root_unavailable:
            try:
                self._persist_selected_root_unavailable_recovery_state()
                self.__class__._selected_root_unavailable_recovery_override_roots.discard(
                    str(self.committed_selected_root)
                )
            except Exception as e:
                self.recovery_committed_root_unavailable_override = True
                self.__class__._selected_root_unavailable_recovery_override_roots.add(
                    str(self.committed_selected_root)
                )
                logger.warning(
                    "Failed to persist selected-root-unavailable recovery state; "
                    "continuing with in-memory recovery flag: %s",
                    e,
                    exc_info=True,
                )

    @property
    def cloudsave_dir(self) -> Path:
        """Cloud-save export root directory (the normalized export layer outside the runtime directory)."""
        return self.anchor_root / "cloudsave"

    @property
    def cloudsave_catalog_dir(self) -> Path:
        return self.cloudsave_dir / "catalog"

    @property
    def cloudsave_profiles_dir(self) -> Path:
        return self.cloudsave_dir / "profiles"

    @property
    def cloudsave_bindings_dir(self) -> Path:
        return self.cloudsave_dir / "bindings"

    @property
    def cloudsave_memory_dir(self) -> Path:
        return self.cloudsave_dir / "memory"

    @property
    def cloudsave_overrides_dir(self) -> Path:
        return self.cloudsave_dir / "overrides"

    @property
    def cloudsave_meta_dir(self) -> Path:
        return self.cloudsave_dir / "meta"

    @property
    def cloudsave_workshop_meta_dir(self) -> Path:
        return self.cloudsave_meta_dir / "workshop"

    @property
    def cloudsave_manifest_path(self) -> Path:
        return self.cloudsave_dir / "manifest.json"

    @property
    def cloudsave_staging_dir(self) -> Path:
        """Local staging area; excluded from the cloud sync whitelist."""
        return self.anchor_root / ".cloudsave_staging"

    @property
    def cloudsave_backups_dir(self) -> Path:
        """Local conflict backup pool, kept explicitly outside cloudsave/ to avoid accidental future sync."""
        return self.anchor_root / "cloudsave_backups"

    @property
    def local_state_dir(self) -> Path:
        """Local state directory, holding sync metadata that never goes to the cloud."""
        return self.anchor_root / "state"

    @property
    def root_state_path(self) -> Path:
        return self.local_state_dir / "root_state.json"

    @property
    def cloudsave_local_state_path(self) -> Path:
        return self.local_state_dir / "cloudsave_local_state.json"

    @property
    def character_tombstones_state_path(self) -> Path:
        return self.local_state_dir / "character_tombstones.json"

    def _build_selected_root_unavailable_recovery_state(self, state=None):
        unavailable_root = str(self.committed_selected_root)
        state = dict(state) if isinstance(state, dict) else {}
        state["version"] = self.ROOT_STATE_VERSION
        from utils.cloudsave_runtime import ROOT_MODE_DEFERRED_INIT

        state["mode"] = ROOT_MODE_DEFERRED_INIT
        state["current_root"] = unavailable_root
        state["last_known_good_root"] = unavailable_root
        if not str(state.get("last_migration_result") or "").strip():
            state["last_migration_result"] = f"selected_root_unavailable:{unavailable_root}"
        state.setdefault("last_migration_source", "")
        state.setdefault("last_migration_backup", "")
        state.setdefault("last_successful_boot_at", "")
        state.setdefault("legacy_cleanup_pending", False)
        return state

    def _has_selected_root_unavailable_recovery_override(self) -> bool:
        if not self.recovery_committed_root_unavailable:
            return False
        if bool(getattr(self, "recovery_committed_root_unavailable_override", False)):
            return True
        return str(self.committed_selected_root) in self.__class__._selected_root_unavailable_recovery_override_roots

    def _persist_selected_root_unavailable_recovery_state(self):
        state: dict = {}
        try:
            loaded = self._load_json_file(self.root_state_path, default_value={})
            if isinstance(loaded, dict):
                state = loaded
        except Exception:
            state = {}
        self.save_root_state(self._build_selected_root_unavailable_recovery_state(state))
    
    def _log(self, msg):
        """Print debug info only in the main process"""
        if self._verbose:
            print(msg, file=sys.stderr)

    def _can_write_existing_directory(self, directory):
        """Check whether an existing directory accepts a real write probe."""
        try:
            directory = Path(directory)
            if not directory.exists():
                return False
            if not os.access(str(directory), os.R_OK | os.W_OK):
                return False

            test_path = directory / f".test_neko_write.{uuid.uuid4().hex}.tmp"
            test_path.touch()
            test_path.unlink()
            return True
        except Exception:
            return False

    @staticmethod
    def _dedupe_paths(paths):
        unique = []
        seen = set()
        for path in paths:
            if not path:
                continue
            normalized = str(Path(path))
            if normalized in seen:
                continue
            seen.add(normalized)
            unique.append(Path(path))
        return unique

    def _get_standard_data_directory_candidates(self):
        """Return preferred app-data root directory candidates for the current platform."""
        candidates = []
        if sys.platform == "win32":
            localappdata = os.environ.get("LOCALAPPDATA", "").strip()
            if localappdata:
                candidates.append(Path(localappdata))
        elif sys.platform == "darwin":
            candidates.append(Path.home() / "Library" / "Application Support")
        else:
            xdg_data_home = os.getenv("XDG_DATA_HOME", "").strip()
            if xdg_data_home:
                candidates.append(Path(xdg_data_home))
            candidates.append(Path.home() / ".local" / "share")
        return self._dedupe_paths(candidates)

    def _get_legacy_storage_candidates(self):
        """Return parent-directory candidates of historical runtime roots, used only for legacy data import."""
        candidates = []

        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import windll, wintypes

                CSIDL_PERSONAL = 5
                SHGFP_TYPE_CURRENT = 0

                buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
                windll.shell32.SHGetFolderPathW(None, CSIDL_PERSONAL, None, SHGFP_TYPE_CURRENT, buf)
                api_path = Path(buf.value)
                candidates.append(api_path)

                if not api_path.exists() and api_path.drive:
                    drive = api_path.drive
                    for name in ("文档", "Documents", "My Documents"):
                        alt_path = Path(drive) / name
                        if alt_path.exists():
                            self._log(f"[ConfigManager] Found legacy Documents alternative: {alt_path}")
                            candidates.append(alt_path)
            except Exception as e:
                print(f"Warning: Failed to get legacy Documents path via API: {e}", file=sys.stderr)

            try:
                import winreg

                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
                )
                reg_path_str = winreg.QueryValueEx(key, "Personal")[0]
                winreg.CloseKey(key)
                reg_path = Path(os.path.expandvars(reg_path_str))
                candidates.append(reg_path)
            except Exception as e:
                print(f"Warning: Failed to get legacy Documents path from registry: {e}", file=sys.stderr)

            candidates.append(Path.home() / "Documents")
            candidates.append(Path.home() / "文档")
        elif sys.platform == "darwin":
            candidates.append(Path.home() / "Documents")
        else:
            xdg_docs = os.getenv("XDG_DOCUMENTS_DIR", "").strip()
            if xdg_docs:
                candidates.append(Path(xdg_docs))
            candidates.append(Path.home() / "Documents")

        if getattr(sys, 'frozen', False):
            candidates.append(Path(sys.executable).parent)
        candidates.append(Path.cwd())
        return self._dedupe_paths(candidates)

    def _get_legacy_document_candidates(self):
        """Return legacy document-folder candidates only."""
        candidates = []

        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import windll, wintypes

                CSIDL_PERSONAL = 5
                SHGFP_TYPE_CURRENT = 0

                buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
                windll.shell32.SHGetFolderPathW(None, CSIDL_PERSONAL, None, SHGFP_TYPE_CURRENT, buf)
                api_path = Path(buf.value)
                candidates.append(api_path)

                if not api_path.exists() and api_path.drive:
                    drive = api_path.drive
                    for name in ("文档", "Documents", "My Documents"):
                        alt_path = Path(drive) / name
                        if alt_path.exists():
                            candidates.append(alt_path)
            except Exception:
                pass

            try:
                import winreg

                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
                )
                reg_path_str = winreg.QueryValueEx(key, "Personal")[0]
                winreg.CloseKey(key)
                reg_path = Path(os.path.expandvars(reg_path_str))
                candidates.append(reg_path)
            except Exception:
                pass

            candidates.append(Path.home() / "Documents")
            candidates.append(Path.home() / "文档")
        elif sys.platform == "darwin":
            candidates.append(Path.home() / "Documents")
        else:
            xdg_docs = os.getenv("XDG_DOCUMENTS_DIR", "").strip()
            if xdg_docs:
                candidates.append(Path(xdg_docs))
            candidates.append(Path.home() / "Documents")

        return self._dedupe_paths(candidates)

    def get_legacy_app_root_candidates(self):
        """Return legacy user root directory candidates (with app_name), used for phase-0 startup import."""
        roots = []
        current_root = str(self.app_docs_dir)
        for base_dir in self._get_legacy_storage_candidates():
            app_root = base_dir / self.app_name
            if str(app_root) == current_root:
                continue
            roots.append(app_root)
        return self._dedupe_paths(roots)
    
    def _get_documents_directory(self):
        """Get the parent directory of the runtime data root.

        The method name is kept for historical compatibility, but after phase 0 it prefers
        the standard app-data directory; Documents / exe directory / cwd are only candidates
        for legacy data import and last-resort fallback.
        """
        primary_candidates = self._get_standard_data_directory_candidates()
        legacy_candidates = self._get_legacy_storage_candidates()
        legacy_document_candidates = self._get_legacy_document_candidates()
        candidates = self._dedupe_paths(primary_candidates + legacy_candidates)
        first_readable = next(
            (
                path
                for path in legacy_document_candidates
                if path.exists() and os.access(str(path), os.R_OK)
            ),
            None,
        )
        first_readable_non_writable = next(
            (
                path
                for path in legacy_document_candidates
                if path.exists()
                and os.access(str(path), os.R_OK)
                and not self._can_write_existing_directory(path)
            ),
            None,
        )
        for docs_dir in candidates:
            try:
                if docs_dir.exists():
                    if self._can_write_existing_directory(docs_dir):
                        self._log(f"[ConfigManager] ✓ Using app data directory: {docs_dir}")
                        self._first_readable_candidate = first_readable
                        self._first_non_writable_readable_candidate = first_readable_non_writable
                        return docs_dir
                    self._log(f"[ConfigManager] Path exists but not writable: {docs_dir}")
                    continue

                if not docs_dir.exists():
                    dirs_to_create = []
                    current = docs_dir
                    while current and not current.exists():
                        dirs_to_create.append(current)
                        current = current.parent
                        if current == current.parent:
                            break

                    for dir_path in reversed(dirs_to_create):
                        if not dir_path.exists():
                            dir_path.mkdir(parents=False, exist_ok=True)

                    test_path = docs_dir / ".test_neko_write"
                    test_path.touch()
                    test_path.unlink()
                    self._log(f"[ConfigManager] ✓ Using app data directory (created): {docs_dir}")
                    self._first_readable_candidate = first_readable
                    self._first_non_writable_readable_candidate = first_readable_non_writable
                    return docs_dir
            except Exception as e:
                self._log(f"[ConfigManager] Failed to use path {docs_dir}: {e}")
                continue

        self._first_readable_candidate = first_readable
        self._first_non_writable_readable_candidate = first_readable_non_writable
        fallback = Path.cwd()
        self._log(f"[ConfigManager] ⚠ All app data directories failed, using fallback: {fallback}")
        return fallback
    
    def _get_project_root(self):
        """Get the project root directory (private method).

        In source mode this is fixed to backtracking from this file's location to the repo
        root, so static, config, memory/store and other project resources never resolve to
        wrong locations due to IDE / external cwd.
        """
        if getattr(sys, 'frozen', False):
            # 如果是打包后的exe（PyInstaller）
            if hasattr(sys, '_MEIPASS'):
                # 单文件模式：使用临时解压目录
                return Path(sys._MEIPASS)
            else:
                # 多文件模式：使用 exe 同目录
                return Path(sys.executable).parent
        else:
            # 开发模式：固定使用仓库根目录
            # Two levels up from this file (utils/config_manager/storage_roots.py);
            # the former monolith computed one level up from utils/config_manager.py.
            return Path(__file__).resolve().parents[2]
    
    @property
    def project_root(self):
        """Get the project root directory (public property)"""
        return self._get_project_root()
    
    def _get_project_config_directory(self):
        """Get the project's config directory"""
        return self._get_project_root() / "config"
    
    def _get_project_memory_directory(self):
        """Get the project's memory/store directory"""
        return self._get_project_root() / "memory" / "store"
    
    def _ensure_app_docs_directory(self):
        """Ensure the app documents directory exists (the N.E.K.O directory itself)"""
        try:
            # 先确保父目录（docs_dir）存在
            if not self.docs_dir.exists():
                print(f"Warning: Documents directory does not exist: {self.docs_dir}", file=sys.stderr)
                print("Warning: Attempting to create documents directory...", file=sys.stderr)
                try:
                    # 尝试创建父目录（可能需要创建多级）
                    dirs_to_create = []
                    current = self.docs_dir
                    while current and not current.exists():
                        dirs_to_create.append(current)
                        current = current.parent
                        # 防止无限循环，到达根目录就停止
                        if current == current.parent:
                            break
                    
                    # 从最顶层开始创建目录
                    for dir_path in reversed(dirs_to_create):
                        if not dir_path.exists():
                            print(f"Creating directory: {dir_path}", file=sys.stderr)
                            dir_path.mkdir(exist_ok=True)
                except Exception as e2:
                    print(f"Warning: Failed to create documents directory: {e2}", file=sys.stderr)
                    return False
            
            # 创建应用目录
            if not self.app_docs_dir.exists():
                print(f"Creating app directory: {self.app_docs_dir}", file=sys.stderr)
                self.app_docs_dir.mkdir(exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create app directory {self.app_docs_dir}: {e}", file=sys.stderr)
            return False

    def _ensure_anchor_root_directory(self):
        """Ensure the anchor directory exists (it permanently hosts cloudsave/state)."""
        try:
            self.anchor_root.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create anchor directory {self.anchor_root}: {e}", file=sys.stderr)
            return False
    
    def ensure_config_directory(self):
        """Ensure the config directory under Documents exists"""
        try:
            # 先确保app_docs_dir存在
            if not self._ensure_app_docs_directory():
                return False
            
            self.config_dir.mkdir(exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create config directory: {e}", file=sys.stderr)
            return False
    
    def ensure_memory_directory(self):
        """Ensure the memory directory under Documents exists"""
        try:
            # 先确保app_docs_dir存在
            if not self._ensure_app_docs_directory():
                return False
            
            self.memory_dir.mkdir(exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create memory directory: {e}", file=sys.stderr)
            return False

    def ensure_plugins_directory(self):
        """Ensure the plugins directory under Documents exists"""
        try:
            if not self._ensure_app_docs_directory():
                return False

            self.plugins_dir.mkdir(exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create plugins directory: {e}", file=sys.stderr)
            return False
    
    def ensure_live2d_directory(self):
        """Ensure the live2d directory under Documents exists"""
        try:
            # 先确保app_docs_dir存在
            if not self._ensure_app_docs_directory():
                return False

            self.live2d_dir.mkdir(exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create live2d directory: {e}", file=sys.stderr)
            return False

    @property
    def readable_live2d_dir(self):
        """The live2d directory under the original Documents (read-only, for CFA scenarios).

        When Windows Controlled Folder Access (CFA / anti-ransomware protection) blocks
        writes to Documents, write operations fall back to AppData, but the user's model
        files are still in the original Documents. This property returns the live2d path
        in the original Documents for reading.

        Returns None in non-CFA scenarios (live2d_dir itself then points to Documents).
        """
        if self.is_windows_cfa_fallback_active and self._readable_docs_dir is not None:
            p = self._readable_docs_dir / self.app_name / "live2d"
            if p.exists():
                return p
        return None

    @property
    def is_windows_cfa_fallback_active(self) -> bool:
        """Whether Windows CFA read/write split mode is active."""
        if self._readable_docs_dir is None:
            return False
        write_docs_dir = getattr(self, "_cfa_fallback_write_docs_dir", None)
        if write_docs_dir is None:
            return False
        current_write_docs_dir = Path(self.app_docs_dir).parent
        return str(self._readable_docs_dir) != str(current_write_docs_dir) and str(write_docs_dir) == str(current_write_docs_dir)

    def get_live2d_lookup_roots(self, *, prefer_writable: bool = True) -> list[Path]:
        """Return Live2D lookup paths (deduped).

        Prefers the writable runtime directory by default, falling back to the read-only
        legacy directory on miss, avoiding the CFA-mode pitfall where a newly imported
        model exists but the legacy directory is still matched first.
        """
        readable = self.readable_live2d_dir
        writable = Path(self.live2d_dir)
        ordered_candidates = [writable, readable] if prefer_writable else [readable, writable]

        roots: list[Path] = []
        seen: set[str] = set()
        for candidate in ordered_candidates:
            if not candidate:
                continue
            normalized = os.path.normcase(os.path.normpath(str(candidate)))
            if normalized in seen:
                continue
            seen.add(normalized)
            roots.append(Path(candidate))
        return roots

    def ensure_vrm_directory(self):
        """Ensure the vrm directory and its animation subdirectory exist under the user documents directory"""
        try:
            # 先确保app_docs_dir存在
            if not self._ensure_app_docs_directory():
                return False
            # 创建vrm目录
            self.vrm_dir.mkdir(parents=True, exist_ok=True)
            # 创建animation子目录
            self.vrm_animation_dir.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create vrm directory: {e}", file=sys.stderr)
            return False
    
    def ensure_mmd_directory(self):
        """Ensure the mmd directory and its animation subdirectory exist under the user documents directory"""
        try:
            if not self._ensure_app_docs_directory():
                return False
            self.mmd_dir.mkdir(parents=True, exist_ok=True)
            self.mmd_animation_dir.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create mmd directory: {e}", file=sys.stderr)
            return False

    def ensure_pngtuber_directory(self):
        """Ensure the user PNGTuber asset directory exists."""
        try:
            if not self._ensure_app_docs_directory():
                return False
            self.pngtuber_dir.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create pngtuber directory: {e}", file=sys.stderr)
            return False
        
    def ensure_chara_directory(self):
        """Ensure the character_cards directory under Documents exists"""
        try:
            # 先确保app_docs_dir存在
            if not self._ensure_app_docs_directory():
                return False
            
            self.chara_dir.mkdir(exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create character_cards directory: {e}", file=sys.stderr)
            return False

    def ensure_card_faces_directory(self):
        """Ensure the card_faces directory under Documents exists"""
        try:
            if not self._ensure_app_docs_directory():
                return False
            self.card_faces_dir.mkdir(exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create card_faces directory: {e}", file=sys.stderr)
            return False

    def card_face_meta_path(self, name: str):
        """Return the catgirl card-face metadata sidecar file path (card_faces/{name}.json).

        No existence check is performed; callers must handle that themselves. Used only
        for reading/writing sidecar metadata (author, creation time, source, etc.).
        """
        return self.card_faces_dir / f"{name}.json"

    def ensure_cloudsave_structure(self):
        """Ensure the local cloudsave base directories exist.

        Only the directory skeleton and local workspace are created here, not manifest
        content, so phase 0 can land path and state infrastructure first without
        changing existing sync semantics.
        """
        try:
            if not self._ensure_anchor_root_directory():
                return False

            for directory in (
                self.cloudsave_dir,
                self.cloudsave_catalog_dir,
                self.cloudsave_profiles_dir,
                self.cloudsave_bindings_dir,
                self.cloudsave_memory_dir,
                self.cloudsave_overrides_dir,
                self.cloudsave_meta_dir,
                self.cloudsave_workshop_meta_dir,
                self.cloudsave_staging_dir,
                self.cloudsave_backups_dir,
            ):
                directory.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create cloudsave structure: {e}", file=sys.stderr)
            return False

    def ensure_local_state_directory(self):
        """Ensure the local state directory exists."""
        self._last_local_state_directory_error = None
        try:
            if self.anchor_root.exists() and not self.anchor_root.is_dir():
                raise LocalStateDirectoryError(
                    "Local state anchor root is unavailable",
                    anchor_root=self.anchor_root,
                    local_state_dir=self.local_state_dir,
                    failed_path=self.anchor_root,
                    reason="anchor_root exists but is not a directory",
                )
            self.anchor_root.mkdir(parents=True, exist_ok=True)
            if self.local_state_dir.exists() and not self.local_state_dir.is_dir():
                raise LocalStateDirectoryError(
                    "Local state directory is unavailable",
                    anchor_root=self.anchor_root,
                    local_state_dir=self.local_state_dir,
                    failed_path=self.local_state_dir,
                    reason="local_state_dir exists but is not a directory",
                )
            self.local_state_dir.mkdir(parents=True, exist_ok=True)
            probe_path = self.local_state_dir / f".neko_state_write_probe.{uuid.uuid4().hex}.tmp"
            try:
                with open(probe_path, "w", encoding="utf-8") as probe_file:
                    probe_file.write("probe")
                    probe_file.flush()
            finally:
                try:
                    probe_path.unlink()
                except FileNotFoundError:
                    pass
            return True
        except LocalStateDirectoryError as e:
            self._last_local_state_directory_error = e
            print(f"Warning: Failed to create local state directory: {e}", file=sys.stderr)
            return False
        except Exception as e:
            diagnostic = LocalStateDirectoryError(
                "Local state directory is unavailable",
                anchor_root=self.anchor_root,
                local_state_dir=self.local_state_dir,
                failed_path=self.local_state_dir,
                reason=str(e),
            )
            self._last_local_state_directory_error = diagnostic
            print(f"Warning: Failed to create local state directory: {diagnostic}", file=sys.stderr)
            return False

    def _raise_local_state_directory_error(self, operation):
        diagnostic = getattr(self, "_last_local_state_directory_error", None)
        message = f"Failed to ensure local state directory before {operation}"
        if isinstance(diagnostic, LocalStateDirectoryError):
            raise LocalStateDirectoryError(
                message,
                anchor_root=diagnostic.anchor_root,
                local_state_dir=diagnostic.local_state_dir,
                failed_path=diagnostic.failed_path,
                reason=diagnostic.reason,
            ) from diagnostic
        raise LocalStateDirectoryError(
            message,
            anchor_root=self.anchor_root,
            local_state_dir=self.local_state_dir,
            failed_path=self.local_state_dir,
            reason="ensure_local_state_directory returned False",
        )

    def _raise_local_state_file_error(self, operation, path, reason, cause=None):
        error = LocalStateDirectoryError(
            f"Failed to ensure local state file before {operation}",
            anchor_root=self.anchor_root,
            local_state_dir=self.local_state_dir,
            failed_path=path,
            reason=reason,
        )
        if cause is not None:
            raise error from cause
        raise error

    def _save_local_state_json_file(self, path, data, operation):
        path = Path(path)
        if path.exists() and not path.is_file():
            self._raise_local_state_file_error(
                operation,
                path,
                "state file target exists but is not a file",
            )
        try:
            self._save_json_file(path, data)
        except OSError as e:
            self._raise_local_state_file_error(operation, path, str(e), cause=e)

    def _load_local_state_json_file(self, path, default_value, operation):
        path = Path(path)
        if path.exists() and not path.is_file():
            self._raise_local_state_file_error(
                operation,
                path,
                "state file target exists but is not a file",
            )
        try:
            return self._load_json_file(path, default_value)
        except OSError as e:
            self._raise_local_state_file_error(operation, path, str(e), cause=e)

    def build_default_root_state(self):
        """Build default root_state content."""
        return {
            "version": self.ROOT_STATE_VERSION,
            "mode": "normal",
            "current_root": str(self.app_docs_dir),
            "last_known_good_root": str(self.app_docs_dir),
            "last_migration_source": "",
            "last_migration_backup": "",
            "last_migration_result": "",
            "last_successful_boot_at": "",
            "legacy_cleanup_pending": False,
        }

    def build_default_cloudsave_local_state(self, *, client_id=None):
        """Build default cloudsave_local_state content."""
        return {
            "version": self.CLOUDSAVE_LOCAL_STATE_VERSION,
            "client_id": str(client_id or uuid.uuid4().hex),
            "client_proof": secrets.token_urlsafe(32),
            "next_sequence_number": 1,
            "last_applied_manifest_fingerprint": "",
            "last_successful_export_at": "",
            "last_successful_import_at": "",
        }

    def build_default_character_tombstones_state(self):
        """Build default per-character tombstone local state."""
        return {
            "version": self.CHARACTER_TOMBSTONES_STATE_VERSION,
            "tombstones": [],
        }

    def _load_json_file(self, path, default_value=None):
        """Load an arbitrary JSON file; returns a copy of the default when the file is missing."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            if default_value is not None:
                return deepcopy(default_value)
            raise
        except Exception as e:
            logger.error("加载 JSON 文件失败: path=%s error=%s", path, e)
            raise

    def _save_json_file(self, path, data):
        """Atomically save an arbitrary JSON file."""
        atomic_write_json(path, data, ensure_ascii=False, indent=2)

    def load_root_state(self, default_value=None):
        """Load root_state; returns the default state when missing."""
        if default_value is None:
            default_value = self.build_default_root_state()
        state = self._load_local_state_json_file(
            self.root_state_path,
            default_value,
            "loading root_state",
        )
        if self._has_selected_root_unavailable_recovery_override():
            return self._build_selected_root_unavailable_recovery_state(state)
        return state

    def save_root_state(self, data):
        """Save root_state."""
        if not self.ensure_local_state_directory():
            self._raise_local_state_directory_error("saving root_state")
        self._save_local_state_json_file(self.root_state_path, data, "saving root_state")

    def load_cloudsave_local_state(self, default_value=None):
        """Load cloudsave_local_state; returns a default with a stable field structure when missing."""
        if default_value is None:
            default_value = self.build_default_cloudsave_local_state()
        return self._load_local_state_json_file(
            self.cloudsave_local_state_path,
            default_value,
            "loading cloudsave_local_state",
        )

    def save_cloudsave_local_state(self, data):
        """Save cloudsave_local_state."""
        if not self.ensure_local_state_directory():
            self._raise_local_state_directory_error("saving cloudsave_local_state")
        self._save_local_state_json_file(
            self.cloudsave_local_state_path,
            data,
            "saving cloudsave_local_state",
        )

    def ensure_cloudsave_client_credentials(self):
        """Return a stable client id and secret, persisting either when missing."""
        needs_persist = not self.cloudsave_local_state_path.exists()
        state = self.load_cloudsave_local_state()
        if not isinstance(state, dict):
            state = self.build_default_cloudsave_local_state()
            needs_persist = True

        client_id = state.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            client_id = uuid.uuid4().hex
            state["client_id"] = client_id
            needs_persist = True

        client_proof = state.get("client_proof")
        if not isinstance(client_proof, str) or not 32 <= len(client_proof) <= 256:
            client_proof = secrets.token_urlsafe(32)
            state["client_proof"] = client_proof
            state["version"] = self.CLOUDSAVE_LOCAL_STATE_VERSION
            needs_persist = True

        if needs_persist:
            self.save_cloudsave_local_state(state)
        return client_id, client_proof

    def load_character_tombstones_state(self, default_value=None):
        """Load per-character tombstone local state."""
        if default_value is None:
            default_value = self.build_default_character_tombstones_state()
        return self._load_local_state_json_file(
            self.character_tombstones_state_path,
            default_value,
            "loading character_tombstones_state",
        )

    def save_character_tombstones_state(self, data):
        """Save per-character tombstone local state."""
        if not self.ensure_local_state_directory():
            self._raise_local_state_directory_error("saving character_tombstones_state")
        self._save_local_state_json_file(
            self.character_tombstones_state_path,
            data,
            "saving character_tombstones_state",
        )

    def ensure_cloudsave_state_files(self):
        """Ensure local cloudsave-related state files exist; returns whether anything was created."""
        created = False
        if not self.ensure_local_state_directory():
            diagnostic = getattr(self, "_last_local_state_directory_error", None)
            diagnostic_suffix = f"\n{diagnostic}" if diagnostic is not None else ""
            raise RuntimeError(
                "Failed to initialize local state directory for "
                f"{self.root_state_path.name}, "
                f"{self.cloudsave_local_state_path.name}, and "
                f"{self.character_tombstones_state_path.name}"
                f"{diagnostic_suffix}"
            )

        if not self.root_state_path.exists():
            self.save_root_state(self.build_default_root_state())
            created = True
        if not self.cloudsave_local_state_path.exists():
            self.save_cloudsave_local_state(self.build_default_cloudsave_local_state())
            created = True
        if not self.character_tombstones_state_path.exists():
            self.save_character_tombstones_state(self.build_default_character_tombstones_state())
            created = True
        return created
    
    def get_config_path(self, filename):
        """
        Get the config file path
        
        Priority:
        1. Documents/{APP_NAME}/config/
        2. project directory/config/
        
        Args:
            filename: config file name
            
        Returns:
            Path: config file path
        """
        # 首选：我的文档下的配置
        docs_config_path = self.config_dir / filename
        if docs_config_path.exists():
            return docs_config_path
        
        # 备选：项目目录下的配置
        project_config_path = self.project_config_dir / filename
        if project_config_path.exists():
            return project_config_path
        
        # 都不存在，返回我的文档路径（用于创建新文件）
        return docs_config_path

    def get_runtime_config_path(self, filename):
        """Get the runtime source-of-truth config path (always under app_docs_dir/config)."""
        return self.config_dir / filename

    def load_json_config(self, filename, default_value=None):
        """
        Load a JSON config file
        
        Args:
            filename: config file name
            default_value: default value (when the file does not exist)
            
        Returns:
            dict: config content
        """
        config_path = self.get_config_path(filename)
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            if default_value is not None:
                return deepcopy(default_value)
            raise
        except Exception as e:
            print(f"Error loading {filename}: {e}", file=sys.stderr)
            if default_value is not None:
                return deepcopy(default_value)
            raise
    
    def save_json_config(self, filename, data, *, bypass_write_fence: bool = False):
        """
        Save a JSON config file
        
        Args:
            filename: config file name
            data: data to save
        """
        if not bypass_write_fence:
            from utils.cloudsave_runtime import assert_cloudsave_writable

            assert_cloudsave_writable(self, operation="save", target=filename)

        # 确保目录存在
        self.ensure_config_directory()
        
        config_path = self.config_dir / filename
        
        try:
            atomic_write_json(config_path, data, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving {filename}: {e}", file=sys.stderr)
            raise
    
    def get_memory_path(self, filename):
        """
        Get a memory file path
        
        Priority:
        1. Documents/{APP_NAME}/memory/
        2. project directory/memory/store/
        
        Args:
            filename: memory file name
            
        Returns:
            Path: memory file path
        """
        # 首选：我的文档下的记忆
        docs_memory_path = self.memory_dir / filename
        if docs_memory_path.exists():
            return docs_memory_path
        
        # 备选：项目目录下的记忆
        project_memory_path = self.project_memory_dir / filename
        if project_memory_path.exists():
            return project_memory_path
        
        # 都不存在，返回我的文档路径（用于创建新文件）
        return docs_memory_path
    
    def get_config_info(self):
        """Get config directory info"""
        return {
            "documents_dir": str(self.docs_dir),
            "app_dir": str(self.app_docs_dir),
            "config_dir": str(self.config_dir),
            "memory_dir": str(self.memory_dir),
            "plugins_dir": str(self.plugins_dir),
            "live2d_dir": str(self.live2d_dir),
            "readable_live2d_dir": str(self.readable_live2d_dir) if self.readable_live2d_dir else "",
            "windows_cfa_fallback_active": self.is_windows_cfa_fallback_active,
            "workshop_dir": str(self.workshop_dir),
            "chara_dir": str(self.chara_dir),
            "cloudsave_dir": str(self.cloudsave_dir),
            "cloudsave_staging_dir": str(self.cloudsave_staging_dir),
            "cloudsave_backups_dir": str(self.cloudsave_backups_dir),
            "local_state_dir": str(self.local_state_dir),
            "character_tombstones_state_path": str(self.character_tombstones_state_path),
            "project_config_dir": str(self.project_config_dir),
            "project_memory_dir": str(self.project_memory_dir),
            "config_files": {
                filename: str(self.get_config_path(filename))
                for filename in CONFIG_FILES
            }
        }
