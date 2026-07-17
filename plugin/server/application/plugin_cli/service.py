from __future__ import annotations

import asyncio
from dataclasses import asdict
import hashlib
import shutil
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from plugin.logging_config import get_logger
from plugin.neko_plugin_cli.core.install import PackageInstaller
from plugin.neko_plugin_cli.core.models import InstalledPlugin, InstallResult
from plugin.neko_plugin_cli.public import (
    analyze_bundle_plugins,
    inspect_package,
    build_bundle,
    build_plugin,
    install_package,
)
from plugin.server.application.install_source import (
    InstallSourceError,
    InstallSourceManager,
    classify_plugin_path,
    get_install_source_manager,
)
from plugin.server.application.plugin_cli.paths import PluginCliPathPolicy
from plugin.server.application.plugin_cli.install_plan import build_install_plan
from plugin.server.application.plugins import upgrade_support
from plugin.server.application.plugin_cli.source_resolver import (
    PluginSourceResolver,
    ResolvedPluginSource,
)
from plugin.server.domain.errors import ServerDomainError
from plugin.settings import (
    BUILTIN_PLUGIN_CONFIG_ROOT,
    USER_PACKAGE_PROFILES_ROOT,
    USER_PLUGIN_CONFIG_ROOT,
    USER_PLUGIN_PACKAGES_ROOT,
)

_PLUGIN_ROOT = Path(__file__).resolve().parents[3]
# Deprecated compatibility anchors. Package-management code below resolves
# roots through PluginCliPathPolicy.from_settings() for each operation.
_RUNTIME_PLUGINS_ROOT = BUILTIN_PLUGIN_CONFIG_ROOT
_INSTALL_PLUGINS_ROOT = USER_PLUGIN_CONFIG_ROOT
_INSTALL_PROFILES_ROOT = USER_PACKAGE_PROFILES_ROOT
_TARGET_ROOT = USER_PLUGIN_PACKAGES_ROOT

# Allowed extensions for uploaded plugin packages
_ALLOWED_UPLOAD_SUFFIXES = frozenset({".neko-plugin", ".neko-bundle"})
# Maximum upload size (200 MB)
_UPLOAD_MAX_BYTES = 200 * 1024 * 1024

logger = get_logger("server.application.plugin_cli")


def _require_within(path: Path, root: Path, *, field: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{field} must be inside {root}") from exc
    return resolved


def _require_safe_directory_name(value: str, *, field: str) -> str:
    directory_name = value.strip()
    if (
        not directory_name
        or directory_name in {".", ".."}
        or "/" in directory_name
        or "\\" in directory_name
    ):
        raise ValueError(f"{field} must be a safe plugin directory name, got {value!r}")
    return directory_name


class PluginCliService:
    async def list_local_plugins(self) -> dict[str, object]:
        return await asyncio.to_thread(self._list_local_plugins_sync)

    async def list_local_packages(self) -> dict[str, object]:
        return await asyncio.to_thread(self._list_local_packages_sync)

    async def build(
        self,
        *,
        mode: str = "selected",
        plugin: str | None = None,
        plugins: list[str] | None = None,
        plugin_ref: dict[str, Any] | None = None,
        plugin_refs: list[dict[str, Any]] | None = None,
        out: str | None = None,
        target_dir: str | None = None,
        keep_staging: bool = False,
        bundle_id: str | None = None,
        package_name: str | None = None,
        package_description: str | None = None,
        version: str | None = None,
    ) -> dict[str, object]:
        return await asyncio.to_thread(
            self._build_sync,
            mode=mode,
            plugin=plugin,
            plugins=plugins,
            plugin_ref=plugin_ref,
            plugin_refs=plugin_refs,
            out=out,
            target_dir=target_dir,
            keep_staging=keep_staging,
            bundle_id=bundle_id,
            package_name=package_name,
            package_description=package_description,
            version=version,
        )

    async def inspect(self, *, package: str) -> dict[str, object]:
        return await asyncio.to_thread(self._inspect_sync, package=package)

    async def verify(self, *, package: str) -> dict[str, object]:
        return await asyncio.to_thread(self._verify_sync, package=package)

    async def plan_install(
        self,
        *,
        package: str,
        plugins_root: str | None = None,
    ) -> dict[str, object]:
        return await asyncio.to_thread(
            self._plan_install_sync,
            package=package,
            plugins_root=plugins_root,
        )

    async def install(
        self,
        *,
        package: str,
        plugins_root: str | None = None,
        profiles_root: str | None = None,
        on_conflict: str = "fail",
        use_staging: bool = True,
        forced_directory_name: str | None = None,
        confirm_upgrade: bool = False,
        confirmation_token: str | None = None,
    ) -> dict[str, object]:
        plan_dict = await self.plan_install(package=package, plugins_root=plugins_root)
        action = str(plan_dict["action"])
        if action == "blocked":
            raise ServerDomainError(
                code="PLUGIN_INSTALL_BLOCKED",
                message="plugin package cannot be installed safely",
                status_code=409,
                details=plan_dict,
            )
        if action == "install":
            return await asyncio.to_thread(
                self._install_sync,
                package=package,
                plugins_root=plugins_root,
                profiles_root=profiles_root,
                on_conflict=on_conflict,
                use_staging=use_staging,
                forced_directory_name=forced_directory_name,
            )

        if not confirm_upgrade or not confirmation_token:
            raise ServerDomainError(
                code="PLUGIN_UPGRADE_CONFIRMATION_REQUIRED",
                message="plugin upgrade requires explicit confirmation",
                status_code=409,
                details=plan_dict,
            )
        if confirmation_token != str(plan_dict["confirmation_token"]):
            raise ServerDomainError(
                code="PLUGIN_UPGRADE_PLAN_CHANGED",
                message="installed plugin changed after upgrade confirmation",
                status_code=409,
                details=plan_dict,
            )

        policy = self._path_policy()
        target_root = (
            _require_within(
                Path(plugins_root).expanduser().resolve(),
                policy.user_plugins_root,
                field="plugins_root",
            )
            if plugins_root
            else policy.user_plugins_root
        )
        target_dir = target_root / str(plan_dict["directory_name"])
        plan = build_install_plan(
            package_path=self._resolve_package_path(package),
            plugins_root=target_root,
        )

        async def install_new() -> dict[str, object]:
            return await asyncio.to_thread(
                self._install_sync,
                package=package,
                plugins_root=plugins_root,
                profiles_root=profiles_root,
                on_conflict="fail",
                use_staging=use_staging,
                forced_directory_name=forced_directory_name,
            )

        async def validate_new() -> None:
            plugin_id = self._read_installed_plugin_toml_id(target_dir)
            if plugin_id != plan.plugin_id or target_dir.name != plan.directory_name:
                raise ValueError("installed plugin identity does not match the upgrade plan")

        async def start(plugin_id: str) -> None:
            await upgrade_support.start_plugin_after_upgrade(plugin_id, strict=True)

        try:
            result = await upgrade_support.perform_safe_upgrade(
                plan=plan,
                target_dir=target_dir,
                install_new=install_new,
                validate_new=validate_new,
                is_running=upgrade_support.plugin_is_running,
                stop=upgrade_support.stop_plugin_for_upgrade,
                start=start,
                cleanup_backup=upgrade_support.remove_directory,
            )
        except upgrade_support.SafeUpgradeError as exc:
            raise ServerDomainError(
                code="PLUGIN_UPGRADE_ROLLED_BACK",
                message="plugin upgrade failed and rollback was attempted",
                status_code=500,
                details={"stage": exc.stage, "rollback_status": exc.rollback_status},
            ) from exc

        return {
            **result.install_result,
            "operation": result.operation,
            "restarted": result.restarted,
            "rollback_status": result.rollback_status,
        }

    async def analyze(
        self,
        *,
        plugins: list[str],
        plugin_refs: list[dict[str, Any]] | None = None,
        current_sdk_version: str | None = None,
    ) -> dict[str, object]:
        return await asyncio.to_thread(
            self._analyze_sync,
            plugins=plugins,
            plugin_refs=plugin_refs,
            current_sdk_version=current_sdk_version,
        )

    # ── Upload & Download ──────────────────────────────────────────────

    async def save_uploaded_package(self, *, filename: str, content: bytes) -> dict[str, object]:
        """Save an uploaded package file to the target directory.

        Returns metadata about the saved file including its server-side path,
        which can be passed to ``install`` or ``inspect``.
        """
        return await asyncio.to_thread(self._save_uploaded_package_sync, filename=filename, content=content)

    async def upload_and_install(
        self,
        *,
        filename: str,
        content: bytes | None = None,
        package_path: str | None = None,
        on_conflict: str = "fail",
        install_source_override: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        """Upload, unpack, and atomically record the install source (design §3.3).

        ``install_source_override`` lets the caller pin the lock entry to
        ``channel="market"`` and mode (``install`` / ``upgrade`` / ``reinstall``)
        in a single call. When ``None`` this method is exactly equivalent to
        :meth:`upload_and_unpack` (no lock write).

        ``install_source_override`` schema (design §3.3.1):

        ```
        {
            "channel": "market",
            "mode": "install" | "upgrade" | "reinstall",
            "market_detail": {
                "plugin_market_id": str,
                "version": str,
                "package_url": str,
                "channel": str,            # "stable" | "beta"
                "package_sha256": str,     # 64-hex from caller; we re-verify
                "payload_hash": str | None,
                "published_at": str,       # ISO 8601
            },
        }
        ```

        Returns a dict with ``upload`` / ``unpack`` / ``install`` keys; the
        ``install`` dict mirrors :class:`SourceDetailMarket` fields. When
        warnings accrue (e.g. mismatched sha256, missing market_detail
        keys, fall back to imported channel) they are joined into an
        ``install_source_warning`` string in the return value (Req 3.4 / R10.5).

        Failure semantics (Req 3.6 / design §10.1):

        * Any exception from the save / unpack / record steps cleans up
          the saved package file and the unpacked directory before
          re-raising. The lock is never left with a half-written entry.
        * ``record_market_*`` raising :class:`InstallSourceError` with
          ``code="lock_write_failed"`` propagates verbatim so the caller
          (Bridge ``_execute_install``) can map it to the right user-facing
          error code.
        """

        if content is None and package_path is None:
            raise ValueError("upload_and_install requires content or package_path")
        if content is not None and package_path is not None:
            raise ValueError("upload_and_install accepts content or package_path, not both")

        if install_source_override is None:
            owns_saved_package = content is not None or package_path is not None
            saved: dict[str, object] | None = None
            unpacked_target_dirs: list[Path] = []
            unpacked_profile_dirs: list[Path] = []
            if package_path is not None:
                saved = await asyncio.to_thread(
                    self._save_package_file_sync,
                    filename=filename,
                    package_path=package_path,
                )
                actual_sha256 = await asyncio.to_thread(
                    self._sha256_file,
                    str(saved["path"]),
                )
            else:
                saved = await self.save_uploaded_package(
                    filename=filename,
                    content=content or b"",
                )
                actual_sha256 = hashlib.sha256(content or b"").hexdigest().lower()
            try:
                install_result = await self.install(
                    package=str(saved["path"]),
                    on_conflict=on_conflict,
                    use_staging=True,
                )
                unpacked_target_dirs = self._extract_unpack_target_dirs(install_result)
                unpacked_profile_dirs = self._extract_unpack_profile_dirs(install_result)
                warning = await self._record_install_source_best_effort(
                    install_result=install_result,
                    package_filename=str(saved["name"]),
                    package_sha256=actual_sha256,
                    override=None,
                )
                payload: dict[str, object] = {
                    "upload": saved,
                    "install": install_result,
                }
                if warning is not None:
                    payload["install_source_warning"] = warning
                return payload
            except Exception:
                self._cleanup_after_failure(
                    saved=saved,
                    unpacked_target_dirs=unpacked_target_dirs,
                    unpacked_profile_dirs=unpacked_profile_dirs,
                    delete_saved_package=owns_saved_package,
                )
                raise

        channel = install_source_override.get("channel")
        if channel != "market":
            raise ValueError(
                f"unsupported install_source_override channel: {channel!r}"
            )

        warnings: list[str] = []
        saved: dict[str, object] | None = None
        unpack_result: dict[str, object] | None = None
        unpacked_target_dirs: list[Path] = []
        unpacked_profile_dirs: list[Path] = []
        owns_saved_package = False

        try:
            # Step 1 — materialise package bytes on disk when needed.
            if package_path is not None:
                saved = await asyncio.to_thread(
                    self._save_package_file_sync,
                    filename=filename,
                    package_path=package_path,
                )
                actual_sha256 = await asyncio.to_thread(
                    self._sha256_file,
                    str(saved["path"]),
                )
                owns_saved_package = True
            else:
                saved = await self.save_uploaded_package(
                    filename=filename,
                    content=content or b"",
                )
                owns_saved_package = True
                actual_sha256 = hashlib.sha256(content or b"").hexdigest().lower()

            # Step 2 — install/unpack into the user plugin root.
            saved_path = str(saved["path"])
            install_mode = install_source_override.get("mode") or "install"
            forced_directory_name = install_source_override.get("directory_name")
            use_staging = install_mode == "install" or isinstance(
                forced_directory_name,
                str,
            )
            unpack_result = await self.install(
                package=saved_path,
                plugins_root=None,
                profiles_root=None,
                on_conflict=on_conflict,
                use_staging=use_staging,
                forced_directory_name=(
                    forced_directory_name
                    if isinstance(forced_directory_name, str)
                    else None
                ),
            )
            unpacked_target_dirs = self._extract_unpack_target_dirs(unpack_result)
            unpacked_profile_dirs = self._extract_unpack_profile_dirs(unpack_result)
            target_dir, _target_directory_plugin_id = self._extract_unpack_target(
                unpack_result
            )
            package_plugin_id = self._read_installed_plugin_toml_id(target_dir)

            # Step 4 — degrade to imported when market_detail is incomplete.
            market_detail_raw = install_source_override.get("market_detail") or {}
            market_detail = dict(market_detail_raw)
            required_keys = ("plugin_market_id", "version", "package_url")
            missing = [k for k in required_keys if not market_detail.get(k)]
            if missing:
                warnings.append(
                    f"market_detail missing required fields ({', '.join(missing)}); "
                    "falling back to imported channel"
                )
                install_dict = await self._record_imported_for_unpack(
                    target_dir=target_dir,
                    saved_filename=str(saved["name"]),
                    actual_sha256=actual_sha256,
                )
                return self._compose_install_result(
                    saved=saved,
                    unpack_result=unpack_result,
                    install_dict=install_dict,
                    warnings=warnings,
                )

            # Step 4b — plugin identity consistency check.
            # When Market tells us "this is plugin X" by passing
            # ``expected_plugin_toml_id`` (the Market plugin slug),
            # the unpacked package's plugin.toml [plugin].id is expected
            # to match. Fresh installs keep the historic soft-warning
            # behavior because Market may still publish legacy slugs, but
            # upgrade/reinstall must fail fast: the bridge rollback flow is
            # keyed to the original plugin id and directory.
            expected_toml_id = market_detail.get("expected_plugin_toml_id")
            if (
                isinstance(expected_toml_id, str)
                and expected_toml_id
                and package_plugin_id
                and expected_toml_id != package_plugin_id
            ):
                message = (
                    f"plugin identity mismatch: Market declared "
                    f"'{expected_toml_id}' but the package contains "
                    f"plugin id '{package_plugin_id}'"
                )
                if install_mode in ("upgrade", "reinstall"):
                    raise ValueError(message)
                warnings.append(
                    f"{message}; install proceeds but please verify the "
                    "package source"
                )
            # ``expected_plugin_toml_id`` is informational only — drop it
            # before passing market_detail to ISM so it does not leak into
            # the lock entry's source_detail.
            market_detail.pop("expected_plugin_toml_id", None)

            # Step 5 — overwrite hash fields with our own freshly-computed
            # values. Mismatches are warnings, not failures (R3.5 says
            # caller's value is informational; the bytes we hashed are what
            # actually landed on disk).
            caller_sha = (market_detail.get("package_sha256") or "").lower()
            if caller_sha and caller_sha != actual_sha256:
                warnings.append(
                    f"package_sha256 mismatch: market={caller_sha!r}, "
                    f"actual={actual_sha256!r}; recording actual"
                )
            market_detail["package_sha256"] = actual_sha256

            unpacked_payload_hash = unpack_result.get("payload_hash")
            if isinstance(unpacked_payload_hash, str) and unpacked_payload_hash:
                caller_payload = market_detail.get("payload_hash")
                if (
                    isinstance(caller_payload, str)
                    and caller_payload
                    and caller_payload.lower() != unpacked_payload_hash.lower()
                ):
                    warnings.append(
                        "payload_hash mismatch between market and unpacked package"
                    )
                market_detail["payload_hash"] = unpacked_payload_hash

            # Step 6 — record into ISM with the right semantic.
            mgr = self._require_install_source_manager()
            root_id, directory_name = classify_plugin_path(
                target_dir,
                builtin_root=mgr.builtin_root,
                user_root=mgr.user_root,
            )

            if install_mode in ("upgrade", "reinstall"):
                entry, ism_warnings = mgr.record_market_upgrade(
                    root_id=root_id,
                    directory_name=directory_name,
                    plugin_id=package_plugin_id,
                    market_detail=market_detail,
                )
            else:
                entry, ism_warnings = mgr.record_market_install(
                    root_id=root_id,
                    directory_name=directory_name,
                    plugin_id=package_plugin_id,
                    market_detail=market_detail,
                )
            warnings.extend(ism_warnings)

            install_dict: dict[str, Any] = {
                "channel": entry.channel,
                "directory_name": entry.directory_name,
                "plugin_id": entry.plugin_id,
            }
            if entry.source_detail is not None and hasattr(
                entry.source_detail, "version"
            ):
                # Mirror SourceDetailMarket fields for the API response.
                install_dict.update(
                    {
                        "version": getattr(entry.source_detail, "version", ""),
                        "package_sha256": getattr(
                            entry.source_detail, "package_sha256", ""
                        ),
                        "payload_hash": getattr(
                            entry.source_detail, "payload_hash", None
                        ),
                        "published_at": getattr(
                            entry.source_detail, "published_at", ""
                        ),
                        "previous_version": getattr(
                            entry.source_detail, "previous_version", None
                        ),
                    }
                )

            return self._compose_install_result(
                saved=saved,
                unpack_result=unpack_result,
                install_dict=install_dict,
                warnings=warnings,
            )

        except InstallSourceError:
            # Lock write failed — fs cleanup still runs, but propagate the
            # structured error so Bridge can map it to ``lock_write_failed``.
            self._cleanup_after_failure(
                saved=saved,
                unpacked_target_dirs=unpacked_target_dirs,
                unpacked_profile_dirs=unpacked_profile_dirs,
                delete_saved_package=owns_saved_package,
            )
            raise
        except Exception:
            self._cleanup_after_failure(
                saved=saved,
                unpacked_target_dirs=unpacked_target_dirs,
                unpacked_profile_dirs=unpacked_profile_dirs,
                delete_saved_package=owns_saved_package,
            )
            raise

    @staticmethod
    def _extract_unpack_entries(unpack_result: dict[str, object]) -> list[dict[str, object]]:
        unpacked_plugins = unpack_result.get("unpacked_plugins")
        if unpacked_plugins is None:
            unpacked_plugins = unpack_result.get("installed_plugins")
        if not isinstance(unpacked_plugins, list) or not unpacked_plugins:
            raise ValueError("install returned no plugins")
        entries: list[dict[str, object]] = []
        for item in unpacked_plugins:
            if not isinstance(item, dict):
                raise ValueError("unpack returned malformed unpacked_plugins entry")
            entries.append(item)
        return entries

    @classmethod
    def _extract_unpack_target_dirs(cls, unpack_result: dict[str, object]) -> list[Path]:
        """Return every target dir created by the unpack operation."""

        target_dirs: list[Path] = []
        for entry in cls._extract_unpack_entries(unpack_result):
            target_dir_raw = entry.get("target_dir")
            if isinstance(target_dir_raw, str) and target_dir_raw:
                target_dirs.append(Path(target_dir_raw))
        return target_dirs

    @staticmethod
    def _extract_unpack_profile_dirs(unpack_result: dict[str, object]) -> list[Path]:
        """Return promoted profile dirs created by the unpack operation."""

        profile_dir_raw = unpack_result.get("profile_dir")
        if isinstance(profile_dir_raw, str) and profile_dir_raw:
            return [Path(profile_dir_raw)]
        return []

    @classmethod
    def _extract_unpack_target(
        cls,
        unpack_result: dict[str, object],
    ) -> tuple[Path, str]:
        """Pull the single Market plugin's target dir + plugin id from a dump.

        The CLI returns potentially many ``unpacked_plugins`` for bundles,
        but Market install-source metadata and rollback are single-plugin
        flows. Reject multi-plugin Market packages before recording any lock
        entry so extra unpacked plugins cannot become untracked installs.
        """

        unpacked_plugins = cls._extract_unpack_entries(unpack_result)
        if len(unpacked_plugins) != 1:
            raise ValueError(
                "Market packages must contain exactly one plugin; "
                f"got {len(unpacked_plugins)}"
            )
        first = unpacked_plugins[0]
        target_dir_raw = first.get("target_dir")
        if not isinstance(target_dir_raw, str) or not target_dir_raw:
            raise ValueError("unpack returned no target_dir for plugin")
        target_plugin_id = str(first.get("target_plugin_id", "")) or ""
        return Path(target_dir_raw), target_plugin_id

    @staticmethod
    def _read_installed_plugin_toml_id(target_dir: Path) -> str:
        plugin_toml = target_dir / "plugin.toml"
        try:
            data = tomllib.loads(plugin_toml.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"installed plugin.toml not found: {plugin_toml}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"installed plugin.toml is invalid TOML: {plugin_toml}") from exc

        plugin_table = data.get("plugin")
        if not isinstance(plugin_table, dict):
            raise ValueError(f"installed plugin.toml missing [plugin] table: {plugin_toml}")
        plugin_id = plugin_table.get("id")
        if not isinstance(plugin_id, str) or not plugin_id.strip():
            raise ValueError(f"installed plugin.toml missing [plugin].id: {plugin_toml}")
        return plugin_id.strip()

    def _compose_install_result(
        self,
        *,
        saved: dict[str, object],
        unpack_result: dict[str, object],
        install_dict: dict[str, Any],
        warnings: list[str],
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "upload": saved,
            "unpack": unpack_result,
            "install": install_dict,
        }
        if warnings:
            result["install_source_warning"] = "; ".join(warnings)
        return result

    async def _record_imported_for_unpack(
        self,
        *,
        target_dir: Path,
        saved_filename: str,
        actual_sha256: str,
    ) -> dict[str, Any]:
        """Fall back to recording the install as ``channel="imported"``.

        Used when ``market_detail`` lacks the required keys; the user
        still gets a working plugin and we still record source-truth, just
        without the Market-side evidence.
        """

        mgr = self._require_install_source_manager()

        def _record() -> None:
            mgr.record_import(
                directory_path=target_dir,
                package_filename=saved_filename,
                package_sha256=actual_sha256,
            )

        await asyncio.to_thread(_record)
        # Build a minimal install_dict mirroring the imported entry shape
        # (no version / channel for imported channel by design).
        return {
            "channel": "imported",
            "directory_name": target_dir.name,
            "plugin_id": target_dir.name,
            "package_filename": saved_filename,
            "package_sha256": actual_sha256,
        }

    def _cleanup_after_failure(
        self,
        *,
        saved: dict[str, object] | None,
        unpacked_target_dirs: list[Path] | None = None,
        unpacked_profile_dirs: list[Path] | None = None,
        delete_saved_package: bool = True,
    ) -> None:
        """Best-effort fs cleanup on upload_and_install failure (R3.6).

        Order is important: we delete the unpacked directory first (so a
        partial extract doesn't get adopted by the next reconcile pass)
        and then the saved archive. Both calls swallow OSError because
        the original exception is what we care about — cleanup failures
        get logged but don't shadow the real error.
        """

        for unpacked_target_dir in unpacked_target_dirs or []:
            self._cleanup_failed_unpack(unpacked_target_dir)
        for unpacked_profile_dir in unpacked_profile_dirs or []:
            self._cleanup_failed_unpack(unpacked_profile_dir)
        if delete_saved_package and saved is not None:
            saved_path_raw = saved.get("path")
            if isinstance(saved_path_raw, str) and saved_path_raw:
                try:
                    Path(saved_path_raw).unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning(
                        "upload_and_install: failed to clean up saved package "
                        "{}: {}",
                        saved_path_raw,
                        exc,
                    )

    @staticmethod
    def _cleanup_failed_unpack(target_dir: Path) -> None:
        """Recursively remove ``target_dir`` ignoring missing-path errors.

        Does NOT touch the lock file — fs rollback only. The caller is
        responsible for ensuring no partial lock entry exists (we never
        write one before unpack completes).
        """

        try:
            shutil.rmtree(target_dir, ignore_errors=True)
        except OSError as exc:  # pragma: no cover — ignore_errors=True suppresses
            logger.warning(
                "upload_and_install: _cleanup_failed_unpack({}) failed: {}",
                target_dir,
                exc,
            )

    @staticmethod
    def _require_install_source_manager() -> InstallSourceManager:
        """Resolve the global manager or raise a clear configuration error.

        The manager is published by ``StartupReconciler`` during FastAPI
        lifespan startup; if a caller hits the market install path before
        that has run we want a meaningful error rather than ``AttributeError``
        on ``None.record_market_install``.
        """

        mgr = get_install_source_manager()
        if mgr is None:
            raise ServerDomainError(
                code="INSTALL_SOURCE_NOT_READY",
                message="install source manager is not initialised",
                status_code=503,
                details={"hint": "wait for FastAPI lifespan startup to complete"},
            )
        return mgr

    def resolve_download_path(self, package: str) -> Path:
        """Resolve and validate a package path for download.

        Returns the absolute path to the package file.  Raises if the file
        does not exist or is outside the target directory.
        """
        try:
            return self._resolve_package_path(package)
        except Exception as exc:
            raise self._domain_error_from_exception(exc, action="download") from exc

    # ── Sync helpers ───────────────────────────────────────────────────

    @staticmethod
    def _path_policy() -> PluginCliPathPolicy:
        return PluginCliPathPolicy.from_settings()

    def _resolver(self) -> PluginSourceResolver:
        return PluginSourceResolver(self._path_policy())

    def _list_local_plugins_sync(self) -> dict[str, object]:
        try:
            sources = self._resolver().list_plugins()
            plugins = [source.directory_name for source in sources]
            plugin_refs = [
                {
                    "root_id": source.root_id,
                    "directory_name": source.directory_name,
                    "plugin_id": source.plugin_id,
                    "label": (
                        f"{source.plugin_id} ({source.root_id}/{source.directory_name})"
                        if source.plugin_id and source.plugin_id != source.directory_name
                        else f"{source.root_id}/{source.directory_name}"
                    ),
                }
                for source in sources
            ]
            return {"plugins": plugins, "plugin_refs": plugin_refs, "count": len(sources)}
        except Exception as exc:
            raise self._domain_error_from_exception(exc, action="list_plugins") from exc

    def _list_local_packages_sync(self) -> dict[str, object]:
        try:
            target_root = self._path_policy().package_artifacts_root
            items: list[dict[str, object]] = []
            package_paths = [
                path
                for suffix in _ALLOWED_UPLOAD_SUFFIXES
                for path in target_root.glob(f"*{suffix}")
                if path.is_file()
            ]
            for path in sorted(
                package_paths,
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            ):
                stat = path.stat()
                items.append(
                    {
                        "name": path.name,
                        "path": str(path.resolve()),
                        "suffix": path.suffix,
                        "size_bytes": stat.st_size,
                        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    }
                )
            return {"packages": items, "count": len(items), "target_dir": str(target_root)}
        except Exception as exc:
            raise self._domain_error_from_exception(exc, action="list_packages") from exc

    def _build_sync(
        self,
        *,
        mode: str,
        plugin: str | None,
        plugins: list[str] | None,
        plugin_ref: dict[str, Any] | None,
        plugin_refs: list[dict[str, Any]] | None,
        out: str | None,
        target_dir: str | None,
        keep_staging: bool,
        bundle_id: str | None,
        package_name: str | None,
        package_description: str | None,
        version: str | None,
    ) -> dict[str, object]:
        try:
            policy = self._path_policy()
            target_root = policy.package_artifacts_root
            sources = self._resolve_plugin_sources(
                mode=mode,
                plugin=plugin,
                plugins=plugins or [],
                plugin_ref=plugin_ref,
                plugin_refs=plugin_refs or [],
            )
            plugin_dirs = [source.plugin_dir for source in sources]
            resolved_target_dir = Path(target_dir).expanduser().resolve() if target_dir else target_root
            _require_within(resolved_target_dir, target_root, field="target_dir")
            resolved_target_dir.mkdir(parents=True, exist_ok=True)

            if out and mode != "bundle" and len(plugin_dirs) != 1:
                raise ValueError("'out' can only be used when building a single plugin")

            if mode == "bundle":
                resolved_bundle_id = bundle_id or "__".join(sorted(item.directory_name for item in sources))
                output_path = (
                    _require_within(Path(out).expanduser().resolve(), target_root, field="out")
                    if out
                    else _require_within(
                        (resolved_target_dir / f"{resolved_bundle_id}.neko-bundle").resolve(),
                        target_root,
                        field="out",
                    )
                )
                result = build_bundle(
                    plugin_dirs,
                    output_path,
                    bundle_id=resolved_bundle_id,
                    package_name=package_name,
                    package_description=package_description,
                    version=version or "0.1.0",
                    keep_staging=keep_staging,
                )
                built = [result.model_dump(mode="json")]
                return {
                    "built": built,
                    "built_count": len(built),
                    "failed": [],
                    "failed_count": 0,
                    "ok": True,
                }

            built: list[dict[str, object]] = []
            failed: list[dict[str, object]] = []
            output_stems = self._output_stems_for_sources(sources)
            for source, plugin_dir in zip(sources, plugin_dirs, strict=True):
                output_path = (
                    _require_within(Path(out).expanduser().resolve(), target_root, field="out")
                    if out
                    else resolved_target_dir / f"{output_stems[source]}.neko-plugin"
                )
                try:
                    result = build_plugin(
                        plugin_dir,
                        output_path,
                        keep_staging=keep_staging,
                    )
                    built.append(result.model_dump(mode="json"))
                except Exception as exc:
                    failed.append({"plugin": f"{source.root_id}/{source.directory_name}", "error": str(exc)})

            return {
                "built": built,
                "built_count": len(built),
                "failed": failed,
                "failed_count": len(failed),
                "ok": not failed,
            }
        except Exception as exc:
            raise self._domain_error_from_exception(exc, action="build") from exc

    def _inspect_sync(self, *, package: str) -> dict[str, object]:
        try:
            result = inspect_package(self._resolve_package_path(package))
            return result.model_dump(mode="json")
        except Exception as exc:
            raise self._domain_error_from_exception(exc, action="inspect") from exc

    def _verify_sync(self, *, package: str) -> dict[str, object]:
        try:
            result = inspect_package(self._resolve_package_path(package))
            payload_hash_verified = result.payload_hash_verified
            return {
                **result.model_dump(mode="json"),
                "ok": payload_hash_verified is True,
            }
        except Exception as exc:
            raise self._domain_error_from_exception(exc, action="verify") from exc

    def _plan_install_sync(
        self,
        *,
        package: str,
        plugins_root: str | None,
    ) -> dict[str, object]:
        try:
            policy = self._path_policy()
            target_root = (
                _require_within(
                    Path(plugins_root).expanduser().resolve(),
                    policy.user_plugins_root,
                    field="plugins_root",
                )
                if plugins_root
                else policy.user_plugins_root
            )
            plan = build_install_plan(
                package_path=self._resolve_package_path(package),
                plugins_root=target_root,
            )
            return asdict(plan)
        except Exception as exc:
            raise self._domain_error_from_exception(exc, action="install-plan") from exc

    def _install_sync(
        self,
        *,
        package: str,
        plugins_root: str | None,
        profiles_root: str | None,
        on_conflict: str,
        use_staging: bool = True,
        forced_directory_name: str | None = None,
    ) -> dict[str, object]:
        try:
            policy = self._path_policy()
            install_plugins_root = policy.user_plugins_root
            install_profiles_root = policy.package_profiles_root
            plugins_root_path = (
                _require_within(Path(plugins_root).expanduser().resolve(), install_plugins_root, field="plugins_root")
                if plugins_root
                else install_plugins_root
            )
            profiles_root_path = (
                _require_within(Path(profiles_root).expanduser().resolve(), install_profiles_root, field="profiles_root")
                if profiles_root
                else install_profiles_root
            )
            package_path = self._resolve_package_path(package)
            if use_staging:
                result = self._install_via_staging_sync(
                    package=package_path,
                    plugins_root=plugins_root_path,
                    profiles_root=profiles_root_path,
                    on_conflict=on_conflict,
                    forced_directory_name=forced_directory_name,
                )
            elif forced_directory_name is not None:
                raise ValueError("forced_directory_name requires use_staging=True")
            else:
                result = install_package(
                    package_path,
                    plugins_root=plugins_root_path,
                    profiles_root=profiles_root_path,
                    on_conflict=on_conflict,
                )
            return result.model_dump(mode="json")
        except Exception as exc:
            raise self._domain_error_from_exception(exc, action="install") from exc

    def _install_via_staging_sync(
        self,
        *,
        package: Path,
        plugins_root: Path,
        profiles_root: Path,
        on_conflict: str,
        forced_directory_name: str | None = None,
    ) -> InstallResult:
        """Extract into a staging tree, then rename into place atomically."""

        forced_directory_name = (
            _require_safe_directory_name(forced_directory_name, field="forced_directory_name")
            if forced_directory_name is not None
            else None
        )
        staging_token = uuid.uuid4().hex
        staging_plugins = plugins_root / f".neko_staging_{staging_token}"
        staging_profiles = profiles_root / f".neko_staging_{staging_token}"
        staging_plugins.mkdir(parents=True, exist_ok=True)
        staging_profiles.mkdir(parents=True, exist_ok=True)
        installer = PackageInstaller()
        promoted_plugins: list[InstalledPlugin] = []
        promoted_profile: Path | None = None

        try:
            staged = install_package(
                package,
                plugins_root=staging_plugins,
                profiles_root=staging_profiles,
                on_conflict="fail",
            )

            for item in staged.installed_plugins:
                source_dir = Path(item.target_dir)
                desired_name = forced_directory_name or item.target_plugin_id
                desired = plugins_root / desired_name
                final_dir = installer.resolve_plugin_target_dir(
                    desired,
                )
                if source_dir.resolve() != final_dir.resolve():
                    final_dir.parent.mkdir(parents=True, exist_ok=True)
                    source_dir.rename(final_dir)
                promoted_plugins.append(
                    InstalledPlugin(
                        source_folder=item.source_folder,
                        target_plugin_id=final_dir.name,
                        target_dir=final_dir,
                        renamed=(final_dir.name != item.source_folder),
                    )
                )
                if not (final_dir / "plugin.toml").is_file():
                    raise ValueError(f"promoted plugin is missing plugin.toml: {final_dir}")

            if staged.profile_dir is not None:
                source_profile = Path(staged.profile_dir)
                desired_profile = installer.resolve_target_dir(
                    profiles_root / source_profile.name,
                    on_conflict=on_conflict,
                )
                if source_profile.resolve() != desired_profile.resolve():
                    desired_profile.parent.mkdir(parents=True, exist_ok=True)
                    source_profile.rename(desired_profile)
                promoted_profile = desired_profile

            return InstallResult(
                package_path=staged.package_path,
                package_type=staged.package_type,
                package_id=staged.package_id,
                plugins_root=plugins_root,
                profiles_root=profiles_root,
                installed_plugins=promoted_plugins,
                profile_dir=promoted_profile,
                metadata_found=staged.metadata_found,
                payload_hash=staged.payload_hash,
                payload_hash_verified=staged.payload_hash_verified,
                conflict_strategy=on_conflict,
            )
        except Exception:
            for item in promoted_plugins:
                shutil.rmtree(item.target_dir, ignore_errors=True)
            if promoted_profile is not None:
                shutil.rmtree(promoted_profile, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(staging_plugins, ignore_errors=True)
            shutil.rmtree(staging_profiles, ignore_errors=True)

    @staticmethod
    def _sha256_file(path: str | Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest().lower()

    @staticmethod
    def _package_ref_from_path(*, filename: str, package_path: str) -> dict[str, object]:
        resolved = Path(package_path).expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"package file not found: {package_path}")
        return {
            "name": filename,
            "path": str(resolved),
            "size": resolved.stat().st_size,
        }

    def _analyze_sync(
        self,
        *,
        plugins: list[str],
        plugin_refs: list[dict[str, Any]] | None,
        current_sdk_version: str | None,
    ) -> dict[str, object]:
        try:
            plugin_dirs = [
                source.plugin_dir
                for source in self._resolver().resolve_many(
                    refs=plugin_refs or [],
                    specifiers=plugins,
                )
            ]
            result = analyze_bundle_plugins(
                plugin_dirs,
                current_sdk_version=current_sdk_version,
            )
            return result.model_dump(mode="json")
        except Exception as exc:
            raise self._domain_error_from_exception(exc, action="analyze") from exc

    def _save_uploaded_package_sync(self, *, filename: str, content: bytes) -> dict[str, object]:
        try:
            target_root = self._path_policy().package_artifacts_root
            # Validate file size
            if len(content) > _UPLOAD_MAX_BYTES:
                raise ValueError(
                    f"File too large: {len(content)} bytes "
                    f"(max {_UPLOAD_MAX_BYTES // (1024 * 1024)} MB)"
                )

            # Validate and sanitize filename
            safe_name = Path(filename).name  # strip directory components
            if not safe_name:
                raise ValueError("Invalid filename")

            # Check extension — must match one of the allowed suffixes
            # Path.suffixes gives e.g. ['.neko', '-plugin'] for "foo.neko-plugin",
            # but we need the compound suffix, so we check the name directly.
            has_valid_suffix = any(safe_name.endswith(suffix) for suffix in _ALLOWED_UPLOAD_SUFFIXES)
            if not has_valid_suffix:
                allowed = ", ".join(sorted(_ALLOWED_UPLOAD_SUFFIXES))
                raise ValueError(f"Unsupported file type. Allowed: {allowed}")

            # Ensure target directory exists
            target_root.mkdir(parents=True, exist_ok=True)

            stem = safe_name
            suffix = ""
            for allowed_suffix in sorted(_ALLOWED_UPLOAD_SUFFIXES, key=len, reverse=True):
                if stem.endswith(allowed_suffix):
                    suffix = allowed_suffix
                    stem = stem[: -len(allowed_suffix)]
                    break

            # Exclusive create: if name collides (including concurrent uploads
            # racing on the same filename), pick a UUID-suffixed dest and retry.
            dest = target_root / safe_name
            while True:
                try:
                    with dest.open("xb") as file:
                        file.write(content)
                    break
                except FileExistsError:
                    unique = uuid.uuid4().hex[:8]
                    dest = target_root / f"{stem}_{unique}{suffix}"
                except Exception:
                    dest.unlink(missing_ok=True)
                    raise

            stat = dest.stat()
            return {
                "name": dest.name,
                "path": str(dest.resolve()),
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            }
        except Exception as exc:
            raise self._domain_error_from_exception(exc, action="upload") from exc

    def _save_package_file_sync(self, *, filename: str, package_path: str) -> dict[str, object]:
        """Copy an existing package into the managed package artifacts root."""

        source = Path(package_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"package file not found: {package_path}")
        if source.stat().st_size > _UPLOAD_MAX_BYTES:
            raise ValueError(
                f"File too large: {source.stat().st_size} bytes "
                f"(max {_UPLOAD_MAX_BYTES // (1024 * 1024)} MB)"
            )

        safe_name = Path(filename or source.name).name
        if not safe_name:
            raise ValueError("Invalid filename")
        has_valid_suffix = any(safe_name.endswith(suffix) for suffix in _ALLOWED_UPLOAD_SUFFIXES)
        if not has_valid_suffix:
            allowed = ", ".join(sorted(_ALLOWED_UPLOAD_SUFFIXES))
            raise ValueError(f"Unsupported file type. Allowed: {allowed}")

        target_root = self._path_policy().package_artifacts_root
        target_root.mkdir(parents=True, exist_ok=True)
        stem = safe_name
        suffix = ""
        for allowed_suffix in sorted(_ALLOWED_UPLOAD_SUFFIXES, key=len, reverse=True):
            if stem.endswith(allowed_suffix):
                suffix = allowed_suffix
                stem = stem[: -len(allowed_suffix)]
                break

        dest = target_root / safe_name
        while True:
            try:
                with source.open("rb") as src, dest.open("xb") as dst:
                    shutil.copyfileobj(src, dst)
                break
            except FileExistsError:
                unique = uuid.uuid4().hex[:8]
                dest = target_root / f"{stem}_{unique}{suffix}"
            except Exception:
                dest.unlink(missing_ok=True)
                raise

        stat = dest.stat()
        return {
            "name": dest.name,
            "path": str(dest.resolve()),
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        }

    def _resolve_plugin_sources(
        self,
        *,
        mode: str,
        plugin: str | None,
        plugins: list[str],
        plugin_ref: dict[str, Any] | None,
        plugin_refs: list[dict[str, Any]],
    ) -> list[ResolvedPluginSource]:
        resolver = self._resolver()
        if mode == "all":
            sources = resolver.list_plugins()
            if not sources:
                roots = ", ".join(f"{root_id}={root}" for root_id, root in self._path_policy().build_source_roots)
                raise FileNotFoundError(f"No plugin.toml files found under builtin or user plugin roots ({roots})")
            return sources

        if mode == "single":
            if plugin_ref is not None:
                return [resolver.resolve_plugin_ref(plugin_ref)]
            if plugin:
                return [resolver.resolve_string(plugin)]
            raise ValueError("Please provide plugin_ref or plugin when mode=single")

        if mode in {"selected", "bundle"}:
            if plugin_refs:
                return [resolver.resolve_plugin_ref(item) for item in plugin_refs]
            if plugins:
                return [resolver.resolve_string(item) for item in plugins]
            raise ValueError(f"Please provide plugin_refs or plugins when mode={mode}")

        raise ValueError("Unsupported build mode")

    @staticmethod
    def _output_stems_for_sources(sources: list[ResolvedPluginSource]) -> dict[ResolvedPluginSource, str]:
        counts: dict[str, int] = {}
        for source in sources:
            counts[source.directory_name] = counts.get(source.directory_name, 0) + 1
        return {
            source: (
                source.directory_name
                if counts[source.directory_name] == 1
                else f"{source.root_id}_{source.directory_name}"
            )
            for source in sources
        }

    def _resolve_package_path(self, raw: str) -> Path:
        target_root = self._path_policy().package_artifacts_root

        def _accept(path: Path) -> bool:
            return path.is_file() and any(
                path.name.endswith(suffix) for suffix in _ALLOWED_UPLOAD_SUFFIXES
            )

        candidate = Path(raw).expanduser()
        if candidate.exists():
            resolved = candidate.resolve()
            _require_within(resolved, target_root, field=f"package '{raw}'")
            if _accept(resolved):
                return resolved

        target_candidate = (target_root / raw).resolve()
        if target_candidate.exists():
            _require_within(target_candidate, target_root, field=f"package '{raw}'")
            if _accept(target_candidate):
                return target_candidate

        raise FileNotFoundError(f"package file not found: {raw}")

    async def _record_install_source_best_effort(
        self,
        *,
        install_result: dict,
        package_filename: str,
        package_sha256: str,
        override: dict | None,
    ) -> str | None:
        """Best-effort record the install source in the lock file (design §7.3).

        Returns ``None`` on success or a short human-readable warning
        string on failure (to be surfaced as ``install_source_warning``
        per Req 9.6 / 10.8). This helper intentionally never raises: a
        broken install-source subsystem must not mask a successful
        plugin install.
        """
        try:
            from plugin.server.application.install_source import (
                get_install_source_manager,
            )
        except Exception as exc:
            return f"install_source_import_failed: {exc}"

        mgr = get_install_source_manager()
        if mgr is None:
            return "install_source_manager_unavailable"
        if mgr.is_degraded:
            return f"install_source_manager_degraded: {mgr.degrade_reason}"

        try:
            await asyncio.to_thread(
                _record_install_source_for_install_result,
                mgr,
                install_result,
                package_filename,
                package_sha256,
                override,
            )
            return None
        except Exception as exc:
            logger.warning(
                "record_install_source failed: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            # Design §13 Fix 12: for BUILTIN_CHANNEL_LOCKED errors,
            # surface a specifically-shaped warning so ops can grep for
            # internal bug triggers.
            try:
                from plugin.server.application.install_source import InstallSourceError

                if isinstance(exc, InstallSourceError):
                    if exc.code == "BUILTIN_CHANNEL_LOCKED":
                        details = exc.details
                        return (
                            "internal_error: attempted to mutate builtin channel, "
                            f"plugin_id={details.get('plugin_id', '')} "
                            f"directory={details.get('directory_name', '')}"
                        )
                    return f"{exc.code}: {exc.message}"
            except Exception:
                pass  # classification failed; use generic fallback below
            return f"unexpected: {exc}"

    def _domain_error_from_exception(self, exc: Exception, *, action: str) -> ServerDomainError:
        if isinstance(exc, ServerDomainError):
            return exc
        if isinstance(exc, FileNotFoundError):
            status_code = 404
            code = "PLUGIN_CLI_NOT_FOUND"
        elif isinstance(exc, FileExistsError):
            status_code = 409
            code = "PLUGIN_CLI_CONFLICT"
        elif isinstance(exc, ValueError):
            status_code = 400
            code = "PLUGIN_CLI_INVALID_REQUEST"
        else:
            status_code = 500
            code = "PLUGIN_CLI_INTERNAL_ERROR"

        logger.warning(
            "plugin cli action failed: action={}, err_type={}, err={}",
            action,
            type(exc).__name__,
            str(exc),
        )
        return ServerDomainError(
            code=code,
            message=str(exc),
            status_code=status_code,
            details={"action": action, "error_type": type(exc).__name__},
        )


def _record_install_source_for_install_result(
    mgr,
    install_result: dict,
    package_filename: str,
    package_sha256: str,
    override: dict | None,
) -> None:
    """Walk ``install_result["installed_plugins"]`` and call the appropriate
    ``record_*`` method on ``mgr`` for each one (design §7.3).

    Raises :class:`InstallSourceError` with code ``"UNSUPPORTED_OVERRIDE"``
    when the caller supplies an ``override`` whose ``channel`` is not one
    of the supported values. Other ``InstallSourceError`` codes (e.g.
    ``PATH_OUTSIDE_ROOTS``, ``BUILTIN_CHANNEL_LOCKED``) propagate from
    the manager.
    """
    from plugin.server.application.install_source import InstallSourceError

    installed_plugins = install_result.get("installed_plugins", [])
    for installed in installed_plugins:
        target_dir = Path(installed["target_dir"])
        if override is None:
            mgr.record_import(
                directory_path=target_dir,
                package_filename=package_filename,
                package_sha256=package_sha256,
            )
        elif override.get("channel") == "market":
            detail = override.get("market_detail", {})
            mgr.record_market(
                directory_path=target_dir,
                plugin_market_id=detail.get("plugin_market_id", ""),
                version=detail.get("version", ""),
                package_url=detail.get("package_url", ""),
            )
        else:
            raise InstallSourceError(
                "UNSUPPORTED_OVERRIDE",
                f"unsupported override channel={override.get('channel')}",
                details={"override": override},
            )
