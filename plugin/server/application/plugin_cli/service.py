from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path

from plugin.logging_config import get_logger
from plugin.neko_plugin_cli.public import (
    analyze_bundle_plugins,
    inspect_package,
    build_bundle,
    build_plugin,
    install_package,
)
from plugin.server.domain.errors import ServerDomainError
from plugin.settings import (
    USER_PACKAGE_PROFILES_ROOT,
    USER_PLUGIN_CONFIG_ROOT,
    USER_PLUGIN_PACKAGES_ROOT,
)

_PLUGIN_ROOT = Path(__file__).resolve().parents[3]
# 源仓库内置插件目录：用于 list/build（只读扫描）。
_RUNTIME_PLUGINS_ROOT = _PLUGIN_ROOT / "plugins"
# install（导入）目标目录：统一落到用户我的文档下的 plugins 配置根。
_INSTALL_PLUGINS_ROOT = USER_PLUGIN_CONFIG_ROOT
_INSTALL_PROFILES_ROOT = USER_PACKAGE_PROFILES_ROOT
# build/upload 产物（``.neko-plugin`` / ``.neko-bundle``）落地目录：
# 必须落到用户可写的我的文档目录，否则 Nuitka 打包安装到 Program Files 后会失败。
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

    async def install(
        self,
        *,
        package: str,
        plugins_root: str | None = None,
        profiles_root: str | None = None,
        on_conflict: str = "rename",
    ) -> dict[str, object]:
        return await asyncio.to_thread(
            self._install_sync,
            package=package,
            plugins_root=plugins_root,
            profiles_root=profiles_root,
            on_conflict=on_conflict,
        )

    async def analyze(
        self,
        *,
        plugins: list[str],
        current_sdk_version: str | None = None,
    ) -> dict[str, object]:
        return await asyncio.to_thread(
            self._analyze_sync,
            plugins=plugins,
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
        content: bytes,
        on_conflict: str = "rename",
        install_source_override: dict | None = None,
    ) -> dict[str, object]:
        """Upload a package file and immediately install it.

        Combines ``save_uploaded_package`` and ``install`` into a single operation
        for convenience.

        After a successful install, records the install source in the
        install-source lock (design §7 / Req 9). When ``install_source_override``
        is ``None`` (the default, used by the direct ``/plugin-cli/upload-and-install``
        route), the entry is recorded with ``channel="imported"`` and
        ``source_detail.package_filename`` / ``source_detail.package_sha256``
        derived from the upload. When ``install_source_override`` is provided
        (used by ``market_bridge`` — design Fix 8), it drives the subsequent
        call to ``record_market`` instead, bypassing the imported channel
        entirely so the lock file never passes through an ``imported →
        market`` intermediate state.

        If the install-source manager is unavailable, degraded, or
        otherwise raises during recording, the response body still
        returns the original upload + install payload plus an
        ``install_source_warning`` field describing the issue (Req 9.6
        / 10.8). The HTTP status stays 200 — install-source failures
        must never mask a successful install.
        """
        import hashlib

        save_result = await self.save_uploaded_package(filename=filename, content=content)
        saved_path = str(save_result["path"])
        install_result = await self.install(package=saved_path, on_conflict=on_conflict)

        package_sha256 = hashlib.sha256(content).hexdigest().lower()
        warning = await self._record_install_source_best_effort(
            install_result=install_result,
            package_filename=filename,
            package_sha256=package_sha256,
            override=install_source_override,
        )

        payload: dict[str, object] = {
            "upload": save_result,
            "install": install_result,
        }
        if warning is not None:
            payload["install_source_warning"] = warning
        return payload

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

    def _list_local_plugins_sync(self) -> dict[str, object]:
        try:
            plugins = sorted(
                path.parent.name
                for path in _RUNTIME_PLUGINS_ROOT.glob("*/plugin.toml")
                if path.is_file()
            )
            return {"plugins": plugins, "count": len(plugins)}
        except Exception as exc:
            raise self._domain_error_from_exception(exc, action="list_plugins") from exc

    def _list_local_packages_sync(self) -> dict[str, object]:
        try:
            items: list[dict[str, object]] = []
            package_paths = [
                path
                for suffix in _ALLOWED_UPLOAD_SUFFIXES
                for path in _TARGET_ROOT.glob(f"*{suffix}")
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
            return {"packages": items, "count": len(items), "target_dir": str(_TARGET_ROOT)}
        except Exception as exc:
            raise self._domain_error_from_exception(exc, action="list_packages") from exc

    def _build_sync(
        self,
        *,
        mode: str,
        plugin: str | None,
        plugins: list[str] | None,
        out: str | None,
        target_dir: str | None,
        keep_staging: bool,
        bundle_id: str | None,
        package_name: str | None,
        package_description: str | None,
        version: str | None,
    ) -> dict[str, object]:
        try:
            plugin_dirs = self._resolve_plugin_dirs(mode=mode, plugin=plugin, plugins=plugins or [])
            resolved_target_dir = Path(target_dir).expanduser().resolve() if target_dir else _TARGET_ROOT
            _require_within(resolved_target_dir, _TARGET_ROOT, field="target_dir")
            resolved_target_dir.mkdir(parents=True, exist_ok=True)

            if out and mode != "bundle" and len(plugin_dirs) != 1:
                raise ValueError("'out' can only be used when building a single plugin")

            if mode == "bundle":
                resolved_bundle_id = bundle_id or "__".join(sorted(item.name for item in plugin_dirs))
                output_path = (
                    _require_within(Path(out).expanduser().resolve(), _TARGET_ROOT, field="out")
                    if out
                    else _require_within(
                        (resolved_target_dir / f"{resolved_bundle_id}.neko-bundle").resolve(),
                        _TARGET_ROOT,
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
            for plugin_dir in plugin_dirs:
                output_path = (
                    _require_within(Path(out).expanduser().resolve(), _TARGET_ROOT, field="out")
                    if out
                    else resolved_target_dir / f"{plugin_dir.name}.neko-plugin"
                )
                try:
                    result = build_plugin(
                        plugin_dir,
                        output_path,
                        keep_staging=keep_staging,
                    )
                    built.append(result.model_dump(mode="json"))
                except Exception as exc:
                    failed.append({"plugin": plugin_dir.name, "error": str(exc)})

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

    def _install_sync(
        self,
        *,
        package: str,
        plugins_root: str | None,
        profiles_root: str | None,
        on_conflict: str,
    ) -> dict[str, object]:
        try:
            plugins_root_path = (
                _require_within(Path(plugins_root).expanduser().resolve(), _INSTALL_PLUGINS_ROOT, field="plugins_root")
                if plugins_root
                else _INSTALL_PLUGINS_ROOT
            )
            profiles_root_path = (
                _require_within(Path(profiles_root).expanduser().resolve(), _INSTALL_PROFILES_ROOT, field="profiles_root")
                if profiles_root
                else _INSTALL_PROFILES_ROOT
            )
            result = install_package(
                self._resolve_package_path(package),
                plugins_root=plugins_root_path,
                profiles_root=profiles_root_path,
                on_conflict=on_conflict,
            )
            return result.model_dump(mode="json")
        except Exception as exc:
            raise self._domain_error_from_exception(exc, action="install") from exc

    def _analyze_sync(
        self,
        *,
        plugins: list[str],
        current_sdk_version: str | None,
    ) -> dict[str, object]:
        try:
            plugin_dirs = [self._resolve_plugin_dir_candidate(item) for item in plugins]
            result = analyze_bundle_plugins(
                plugin_dirs,
                current_sdk_version=current_sdk_version,
            )
            return result.model_dump(mode="json")
        except Exception as exc:
            raise self._domain_error_from_exception(exc, action="analyze") from exc

    def _save_uploaded_package_sync(self, *, filename: str, content: bytes) -> dict[str, object]:
        try:
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
            _TARGET_ROOT.mkdir(parents=True, exist_ok=True)

            stem = safe_name
            suffix = ""
            for allowed_suffix in sorted(_ALLOWED_UPLOAD_SUFFIXES, key=len, reverse=True):
                if stem.endswith(allowed_suffix):
                    suffix = allowed_suffix
                    stem = stem[: -len(allowed_suffix)]
                    break

            # Exclusive create: if name collides (including concurrent uploads
            # racing on the same filename), pick a UUID-suffixed dest and retry.
            dest = _TARGET_ROOT / safe_name
            while True:
                try:
                    with dest.open("xb") as file:
                        file.write(content)
                    break
                except FileExistsError:
                    unique = uuid.uuid4().hex[:8]
                    dest = _TARGET_ROOT / f"{stem}_{unique}{suffix}"
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

    def _resolve_plugin_dirs(self, *, mode: str, plugin: str | None, plugins: list[str]) -> list[Path]:
        if mode == "all":
            plugin_dirs = sorted(
                path.parent.resolve()
                for path in _RUNTIME_PLUGINS_ROOT.glob("*/plugin.toml")
                if path.is_file()
            )
            if not plugin_dirs:
                raise FileNotFoundError(f"No plugin.toml files found under {_RUNTIME_PLUGINS_ROOT}")
            return plugin_dirs

        if mode == "single":
            if not plugin:
                raise ValueError("Please provide a plugin when mode=single")
            return [self._resolve_plugin_dir_candidate(plugin)]

        if mode in {"selected", "bundle"}:
            if not plugins:
                raise ValueError(f"Please provide plugins when mode={mode}")
            return [self._resolve_plugin_dir_candidate(item) for item in plugins]

        raise ValueError("Unsupported build mode")

    def _resolve_plugin_dir_candidate(self, raw: str) -> Path:
        candidate = Path(raw).expanduser()
        plugin_dir = candidate.resolve() if candidate.exists() else (_RUNTIME_PLUGINS_ROOT / raw).resolve()
        _require_within(plugin_dir, _RUNTIME_PLUGINS_ROOT, field=f"plugin '{raw}'")
        plugin_toml = plugin_dir / "plugin.toml"
        if not plugin_toml.is_file():
            raise FileNotFoundError(f"plugin.toml not found for plugin '{raw}': {plugin_toml}")
        return plugin_dir

    def _resolve_package_path(self, raw: str) -> Path:
        def _accept(path: Path) -> bool:
            return path.is_file() and any(
                path.name.endswith(suffix) for suffix in _ALLOWED_UPLOAD_SUFFIXES
            )

        candidate = Path(raw).expanduser()
        if candidate.exists():
            resolved = candidate.resolve()
            _require_within(resolved, _TARGET_ROOT, field=f"package '{raw}'")
            if _accept(resolved):
                return resolved

        target_candidate = (_TARGET_ROOT / raw).resolve()
        if target_candidate.exists():
            _require_within(target_candidate, _TARGET_ROOT, field=f"package '{raw}'")
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
                pass
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
