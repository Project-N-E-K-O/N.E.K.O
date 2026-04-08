from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys

from plugin.logging_config import get_logger
from plugin.server.domain.errors import ServerDomainError

_PLUGIN_ROOT = Path(__file__).resolve().parents[3]
_CLI_ROOT = _PLUGIN_ROOT / "neko-plugin-cli"
_RUNTIME_PLUGINS_ROOT = _PLUGIN_ROOT / "plugins"
_RUNTIME_PROFILES_ROOT = _PLUGIN_ROOT / ".neko-package-profiles"
_TARGET_ROOT = _CLI_ROOT / "target"

if str(_CLI_ROOT) not in sys.path:
    sys.path.insert(0, str(_CLI_ROOT))

from public import analyze_bundle_plugins, inspect_package, pack_bundle, pack_plugin, unpack_package

logger = get_logger("server.application.plugin_cli")


class PluginCliService:
    async def list_local_plugins(self) -> dict[str, object]:
        return await asyncio.to_thread(self._list_local_plugins_sync)

    async def list_local_packages(self) -> dict[str, object]:
        return await asyncio.to_thread(self._list_local_packages_sync)

    async def pack(
        self,
        *,
        plugin: str | None = None,
        plugins: list[str] | None = None,
        pack_all: bool = False,
        out: str | None = None,
        target_dir: str | None = None,
        keep_staging: bool = False,
        bundle: bool = False,
        bundle_id: str | None = None,
        package_name: str | None = None,
        package_description: str | None = None,
        version: str | None = None,
    ) -> dict[str, object]:
        return await asyncio.to_thread(
            self._pack_sync,
            plugin=plugin,
            plugins=plugins,
            pack_all=pack_all,
            out=out,
            target_dir=target_dir,
            keep_staging=keep_staging,
            bundle=bundle,
            bundle_id=bundle_id,
            package_name=package_name,
            package_description=package_description,
            version=version,
        )

    async def inspect(self, *, package: str) -> dict[str, object]:
        return await asyncio.to_thread(self._inspect_sync, package=package)

    async def verify(self, *, package: str) -> dict[str, object]:
        return await asyncio.to_thread(self._verify_sync, package=package)

    async def unpack(
        self,
        *,
        package: str,
        plugins_root: str | None = None,
        profiles_root: str | None = None,
        on_conflict: str = "rename",
    ) -> dict[str, object]:
        return await asyncio.to_thread(
            self._unpack_sync,
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
            for path in sorted(
                _TARGET_ROOT.glob("*.neko-*"),
                key=lambda item: item.stat().st_mtime if item.exists() else 0,
                reverse=True,
            ):
                if not path.is_file():
                    continue
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

    def _pack_sync(
        self,
        *,
        plugin: str | None,
        plugins: list[str] | None,
        pack_all: bool,
        out: str | None,
        target_dir: str | None,
        keep_staging: bool,
        bundle: bool,
        bundle_id: str | None,
        package_name: str | None,
        package_description: str | None,
        version: str | None,
    ) -> dict[str, object]:
        try:
            plugin_dirs = self._resolve_plugin_dirs(plugin=plugin, plugins=plugins or [], pack_all=pack_all)
            resolved_target_dir = Path(target_dir).expanduser().resolve() if target_dir else _TARGET_ROOT
            resolved_target_dir.mkdir(parents=True, exist_ok=True)

            if out and not bundle and len(plugin_dirs) != 1:
                raise ValueError("'out' can only be used when packing a single plugin")

            if bundle or len(plugin_dirs) > 1:
                resolved_bundle_id = bundle_id or "__".join(sorted(item.name for item in plugin_dirs))
                output_path = (
                    Path(out).expanduser().resolve()
                    if out
                    else resolved_target_dir / f"{resolved_bundle_id}.neko-bundle"
                )
                result = pack_bundle(
                    plugin_dirs,
                    output_path,
                    bundle_id=resolved_bundle_id,
                    package_name=package_name,
                    package_description=package_description,
                    version=version or "0.1.0",
                    keep_staging=keep_staging,
                )
                packed = [result.model_dump(mode="json")]
                return {
                    "packed": packed,
                    "packed_count": len(packed),
                    "failed": [],
                    "failed_count": 0,
                    "ok": True,
                }

            packed: list[dict[str, object]] = []
            failed: list[dict[str, object]] = []
            for plugin_dir in plugin_dirs:
                output_path = (
                    Path(out).expanduser().resolve()
                    if out
                    else resolved_target_dir / f"{plugin_dir.name}.neko-plugin"
                )
                try:
                    result = pack_plugin(
                        plugin_dir,
                        output_path,
                        keep_staging=keep_staging,
                    )
                    packed.append(result.model_dump(mode="json"))
                except Exception as exc:
                    failed.append({"plugin": plugin_dir.name, "error": str(exc)})

            return {
                "packed": packed,
                "packed_count": len(packed),
                "failed": failed,
                "failed_count": len(failed),
                "ok": not failed,
            }
        except Exception as exc:
            raise self._domain_error_from_exception(exc, action="pack") from exc

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

    def _unpack_sync(
        self,
        *,
        package: str,
        plugins_root: str | None,
        profiles_root: str | None,
        on_conflict: str,
    ) -> dict[str, object]:
        try:
            result = unpack_package(
                self._resolve_package_path(package),
                plugins_root=plugins_root or _RUNTIME_PLUGINS_ROOT,
                profiles_root=profiles_root or _RUNTIME_PROFILES_ROOT,
                on_conflict=on_conflict,
            )
            return result.model_dump(mode="json")
        except Exception as exc:
            raise self._domain_error_from_exception(exc, action="unpack") from exc

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

    def _resolve_plugin_dirs(self, *, plugin: str | None, plugins: list[str], pack_all: bool) -> list[Path]:
        if pack_all:
            plugin_dirs = sorted(
                path.parent.resolve()
                for path in _RUNTIME_PLUGINS_ROOT.glob("*/plugin.toml")
                if path.is_file()
            )
            if not plugin_dirs:
                raise FileNotFoundError(f"No plugin.toml files found under {_RUNTIME_PLUGINS_ROOT}")
            return plugin_dirs

        if plugin:
            return [self._resolve_plugin_dir_candidate(plugin)]
        if plugins:
            return [self._resolve_plugin_dir_candidate(item) for item in plugins]
        raise ValueError("Please provide one or more plugins or set pack_all=true")

    def _resolve_plugin_dir_candidate(self, raw: str) -> Path:
        candidate = Path(raw).expanduser()
        plugin_dir = candidate.resolve() if candidate.exists() else (_RUNTIME_PLUGINS_ROOT / raw).resolve()
        plugin_toml = plugin_dir / "plugin.toml"
        if not plugin_toml.is_file():
            raise FileNotFoundError(f"plugin.toml not found for plugin '{raw}': {plugin_toml}")
        return plugin_dir

    def _resolve_package_path(self, raw: str) -> Path:
        candidate = Path(raw).expanduser()
        if candidate.exists():
            return candidate.resolve()

        target_candidate = (_TARGET_ROOT / raw).resolve()
        if target_candidate.exists():
            return target_candidate

        raise FileNotFoundError(f"package file not found: {raw}")

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
