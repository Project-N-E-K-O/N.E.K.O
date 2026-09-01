from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from plugin.core.dependency import _topological_sort_plugins
from plugin.core.entry_points import describe_plugin_entry_directory_mismatch
from plugin.core.registry import (
    PluginContext,
    _build_plugin_meta,
    _check_plugin_dependency,
    _extract_entries_preview,
    _extract_plugin_ui_config,
    _find_missing_python_requirements,
    _parse_single_plugin_config,
    _prepare_plugin_import_roots,
    _resolve_plugin_id_conflict,
    register_plugin,
)
from plugin.server.application.plugins.metadata_scanner import (
    _DEFAULT_SCAN_TIMEOUT_SECONDS as _DEFAULT_ITEM_SCAN_TIMEOUT,
    clear_plugin_metadata_scan_cache,
    MAX_CONCURRENT_METADATA_SCANS,
    PluginMetadataScanError,
    scan_cache_clear_count,
    scan_plugin_metadata_isolated,
)
from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.domain.errors import ServerDomainError
from plugin.settings import BUILTIN_PLUGIN_CONFIG_ROOT, PLUGIN_CONFIG_ROOTS

logger = get_logger("server.application.plugins.registry")
_PLUGIN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

_MANAGED_META_KEYS = {
    "id",
    "name",
    "type",
    "plugin_type",
    "description",
    "short_description",
    "keywords",
    "passive",
    "version",
    "sdk_version",
    "sdk_recommended",
    "sdk_supported",
    "sdk_untested",
    "sdk_conflicts",
    "input_schema",
    "author",
    "dependencies",
    "i18n",
    "plugin_ui",
    "config_path",
    "entry_point",
    "runtime_enabled",
    "runtime_auto_start",
    "runtime_load_state",
    "runtime_load_error_type",
    "runtime_load_error_message",
    "runtime_load_error_phase",
    "entries_preview",
    "adapter_mode",
    "runtime_source_missing",
    "source",
    "effective_source",
    "builtin_version",
    "shadowed_builtin_path",
}


@dataclass(slots=True)
class PluginDiscoveryRecord:
    plugin_id: str
    original_plugin_id: str
    config_path: Path
    entry_point: str
    plugin_type: str
    enabled: bool
    auto_start: bool
    meta_payload: dict[str, object]


@dataclass(slots=True)
class PluginDiscoveryFailure:
    plugin_id: str | None
    config_path: Path
    error: str


@dataclass(slots=True)
class PluginDiscoverySnapshot:
    records: list[PluginDiscoveryRecord]
    failures: list[PluginDiscoveryFailure]
    config_paths: set[Path]
    shadowed: list[PluginDiscoveryRecord]


def _get_registered_plugin_snapshot_sync() -> dict[str, dict[str, object]]:
    with state.acquire_plugins_read_lock():
        snapshot: dict[str, dict[str, object]] = {}
        for plugin_id, meta in state.plugins.items():
            if isinstance(plugin_id, str) and isinstance(meta, dict):
                snapshot[plugin_id] = dict(meta)
        return snapshot


def _list_running_plugin_ids_sync() -> set[str]:
    running: set[str] = set()
    with state.acquire_plugin_hosts_read_lock():
        for plugin_id, host_obj in state.plugin_hosts.items():
            if not isinstance(plugin_id, str):
                continue
            try:
                if hasattr(host_obj, "is_alive") and host_obj.is_alive():
                    running.add(plugin_id)
            except Exception:
                continue
    return running


def _remap_entries_preview_plugin_id(
    entries_preview: list[dict[str, object]],
    *,
    plugin_id: str,
) -> list[dict[str, object]]:
    remapped: list[dict[str, object]] = []
    for item in entries_preview:
        entry_copy = dict(item)
        entry_id_obj = entry_copy.get("id")
        if isinstance(entry_id_obj, str) and entry_id_obj:
            entry_copy["event_key"] = f"{plugin_id}.{entry_id_obj}"
        remapped.append(entry_copy)
    return remapped


def _select_managed_fields(meta: dict[str, object]) -> dict[str, object]:
    return {
        key: meta[key]
        for key in _MANAGED_META_KEYS
        if key in meta
    }


def _find_plugin_config_path(plugin_id: str, roots: tuple[Path, ...]) -> Path | None:
    normalized_plugin_id = plugin_id.strip()
    if not _PLUGIN_ID_PATTERN.fullmatch(normalized_plugin_id):
        return None

    # Roots are declared in effective-source priority order (user, builtin).
    for root in roots:
        resolved_root = root.resolve()
        config_file = (resolved_root / normalized_plugin_id / "plugin.toml").resolve()
        if resolved_root not in config_file.parents:
            continue
        if config_file.exists():
            return config_file
    return None


def _source_for_config_path(config_path: Path) -> str:
    builtin_root = _resolve_config_path(BUILTIN_PLUGIN_CONFIG_ROOT)
    return "builtin" if config_path.parent.parent == builtin_root else "user"


def _select_effective_records(
    records: list[PluginDiscoveryRecord],
    roots: tuple[Path, ...],
) -> tuple[list[PluginDiscoveryRecord], list[PluginDiscoveryRecord]]:
    """Apply the sole supported same-ID source precedence rule.

    Only canonical ``<root>/<id>/plugin.toml`` installations across distinct
    roots form a builtin/user override. Other duplicate declarations remain
    real conflicts and continue through the legacy ``_1`` resolution path.
    """
    grouped: dict[str, list[PluginDiscoveryRecord]] = {}
    order: list[str] = []
    for record in records:
        if record.plugin_id not in grouped:
            grouped[record.plugin_id] = []
            order.append(record.plugin_id)
        grouped[record.plugin_id].append(record)

    selected: list[PluginDiscoveryRecord] = []
    shadowed: list[PluginDiscoveryRecord] = []
    for plugin_id in order:
        group = grouped[plugin_id]
        canonical = [record for record in group if record.config_path.parent.name == plugin_id]
        sources = {_source_for_config_path(record.config_path) for record in canonical}
        if not {"builtin", "user"}.issubset(sources):
            # This is a real legacy ID conflict, not a supported source
            # override. Preserve the historical builtin-first winner even
            # though discovery roots are now ordered user-first.
            winners = sorted(
                group,
                key=lambda record: _source_for_config_path(record.config_path) != "builtin",
            )
            hidden: list[PluginDiscoveryRecord] = []
        else:
            winners = sorted(
                (
                    record
                    for record in group
                    if record not in canonical
                    or _source_for_config_path(record.config_path) == "user"
                ),
                key=lambda record: record not in canonical,
            )
            hidden = [record for record in canonical if record not in winners]

        builtin_hidden = next(
            (record for record in hidden if _source_for_config_path(record.config_path) == "builtin"),
            None,
        )
        for record in winners:
            source = _source_for_config_path(record.config_path)
            record.meta_payload["source"] = source
            record.meta_payload["effective_source"] = source
            if source == "builtin":
                record.meta_payload["builtin_version"] = str(record.meta_payload.get("version", ""))
            elif builtin_hidden is not None and record in canonical:
                record.meta_payload["builtin_version"] = str(
                    builtin_hidden.meta_payload.get("version", "")
                )
                record.meta_payload["shadowed_builtin_path"] = str(builtin_hidden.config_path)
        selected.extend(winners)
        shadowed.extend(hidden)
    return selected, shadowed


def _resolve_meta_config_path(meta: dict[str, object] | None) -> Path | None:
    if not isinstance(meta, dict):
        return None

    config_path_obj = meta.get("config_path")
    if not isinstance(config_path_obj, str) or not config_path_obj:
        return None

    try:
        return Path(config_path_obj).resolve()
    except Exception:
        return Path(config_path_obj)


def _resolve_config_path(path: Path) -> Path:
    try:
        return path.resolve()
    except Exception:
        return path


def _config_path_belongs_to_roots(config_path: Path, roots: tuple[Path, ...]) -> bool:
    resolved_path = _resolve_config_path(config_path)
    return any(
        _resolve_config_path(root) in resolved_path.parents
        for root in roots
    )


def _find_existing_runtime_plugin_id_by_config_path(
    config_path: Path,
    existing_snapshot: dict[str, dict[str, object]],
) -> str | None:
    resolved_config_path = _resolve_config_path(config_path)
    for plugin_id, meta in existing_snapshot.items():
        meta_config_path = _resolve_meta_config_path(meta)
        if meta_config_path is not None and meta_config_path == resolved_config_path:
            return plugin_id
    return None


def _collect_plugin_contexts_from_roots_sync(
    roots: tuple[Path, ...],
) -> tuple[list[PluginContext], dict[str, PluginContext]]:
    # Dependency ordering must use the same effective source as registration.
    candidates: dict[str, list[tuple[PluginContext, str, bool]]] = {}
    pid_to_context: dict[str, PluginContext] = {}
    context_order: list[str] = []
    processed_paths: set[Path] = set()

    for root in roots:
        try:
            resolved_root = root.resolve()
        except Exception:
            resolved_root = root

        if not resolved_root.exists():
            continue

        for config_path in sorted(resolved_root.glob("*/plugin.toml")):
            if config_path.parent.name.startswith("."):
                continue
            try:
                ctx = _parse_single_plugin_config(config_path, processed_paths, logger)
            except Exception as exc:
                logger.debug(
                    "plugin context collection skipped failed config {}: err_type={}, err={}",
                    config_path,
                    type(exc).__name__,
                    str(exc),
                )
                continue

            if ctx is None:
                continue
            if ctx.pid not in candidates:
                context_order.append(ctx.pid)
            candidates.setdefault(ctx.pid, []).append(
                (
                    ctx,
                    _source_for_config_path(config_path),
                    config_path.parent.name == ctx.pid,
                )
            )

    for plugin_id in context_order:
        group = candidates[plugin_id]
        canonical_user = next(
            (ctx for ctx, source, canonical in group if canonical and source == "user"),
            None,
        )
        canonical_builtin = next(
            (ctx for ctx, source, canonical in group if canonical and source == "builtin"),
            None,
        )
        if canonical_user is not None and canonical_builtin is not None:
            winner = canonical_user
        else:
            winner = next(
                (ctx for ctx, source, _canonical in group if source == "builtin"),
                group[0][0],
            )
        pid_to_context[plugin_id] = winner
        for ctx, _source, _canonical in group:
            if ctx is winner:
                continue
            logger.debug(
                "duplicate plugin id '{}' ignored while building runtime plan",
                plugin_id,
            )

    plugin_contexts = [pid_to_context[plugin_id] for plugin_id in context_order]
    return plugin_contexts, pid_to_context


def _build_ordered_plugin_ids_sync(candidate_plugin_ids: set[str] | None = None) -> list[str]:
    roots = tuple(PLUGIN_CONFIG_ROOTS)
    plugin_contexts, pid_to_context = _collect_plugin_contexts_from_roots_sync(roots)
    registered_snapshot = _get_registered_plugin_snapshot_sync()
    if not registered_snapshot:
        return []

    target_ids = set(candidate_plugin_ids) if candidate_plugin_ids is not None else set(registered_snapshot.keys())
    if not target_ids:
        return []

    config_path_to_plugin_id: dict[Path, str] = {}
    for plugin_id, meta in registered_snapshot.items():
        resolved_config_path = _resolve_meta_config_path(meta)
        if resolved_config_path is not None:
            config_path_to_plugin_id[resolved_config_path] = plugin_id

    ordered: list[str] = []
    seen: set[str] = set()
    if plugin_contexts:
        for declared_plugin_id in _topological_sort_plugins(plugin_contexts, pid_to_context, logger):
            ctx = pid_to_context.get(declared_plugin_id)
            if ctx is None:
                continue

            try:
                ctx_config_path = ctx.toml_path.resolve()
            except Exception:
                ctx_config_path = ctx.toml_path
            runtime_plugin_id = config_path_to_plugin_id.get(ctx_config_path, declared_plugin_id)
            if runtime_plugin_id not in target_ids or runtime_plugin_id in seen:
                continue
            if runtime_plugin_id not in registered_snapshot:
                continue
            ordered.append(runtime_plugin_id)
            seen.add(runtime_plugin_id)

    for plugin_id in sorted(target_ids):
        if plugin_id in seen or plugin_id not in registered_snapshot:
            continue
        ordered.append(plugin_id)
        seen.add(plugin_id)

    return ordered


# 元数据扫描的并发上限。
#
# 每个插件的元数据扫描是一次性子进程（读元数据 = 执行插件的模块级代码，放在
# 本进程里 import 会让一个写坏的插件拖死宿主）。Windows 上没有 fork，每次都是
# 完整的 CreateProcess + 全新解释器，实测单次约 0.84 s，其中约 0.76 s 是解释器
# 启动和导入扫描框架本身——也就是说成本几乎与插件无关，纯粹是"起进程"的价钱。
#
# 串行时这笔钱按插件数线性累加：本机 16 个插件约 13.5 s，而插件管理器前端的
# 超时是 30 s。并行实测接近线性（16 个插件：w=2 → 6.9 s，w=4 → 3.9 s，
# w=8 → 2.6 s），子进程读写管道全程释放 GIL，所以线程池就够，不需要多进程。
#
# 上限取 CPU 的四分之一并夹在 [2, 8]：w=16 相比 w=8 只再省半秒，却把并发解释器
# 数翻倍（单个约 66 MB 常驻），不划算；而 4 核小机器上也不该一次拉起 8 个。
# 一整轮 discovery 允许花在元数据扫描上的墙钟总时间。
#
# 单项上限封不住总量：17 个插件按 5 并发是 4 波，4 × 10s 仍然超前端的 30s。总预算
# 才是真正的天花板——用完之后剩下的插件不再起进程，直接按"扫描失败"记录，插件
# 照样出现在列表里，只是没有元数据。下次 refresh 会重试，不是持久禁用。
#
# 20s 的取法：给前端 30s 留出 10s 做其余的事。健康路径根本碰不到——实测全量并行
# 扫描 3.3s，是预算的六分之一。
# Env: NEKO_PLUGIN_DISCOVERY_SCAN_BUDGET
from plugin.server.application.plugins._env_budgets import env_int, env_seconds

_DISCOVERY_SCAN_BUDGET_SECONDS = env_seconds("NEKO_PLUGIN_DISCOVERY_SCAN_BUDGET", 20.0)

# 这一轮的上限，不是整个服务器的上限——池是每轮各建各的。真正封顶并发解释器
# 数量的是 metadata_scanner 里的全局闸，所以这里的天花板取它，两处不会各说各话。
_DISCOVERY_SCAN_MAX_WORKERS = MAX_CONCURRENT_METADATA_SCANS

# "这一刻没扫成"和"这个插件坏了"要分开：只有前者不该让插件掉进
# runtime_load_state="failed"，因为那个状态会把它从自启动名单里除名。
#
# ScanBudgetExhausted 永远属于前者：它的意思是"整轮的时间在轮到你之前就用完了"，
# 跟这个插件本身无关。
#
# TimeoutExpired 两种都可能，得看它当时拿到了多少时间：
#   * 拿到的是被剩余预算压缩过的一小段 —— 还是预算问题；
#   * 拿满了整个单项上限还没扫完 —— 那就是这个插件自己的导入卡住了，必须留在
#     failed。放它进自启动名单只会让服务器启动时再卡一次它的启动超时，正好把这
#     道资格闸自己废掉（codex）。
_ALWAYS_TRANSIENT_SCAN_ERROR_TYPES = frozenset({"ScanBudgetExhausted"})
_BUDGET_SENSITIVE_SCAN_ERROR_TYPES = frozenset({"TimeoutExpired"})


def _scan_failure_is_transient(error_type: str | None, scan_timeout: float | None) -> bool:
    """Whether this scan failure describes the moment rather than the plugin."""
    if not error_type:
        return False
    if error_type in _ALWAYS_TRANSIENT_SCAN_ERROR_TYPES:
        return True
    if error_type not in _BUDGET_SENSITIVE_SCAN_ERROR_TYPES:
        return False
    # 只有"没拿满单项上限"才算被预算挤的。拿满了还超时 = 插件自己卡住。
    return scan_timeout is not None and scan_timeout < _DEFAULT_ITEM_SCAN_TIMEOUT

# 全量刷新的发布序号。
#
# 刷新之间没有串行化，而发布是"把 discovery 结果逐条写进 state.plugins"。两次刷新
# 重叠时，先开始的那次完全可能后落地，于是把更新的那份注册表内容盖回旧的——一次
# 成功的升级/换源就这样被一个更早的请求悄悄撤销了（codex）。
#
# 这个竞态本来就在（刷新从来没有串行化过），但本 PR 把它放大了：命中缓存的刷新
# 只要 0.14s，而冷扫描要 3.3s，"后发先至"从此是常态而不是巧合。
#
# 做法和扫描缓存那两道闸同构：开工前领号，发布前认号，比已发布的号旧就整个放弃
# 发布。被放弃的那次不会丢信息——顶掉它的那次是后开始的，看到的盘面只会更新。
_REGISTRY_PUBLISH_GUARD = threading.Lock()
_REGISTRY_REFRESH_TICKET = 0
_REGISTRY_PUBLISHED_TICKET = 0
# 每个插件各自最后一次被发布时的号。
#
# 全量刷新和单插件刷新都会往 state.plugins 里写，但它们不能共用那个全局号：单插件
# 刷新是 start_plugin(refresh_registry=True) 的必经之路，也就是每次启动插件都会发生
# 一次；让它去推全局号，等于随便启动一个插件就能把一次正在跑的全量刷新整个作废掉。
#
# 所以顺序按**每个插件**判：全量刷新逐条比，单插件刷新只比自己那一条。谁的号新谁
# 说了算，互不牵连（codex）。
# 值是 (最后发布的号, 最后一次 force 发布的号)。存号不存布尔量，理由同
# _REGISTRY_PUBLISHED_FORCED_TICKET：布尔量会被嫁接到别人的号上。
_REGISTRY_PUBLISHED_PLUGIN_TICKET: dict[str, tuple[int, int]] = {}


def _publication_keys(plugin_id: str | None, config_path: Path) -> tuple[str, ...]:
    """The identities a publication of this record has to be ordered against.

    路径**和**插件 id 都算，因为两者都会变而且不同步。只按路径排的话，一个插件从
    内置源换到用户覆盖（路径变了）时，后一次单插件刷新把新号记在新路径上，而一次
    更早的全量刷新查的是旧路径、发现没人认领，就把自己那份陈旧记录发布上去，把插件
    指回一个已经被取代的源（codex）。只按 id 排则漏掉 id 被冲突改名的情况。两个键
    都认：任何一个上被更新的号占了，就让位。
    """
    keys = [f"path:{_resolve_config_path(config_path)}"]
    if plugin_id:
        keys.append(f"id:{plugin_id}")
    return tuple(keys)


# 一次没扫成的刷新要原样带过去的字段。
#
# entries_preview：不扫等于没学到新东西，抹掉它会让插件在 /plugins 里少半张脸。
# runtime_load_state / runtime_load_error_*：上一次**扫成功了**并且判定这个插件坏掉
#   的结论，同样不该被一次根本没跑的扫描清掉——清掉它，插件就在没有任何一次成功
#   重扫的情况下重新获得自启动资格，开机时再卡一次（codex）。
_DEFERRED_SCAN_CARRY_FORWARD = (
    "entries_preview",
    "runtime_load_state",
    "runtime_load_error_type",
    "runtime_load_error_message",
    "runtime_load_error_phase",
)


def _keep_known_entries_on_deferred_scan(
    record: PluginDiscoveryRecord,
    previous: object,
) -> PluginDiscoveryRecord:
    """Carry the last completed scan's verdict through a scan that never ran."""
    payload = record.meta_payload
    if not payload.get("runtime_scan_deferred"):
        return record
    if not isinstance(previous, dict):
        return record
    carried = {
        field: previous[field]
        for field in _DEFERRED_SCAN_CARRY_FORWARD
        if previous.get(field)
    }
    if not carried:
        return record
    return replace(record, meta_payload={**payload, **carried})


# 一次 force 发布之后，号 <= 这个值的**普通**刷新一律作废。
#
# 号只表示"谁先开始"，不表示"谁看到的盘面更新"。加了缓存之后这两件事会分家：一次
# force 刷新冷扫要 3.3s，期间一次普通刷新可能 0.14s 就命中缓存发布完，号还更大。
# 而 force 存在的全部理由就是缓存看不见插件目录**之外**的变化（共享 vendor、
# site-packages）——让那份缓存结果把 force 的结果顶掉，正好是反的（CodeRabbit）。
_REGISTRY_CACHE_BLIND_UNTIL = 0
# 最后一次 force 发布用的号——存号，不存「最后一次发布是不是 force」。
#
# 原来这里是个布尔量，钉在 _REGISTRY_PUBLISHED_TICKET 上。但那个号可以属于另一次
# 普通刷新：一次更旧的 force 后落地时不会推进号（它更小），却会把布尔量翻成 True，
# 于是「最后一次是 force」被嫁接到了普通刷新的号上。之后一次**更新**的 force 拿自己
# 的号去比那个号，反而被挡掉——更新的读盘结果让位给更旧的，依据还是第三方的号
# （本轮对抗复审）。存号，两件事就不会再脱钩。
_REGISTRY_PUBLISHED_FORCED_TICKET = 0
# 按插件的作废屏障：号 <= 这个值的**普通**发布，对这个插件而言可能读的是已经被
# force 作废掉的缓存。
#
# 单插件的 force 刷新不能去推全局屏障——那等于点一下某个插件的刷新就把一次正在跑
# 的全量刷新整个作废。但它确实需要**自己这一条**的屏障：否则一次普通全量刷新只要
# 号更大，就能在它发布之后把这个插件的旧条目和工具 schema 又贴回去（codex）。
_REGISTRY_PLUGIN_CACHE_BLIND_UNTIL: dict[str, int] = {}


def _may_remove_plugin(ticket: int, plugin_id: str) -> bool:
    """Whether this refresh may still delete that plugin's registry entry.

    Caller holds ``_REGISTRY_PUBLISH_GUARD``.

    删除走的是另一条判据，而且以前完全没有排序：一次更早的全量刷新扫的时候插件还在
    旧路径上，之后一次单插件刷新把它从替换后的新路径发布了出来——旧刷新的记录里根本
    没有这个插件，所以逐条那道检查压根不会跑到它，它就被当成"消失了"删掉，或者被标成
    source_missing（codex）。按插件的号在这里也要认。
    """
    published, _ = _REGISTRY_PUBLISHED_PLUGIN_TICKET.get(f"id:{plugin_id}", (0, False))
    return ticket >= published


def _may_publish_record(
    ticket: int, config_path: Path, *, forced: bool, plugin_id: str | None = None
) -> bool:
    """Whether this refresh still owns the latest word on that one plugin.

    Caller holds ``_REGISTRY_PUBLISH_GUARD``.
    """
    keys = _publication_keys(plugin_id, config_path)
    if not forced and ticket <= max(
        [_REGISTRY_CACHE_BLIND_UNTIL]
        + [_REGISTRY_PLUGIN_CACHE_BLIND_UNTIL.get(key, 0) for key in keys]
    ):
        # 屏障对两条发布路径一视同仁。只让全量刷新那道门认它的话，一次在 force 扫描
        # 期间开始的**单插件**刷新照样能把可能来自旧缓存的结果写进去——而单插件刷新
        # 是 start_plugin 的必经之路，它比全量刷新常见得多（CodeRabbit）。
        return False
    published = 0
    published_forced = 0
    for key in keys:
        seen_ticket, seen_forced_ticket = _REGISTRY_PUBLISHED_PLUGIN_TICKET.get(
            key, (0, 0)
        )
        published = max(published, seen_ticket)
        published_forced = max(published_forced, seen_forced_ticket)
    # 是 force 就只跟别的 force 比号；是普通刷新就跟所有已发布的比。
    if ticket < (published_forced if forced else published):
        return False
    _record_publication(ticket, keys, forced=forced)
    return True


def _record_publication(ticket: int, keys, *, forced: bool) -> None:
    """Stamp these identities as published by ``ticket``. Caller holds the guard."""
    for key in keys:
        seen_ticket, seen_forced_ticket = _REGISTRY_PUBLISHED_PLUGIN_TICKET.get(
            key, (0, 0)
        )
        _REGISTRY_PUBLISHED_PLUGIN_TICKET[key] = (
            max(seen_ticket, ticket),
            max(seen_forced_ticket, ticket) if forced else seen_forced_ticket,
        )
        if forced:
            # 此刻还在途的普通刷新，对这个插件而言可能读的是我们刚作废掉的缓存。
            _REGISTRY_PLUGIN_CACHE_BLIND_UNTIL[key] = _REGISTRY_REFRESH_TICKET


def _claim_resolved_runtime_id(ticket: int, resolved_id: str, *, forced: bool) -> None:
    """Also claim the id the record was actually registered under.

    ``_resolve_plugin_id_conflict(enable_rename=True)`` can register a record
    under a different runtime id than ``record.plugin_id``. Removal ordering
    looks plugins up by the id in ``state.plugins`` — the resolved one — so
    without this the rename leaves the claim on a key nobody consults, and an
    older refresh is free to delete the plugin that was just published
    (CodeRabbit).
    """
    if resolved_id:
        _record_publication(ticket, (f"id:{resolved_id}",), forced=forced)


def _take_registry_refresh_ticket() -> int:
    global _REGISTRY_REFRESH_TICKET

    with _REGISTRY_PUBLISH_GUARD:
        _REGISTRY_REFRESH_TICKET += 1
        return _REGISTRY_REFRESH_TICKET


class _registry_publication_of:
    """Hold publication order for one plugin, through its commit.

    Same shape and the same class-not-``@contextmanager`` reason as
    :class:`_registry_publication`; it just scopes the ordering to a single
    plugin instead of the whole registry.
    """

    __slots__ = (
        "_config_path",
        "_plugin_id",
        "_ticket",
        "_forced",
        "_clears_at_start",
        "_held",
    )

    def __init__(
        self,
        config_path: Path,
        ticket: int,
        *,
        forced: bool,
        plugin_id: str | None = None,
        clears_at_start: int | None = None,
    ) -> None:
        self._config_path = config_path
        self._plugin_id = plugin_id
        self._ticket = ticket
        self._forced = forced
        self._clears_at_start = clears_at_start
        self._held = False

    def __enter__(self) -> bool:
        _REGISTRY_PUBLISH_GUARD.acquire()
        # 同上：先对账，再动 _REGISTRY_PUBLISHED_PLUGIN_TICKET 和按插件的屏障。
        if self._clears_at_start is not None and _disk_transaction_superseded(
            self._clears_at_start
        ):
            _REGISTRY_PUBLISH_GUARD.release()
            return False
        if not _may_publish_record(
            self._ticket,
            self._config_path,
            forced=self._forced,
            plugin_id=self._plugin_id,
        ):
            _REGISTRY_PUBLISH_GUARD.release()
            return False
        self._held = True
        return True

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._held:
            self._held = False
            _REGISTRY_PUBLISH_GUARD.release()
        return False


def _disk_transaction_superseded(clears_at_start: int) -> bool:
    """Whether a disk-mutating transaction landed while this refresh was scanning.

    force 只跟别的 force 比号，这条规则本身是对的——但它有个盲区：卸载/替换/换源
    这些**改盘**的事务，收尾时发的是一次普通刷新。一次更早开始的 force 扫描于是能
    绕过它、把事务前的快照发布上去，把刚卸载的插件复活，或者把升级后的元数据换回
    旧的（codex）。

    刷新路由不进插件操作锁，所以两者之间没有互斥。但那些事务都会显式清扫描缓存，
    而清缓存是有计数的——扫描期间计数变过，就说明盘在我们脚下被换过，这份快照不
    该再发布。
    """
    return scan_cache_clear_count() != clears_at_start


class _registry_publication:
    """Claim publication order and hold it for the whole commit.

    Checking the ticket on the way in and then letting go is not enough: the
    refresh that claimed first can be descheduled, let a newer one publish, and
    then wake up and write its remaining stale records and removals on top. The
    claim and the mutations have to happen under one continuous hold (codex).

    A class rather than ``@contextmanager``, for the reason
    ``bounded_operation_wait`` is one too: ``_GeneratorContextManager.__exit__``
    assigns ``exc.__traceback__`` before throwing back into the generator, and
    ``ServerDomainError`` refuses attribute assignment — so a domain error
    raised inside the commit would surface as ``TypeError: super(type, obj)``.
    A plain ``__exit__`` never touches the exception.
    """

    __slots__ = ("_ticket", "_forced", "_clears_at_start", "_held")

    def __init__(
        self, ticket: int, *, forced: bool, clears_at_start: int | None = None
    ) -> None:
        self._ticket = ticket
        self._forced = forced
        self._clears_at_start = clears_at_start
        self._held = False

    def __enter__(self) -> bool:
        global _REGISTRY_PUBLISHED_TICKET, _REGISTRY_CACHE_BLIND_UNTIL
        global _REGISTRY_PUBLISHED_FORCED_TICKET

        _REGISTRY_PUBLISH_GUARD.acquire()
        # 事务对账要排在**动任何排序状态之前**。放在 with 体里判断的话，一次注定
        # 要被丢弃的 force 刷新照样已经把缓存盲区屏障抬到了最新号——紧接着事务自己
        # 那次收尾的普通刷新就被这道屏障挡掉，改盘的结果根本落不进 state.plugins
        # （CodeRabbit）。这比它原本要修的问题更糟。
        if self._clears_at_start is not None and _disk_transaction_superseded(
            self._clears_at_start
        ):
            _REGISTRY_PUBLISH_GUARD.release()
            return False
        # force 不让位于**缓存喂出来的**结果：它是唯一能看见目录外变化的那次读盘，
        # 被一份缓存结果顶掉就意味着升级/换源静默丢失，而返回值还是 success=True、
        # added/updated 全空，调用方看不出任何异常（CodeRabbit）。
        #
        # 但 force 之间仍然按号排：两次 force 都是读盘，一次更新的 force 说了算，
        # 否则先开始、后落地的那次会把新元数据和工具 schema 又换回旧的（codex）。
        if self._forced:
            # 只跟别的 force 比号：两次 force 都是读盘，新的说了算。普通刷新的
            # 号不参与，它可能是缓存喂出来的。
            outranked = self._ticket < _REGISTRY_PUBLISHED_FORCED_TICKET
        else:
            outranked = (
                self._ticket < _REGISTRY_PUBLISHED_TICKET
                or self._ticket <= _REGISTRY_CACHE_BLIND_UNTIL
            )
        if outranked:
            _REGISTRY_PUBLISH_GUARD.release()
            return False
        if self._forced:
            # 此刻还在途的普通刷新，它们的数据可能来自这次 force 刚作废掉的缓存，
            # 一律挡在门外。之后才领号的不受影响。
            _REGISTRY_CACHE_BLIND_UNTIL = _REGISTRY_REFRESH_TICKET
        _REGISTRY_PUBLISHED_TICKET = max(_REGISTRY_PUBLISHED_TICKET, self._ticket)
        if self._forced:
            _REGISTRY_PUBLISHED_FORCED_TICKET = max(
                _REGISTRY_PUBLISHED_FORCED_TICKET, self._ticket
            )
        self._held = True
        return True

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._held:
            self._held = False
            _REGISTRY_PUBLISH_GUARD.release()
        return False
_DISCOVERY_SCAN_MIN_WORKERS = 2


def _discovery_scan_workers(pending: int) -> int:
    """How many metadata scans to run at once for ``pending`` plugins.

    Everything here is capped by the global gate last, including the operator
    override and the lower bound. A pool wider than the gate buys nothing: the
    surplus threads only queue on ``_SCAN_SLOTS``, and waiting for a slot spends
    the plugin's own scan budget, so the extra width turns into scan failures
    rather than throughput. Raising the ceiling is what
    ``NEKO_PLUGIN_METADATA_SCAN_CONCURRENCY`` is for (CodeRabbit).
    """
    override = env_int("NEKO_PLUGIN_DISCOVERY_SCAN_WORKERS", 0, minimum=0)
    if override > 0:
        budget = override
    else:
        cpu = os.cpu_count() or 4
        # 先托底再封顶：反过来写的话，把全局闸调到 1 时下界仍然会顶出 2 个线程，
        # 多出来那个只能堵在信号量上白烧自己的扫描预算。
        budget = max(_DISCOVERY_SCAN_MIN_WORKERS, cpu // 4)
    budget = min(budget, _DISCOVERY_SCAN_MAX_WORKERS)
    return max(1, min(budget, pending))


def _build_discovery_record_safely(
    item: tuple[Path, PluginContext, float, bool],
) -> tuple[PluginDiscoveryRecord | None, PluginDiscoveryFailure | None]:
    """Build one record, turning any failure into a value.

    Returned rather than raised so the pool keeps a slot-for-slot result list:
    discovery order is load-bearing downstream (``_select_effective_records``
    builds its group ordering from first appearance), so results must come back
    in submission order, not completion order.
    """
    config_path, ctx, deadline, force = item
    # 剩余预算决定这一项还能扫多久。已经透支时传 0 —— 扫描器看到非正的 timeout
    # 会直接抛 ScanBudgetExhausted，连子进程都不起。
    remaining = deadline - time.monotonic()
    scan_timeout = min(_DEFAULT_ITEM_SCAN_TIMEOUT, remaining) if remaining > 0 else 0.0
    try:
        return (
            _build_discovery_record_from_context(
                ctx, scan_timeout=scan_timeout, force=force
            ),
            None,
        )
    except Exception as exc:  # noqa: BLE001 - one bad plugin must not stop discovery
        logger.warning(
            "plugin discovery payload failed for {}: err_type={}, err={}",
            config_path,
            type(exc).__name__,
            str(exc),
        )
        return None, PluginDiscoveryFailure(
            plugin_id=ctx.pid or config_path.parent.name or None,
            config_path=config_path,
            error=str(exc),
        )


def _is_forced_target(
    config_path: Path, ctx: PluginContext, force_targets: frozenset[str]
) -> bool:
    """Whether this one record was singled out for a forced rescan."""
    if not force_targets:
        return False
    if str(_resolve_config_path(config_path)) in force_targets:
        return True
    return bool(ctx.pid) and ctx.pid in force_targets


def _discover_registry_snapshot_sync(
    roots: tuple[Path, ...],
    *,
    force: bool = False,
    force_targets: frozenset[str] = frozenset(),
) -> PluginDiscoverySnapshot:
    processed_paths: set[Path] = set()
    pending: list[tuple[Path, PluginContext]] = []
    records: list[PluginDiscoveryRecord] = []
    failures: list[PluginDiscoveryFailure] = []
    config_paths: set[Path] = set()

    for root in roots:
        try:
            resolved_root = root.resolve()
        except Exception:
            resolved_root = root

        if not resolved_root.exists():
            logger.info("No plugin config directory {}, skipping", resolved_root)
            continue

        found_toml_files = [
            path
            for path in sorted(resolved_root.glob("*/plugin.toml"))
            if not path.parent.name.startswith(".")
        ]
        logger.info(
            "Found {} plugin.toml files in {}: {}",
            len(found_toml_files),
            resolved_root,
            [str(path) for path in found_toml_files],
        )

        for config_path in found_toml_files:
            config_paths.add(config_path.resolve())
            try:
                ctx = _parse_single_plugin_config(config_path, processed_paths, logger)
            except Exception as exc:
                logger.warning(
                    "plugin discovery failed for {}: err_type={}, err={}",
                    config_path,
                    type(exc).__name__,
                    str(exc),
                )
                failures.append(
                    PluginDiscoveryFailure(
                        plugin_id=config_path.parent.name or None,
                        config_path=config_path,
                        error=str(exc),
                    )
                )
                continue

            if ctx is None:
                failures.append(
                    PluginDiscoveryFailure(
                        plugin_id=config_path.parent.name or None,
                        config_path=config_path,
                        error="plugin config could not be parsed or validated",
                    )
                )
                continue

            # 解析很便宜（16 个插件合计约 40 ms），扫描很贵（每个约 0.84 s 的
            # 子进程）。先把 ctx 收齐，再一次性并行扫，别在解析的循环里逐个起
            # 进程——这是把 13.5 s 压到 2.6 s 的全部原因。
            pending.append((config_path, ctx))

    if pending:
        deadline = time.monotonic() + _DISCOVERY_SCAN_BUDGET_SECONDS
        pending = [
            (path, ctx, deadline, force or _is_forced_target(path, ctx, force_targets))
            for path, ctx in pending
        ]
        workers = _discovery_scan_workers(len(pending))
        if workers <= 1:
            built = [_build_discovery_record_safely(item) for item in pending]
        else:
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="plugin-discovery"
            ) as pool:
                # map，不是 as_completed：结果必须按提交顺序回来。
                built = list(pool.map(_build_discovery_record_safely, pending))
        for record, failure in built:
            if record is not None:
                records.append(record)
            elif failure is not None:
                failures.append(failure)

    effective_records, shadowed = _select_effective_records(records, roots)
    return PluginDiscoverySnapshot(
        records=effective_records,
        failures=failures,
        config_paths={_resolve_config_path(record.config_path) for record in effective_records},
        shadowed=shadowed,
    )


def _build_discovery_payload(
    ctx: PluginContext,
    *,
    plugin_id: str,
    scan_timeout: float | None = None,
    force: bool = False,
) -> dict[str, object]:
    plugin_type = str(ctx.pdata.get("type", "plugin") or "plugin")
    error_type: str | None = None
    error_message: str | None = None
    error_phase: str | None = None

    if not ctx.enabled:
        entries_preview = _extract_entries_preview(
            plugin_id,
            cls=type("DisabledPluginStub", (), {}),
            conf=ctx.conf,
            pdata=ctx.pdata,
        )
    else:
        entries_preview: list[dict[str, object]]
        entry_mismatch = describe_plugin_entry_directory_mismatch(
            ctx.entry,
            config_path=ctx.toml_path,
        )
        if entry_mismatch:
            error_type = "PluginEntryDirectoryMismatch"
            error_message = entry_mismatch
            error_phase = "entry_validation"
            entries_preview = _extract_entries_preview(
                plugin_id,
                cls=type("FailedPluginStub", (), {}),
                conf=ctx.conf,
                pdata=ctx.pdata,
            )
        else:
            dependency_errors: list[str] = []
            for dep in ctx.dependencies:
                satisfied, dep_error = _check_plugin_dependency(dep, logger, plugin_id)
                if not satisfied:
                    dependency_errors.append(str(dep_error or "dependency check failed"))
                    break
            if dependency_errors:
                error_type = "DependencyCheckFailed"
                error_message = dependency_errors[0]
                error_phase = "dependency_check"
                entries_preview = _extract_entries_preview(
                    plugin_id,
                    cls=type("FailedPluginStub", (), {}),
                    conf=ctx.conf,
                    pdata=ctx.pdata,
                )
            else:
                missing_requirements = _find_missing_python_requirements(
                    ctx.python_requirements,
                    search_paths=ctx.python_requirement_paths,
                )
                if missing_requirements:
                    error_type = "MissingPythonDependencies"
                    error_message = f"Unsatisfied Python dependencies: {missing_requirements}"
                    error_phase = "python_requirements"
                    entries_preview = _extract_entries_preview(
                        plugin_id,
                        cls=type("FailedPluginStub", (), {}),
                        conf=ctx.conf,
                        pdata=ctx.pdata,
                    )
                else:
                    try:
                        module_path, class_name = ctx.entry.split(":", 1)
                        isolated_metadata = scan_plugin_metadata_isolated(
                            plugin_id=plugin_id,
                            module_path=module_path,
                            class_name=class_name,
                            config_path=ctx.toml_path,
                            conf=ctx.conf,
                            pdata=ctx.pdata,
                            python_requirement_paths=ctx.python_requirement_paths,
                            force=force,
                            **(
                                {}
                                if scan_timeout is None
                                else {"timeout": scan_timeout}
                            ),
                        )
                        entries_preview = isolated_metadata.entries_preview
                    except PluginMetadataScanError as exc:
                        error_type = exc.error_type
                        error_message = str(exc)
                        error_phase = (
                            "import_class"
                            if exc.error_type == "AttributeError"
                            else "import_module"
                        )
                        entries_preview = _extract_entries_preview(
                            plugin_id,
                            cls=type("FailedPluginStub", (), {}),
                            conf=ctx.conf,
                            pdata=ctx.pdata,
                        )

    plugin_meta = _build_plugin_meta(
        plugin_id,
        ctx.pdata,
        sdk_supported_str=ctx.sdk_supported_str,
        sdk_recommended_str=ctx.sdk_recommended_str,
        sdk_untested_str=ctx.sdk_untested_str,
        sdk_conflicts_list=ctx.sdk_conflicts_list,
        dependencies=ctx.dependencies,
        plugin_ui=_extract_plugin_ui_config(ctx.conf, plugin_id=plugin_id, logger=logger),
    )
    payload = plugin_meta.model_dump(mode="python")
    payload["config_path"] = str(ctx.toml_path)
    payload["entry_point"] = ctx.entry
    payload["runtime_enabled"] = bool(ctx.enabled)
    payload["runtime_auto_start"] = bool(ctx.auto_start)
    payload["entries_preview"] = entries_preview
    payload["plugin_type"] = plugin_type
    if plugin_type == "adapter":
        adapter_conf = ctx.conf.get("adapter")
        if isinstance(adapter_conf, dict):
            payload["adapter_mode"] = str(adapter_conf.get("mode", "hybrid") or "hybrid")

    # "现在没时间"描述的是此刻，不是"这个插件是什么"——这句话我为缓存写过一次，
    # 却漏了注册表这一半。runtime_load_state="failed" 不只是个显示状态：
    # _get_autostart_plugin_ids_sync 会把 failed 的插件整个排除在自启动之外。于是
    # 一次冷启动扫描超预算（本 PR 之前根本没有预算，所以这是新引入的），会让排在
    # 后面那几个插件从此开机不再自启，而它们什么毛病都没有。
    #
    # 超时/预算耗尽这类**瞬时**失败照常记录错误字段供诊断，但不进 failed 状态：
    # 下一次刷新会重试它们。
    if _scan_failure_is_transient(error_type, scan_timeout):
        payload.pop("runtime_load_state", None)
        # 这一轮没扫成，所以 entries_preview 是个空壳（FailedPluginStub 生出来的）。
        # 直接发布会把插件上一次扫出来的条目和工具 schema 抹掉，而刷新还报 success
        # ——停着的插件就这么从 /plugins 里少了半张脸，直到下次扫描碰巧成功（codex）。
        # 打个标记，发布的时候把上一次的条目接回去。
        payload["runtime_scan_deferred"] = True
        payload["runtime_load_error_type"] = error_type
        payload["runtime_load_error_message"] = error_message or ""
        payload["runtime_load_error_phase"] = error_phase or "metadata_scan"
    elif error_type and error_message and error_phase:
        payload["runtime_load_state"] = "failed"
        payload["runtime_load_error_type"] = error_type
        payload["runtime_load_error_message"] = error_message
        payload["runtime_load_error_phase"] = error_phase
    else:
        payload.pop("runtime_load_state", None)
        payload.pop("runtime_load_error_type", None)
        payload.pop("runtime_load_error_message", None)
        payload.pop("runtime_load_error_phase", None)

    payload.pop("runtime_source_missing", None)
    return payload


def _build_discovery_record_from_context(
    ctx: PluginContext,
    *,
    scan_timeout: float | None = None,
    force: bool = False,
) -> PluginDiscoveryRecord:
    payload = _build_discovery_payload(
        ctx, plugin_id=ctx.pid, scan_timeout=scan_timeout, force=force
    )
    return PluginDiscoveryRecord(
        plugin_id=ctx.pid,
        original_plugin_id=ctx.pid,
        config_path=ctx.toml_path,
        entry_point=ctx.entry,
        plugin_type=str(ctx.pdata.get("type", "plugin") or "plugin"),
        enabled=bool(ctx.enabled),
        auto_start=bool(ctx.auto_start),
        meta_payload=payload,
    )


def _validate_plugin_runtime_source_sync(plugin_id: str, config_path: Path) -> None:
    """Validate one selected source even when its manifest disables runtime loading."""

    resolved_config_path = _resolve_config_path(config_path)
    ctx = _parse_single_plugin_config(resolved_config_path, set(), logger)
    if ctx is None or ctx.pid != plugin_id:
        raise RuntimeError("promoted plugin configuration could not be validated")

    payload = _build_discovery_payload(
        replace(ctx, enabled=True),
        plugin_id=plugin_id,
    )
    if payload.get("runtime_load_state") != "failed":
        return
    error_type = str(payload.get("runtime_load_error_type") or "unknown")
    error_phase = str(payload.get("runtime_load_error_phase") or "unknown")
    raise RuntimeError(
        "promoted plugin runtime validation failed "
        f"({error_type} during {error_phase})"
    )


def _apply_discovery_record_sync(
    record: PluginDiscoveryRecord,
    *,
    existing_snapshot: dict[str, dict[str, object]] | None = None,
    preferred_runtime_plugin_id: str | None = None,
) -> tuple[str, dict[str, object]]:
    target_plugin_id = preferred_runtime_plugin_id
    if target_plugin_id is None and existing_snapshot is not None:
        target_plugin_id = _find_existing_runtime_plugin_id_by_config_path(
            record.config_path,
            existing_snapshot,
        )
    if target_plugin_id is None:
        target_plugin_id = record.plugin_id

    existing_target_meta = (existing_snapshot or {}).get(target_plugin_id)
    existing_target_path = _resolve_meta_config_path(existing_target_meta)
    source_replacement = (
        target_plugin_id == record.plugin_id
        and existing_target_path is not None
        and existing_target_path != _resolve_config_path(record.config_path)
        and (
            bool(record.meta_payload.get("shadowed_builtin_path"))
            or not existing_target_path.exists()
        )
    )

    runtime_plugin_id = target_plugin_id if source_replacement else _resolve_plugin_id_conflict(
        target_plugin_id,
        logger,
        config_path=record.config_path,
        entry_point=record.entry_point,
        plugin_data=record.meta_payload,
        purpose="register",
        enable_rename=True,
    )
    if runtime_plugin_id is None:
        raise ServerDomainError(
            code="PLUGIN_REGISTRY_CONFLICT",
            message=f"Plugin '{record.plugin_id}' could not be registered due to an ID conflict",
            status_code=409,
            details={"plugin_id": record.plugin_id},
        )

    plugin_meta = _build_plugin_meta(
        runtime_plugin_id,
        {
            "name": record.meta_payload.get("name", runtime_plugin_id),
            "type": record.meta_payload.get("type", record.plugin_type),
            "description": record.meta_payload.get("description", ""),
            "short_description": record.meta_payload.get("short_description", ""),
            "keywords": record.meta_payload.get("keywords", []),
            "passive": record.meta_payload.get("passive", False),
            "version": record.meta_payload.get("version", "0.1.0"),
            "author": record.meta_payload.get("author"),
        },
        sdk_supported_str=record.meta_payload.get("sdk_supported") if isinstance(record.meta_payload.get("sdk_supported"), str) else None,
        sdk_recommended_str=record.meta_payload.get("sdk_recommended") if isinstance(record.meta_payload.get("sdk_recommended"), str) else None,
        sdk_untested_str=record.meta_payload.get("sdk_untested") if isinstance(record.meta_payload.get("sdk_untested"), str) else None,
        sdk_conflicts_list=record.meta_payload.get("sdk_conflicts") if isinstance(record.meta_payload.get("sdk_conflicts"), list) else None,
        dependencies=record.meta_payload.get("dependencies") if isinstance(record.meta_payload.get("dependencies"), list) else None,
        plugin_ui=record.meta_payload.get("plugin_ui") if isinstance(record.meta_payload.get("plugin_ui"), dict) else None,
    )
    if source_replacement:
        resolved_id = runtime_plugin_id
        with state.acquire_plugins_write_lock():
            replacement_dump = plugin_meta.model_dump(mode="python")
            replacement_dump["config_path"] = str(record.config_path)
            replacement_dump["entry_point"] = record.entry_point
            state.plugins[resolved_id] = replacement_dump
        state.invalidate_snapshot_cache("plugins")
    else:
        resolved_id = register_plugin(
            plugin_meta,
            logger,
            config_path=record.config_path,
            entry_point=record.entry_point,
        )
    if resolved_id is None:
        raise ServerDomainError(
            code="PLUGIN_REGISTRY_CONFLICT",
            message=f"Plugin '{record.plugin_id}' could not be registered due to an ID conflict",
            status_code=409,
            details={"plugin_id": record.plugin_id},
        )

    payload = dict(record.meta_payload)
    if resolved_id != record.plugin_id:
        payload["id"] = resolved_id
        preview_obj = payload.get("entries_preview")
        if isinstance(preview_obj, list):
            payload["entries_preview"] = _remap_entries_preview_plugin_id(
                [item for item in preview_obj if isinstance(item, dict)],
                plugin_id=resolved_id,
            )

    with state.acquire_plugins_write_lock():
        current_meta = state.plugins.get(resolved_id)
        merged = dict(current_meta) if isinstance(current_meta, dict) else {}
        for key in _MANAGED_META_KEYS:
            if key in payload:
                merged[key] = payload[key]
            else:
                merged.pop(key, None)
        state.plugins[resolved_id] = merged
    state.invalidate_snapshot_cache("plugins")
    return resolved_id, payload


def _remove_config_path_aliases_sync(config_path: Path, *, keep_plugin_id: str) -> list[str]:
    resolved_path = _resolve_config_path(config_path)
    running_ids = _list_running_plugin_ids_sync()
    removed: list[str] = []
    kept_running: list[str] = []
    with state.acquire_plugins_write_lock():
        for plugin_id, raw_meta in list(state.plugins.items()):
            if plugin_id == keep_plugin_id or not isinstance(raw_meta, dict):
                continue
            if _resolve_meta_config_path(raw_meta) != resolved_path:
                continue
            if plugin_id in running_ids:
                preserved = dict(raw_meta)
                preserved["runtime_source_missing"] = True
                state.plugins[plugin_id] = preserved
                kept_running.append(plugin_id)
                continue
            state.plugins.pop(plugin_id, None)
            removed.append(plugin_id)
    if removed or kept_running:
        state.invalidate_snapshot_cache("plugins")
    return removed


def _remove_stale_plugin_metadata_sync(
    stale_ids: set[str],
    *,
    running_ids: set[str],
) -> tuple[list[str], list[str]]:
    removed: list[str] = []
    kept_running: list[str] = []
    with state.acquire_plugins_write_lock():
        for plugin_id in sorted(stale_ids):
            raw_meta = state.plugins.get(plugin_id)
            if not isinstance(raw_meta, dict):
                continue
            if plugin_id in running_ids:
                raw_meta["runtime_source_missing"] = True
                state.plugins[plugin_id] = raw_meta
                kept_running.append(plugin_id)
                continue
            state.plugins.pop(plugin_id, None)
            removed.append(plugin_id)
    if removed or kept_running:
        state.invalidate_snapshot_cache("plugins")
    return removed, kept_running


def _collect_missing_plugin_ids_sync(existing_snapshot: dict[str, dict[str, object]]) -> set[str]:
    missing_ids: set[str] = set()
    for plugin_id, meta in existing_snapshot.items():
        config_path_obj = meta.get("config_path")
        if not isinstance(config_path_obj, str) or not config_path_obj:
            continue
        try:
            config_path = Path(config_path_obj).resolve()
        except Exception:
            config_path = Path(config_path_obj)
        if not config_path.exists():
            missing_ids.add(plugin_id)
    return missing_ids


def _get_autostart_plugin_ids_sync() -> list[str]:
    candidates: set[str] = set()
    with state.acquire_plugins_read_lock():
        for plugin_id, raw_meta in state.plugins.items():
            if not isinstance(plugin_id, str) or not isinstance(raw_meta, dict):
                continue
            if raw_meta.get("runtime_enabled") is False:
                continue
            if raw_meta.get("runtime_auto_start") is False:
                continue
            if raw_meta.get("runtime_load_state") == "failed":
                continue
            if raw_meta.get("runtime_source_missing") is True:
                continue
            candidates.add(plugin_id)
    return _build_ordered_plugin_ids_sync(candidates)


class PluginRegistryService:
    async def refresh_registry(self, *, force: bool = False) -> dict[str, object]:
        """Rebuild the registry. ``force`` re-reads plugins ignoring the cache.

        The scan cache is keyed on file contents under each plugin directory,
        which cannot see a change outside it (a shared ``vendor/``, a package
        reinstalled into site-packages). So every path where the content may
        have moved behind our back passes ``force=True``: install, upgrade,
        uninstall, and the refresh button the user pressed — pressing it means
        "go look again", and answering from cache would make it a no-op.
        """
        # force 顺着扫描链传下去，而不是先清缓存再扫。
        #
        # "清了再扫"不是原子的：清掉之后、这个 worker 查之前，另一次并发的普通
        # 扫描可以把旧条目填回来，或者在之后用旧结果覆盖掉这次的新结果。于是
        # 一次显式刷新仍可能返回陈旧元数据——而 force 存在的全部理由正是探测
        # 那些键看不见的外部变化（codex）。
        return await asyncio.to_thread(self._refresh_registry_sync, force)

    async def refresh_plugin(
        self, plugin_id: str, *, force: bool = False
    ) -> dict[str, object]:
        """Rebuild one plugin's registry entry.

        ``force`` drops just that plugin's cached scan — same reasoning as the
        all-plugins refresh (the key cannot see a change outside the plugin
        directory), but scoped, so refreshing one plugin does not make the other
        sixteen pay for a rescan.
        """
        # 同 refresh_registry：force 顺着扫描链传下去，不靠"先清再扫"——那中间
        # 有一段窗口，并发的普通扫描能把旧条目填回来。
        return await asyncio.to_thread(self._refresh_plugin_sync, plugin_id, force)

    async def validate_plugin_runtime_source(
        self,
        *,
        plugin_id: str,
        config_path: Path,
    ) -> None:
        await asyncio.to_thread(
            _validate_plugin_runtime_source_sync,
            plugin_id,
            config_path,
        )

    async def list_autostart_plugin_ids(self) -> list[str]:
        return await asyncio.to_thread(_get_autostart_plugin_ids_sync)

    async def order_plugin_ids(self, plugin_ids: list[str]) -> list[str]:
        return await asyncio.to_thread(self._order_plugin_ids_sync, plugin_ids)

    def _refresh_registry_sync(self, force: bool = False) -> dict[str, object]:
        ticket = _take_registry_refresh_ticket()
        clears_at_start = scan_cache_clear_count()
        roots = tuple(PLUGIN_CONFIG_ROOTS)
        _prepare_plugin_import_roots(roots, logger)

        existing_snapshot = _get_registered_plugin_snapshot_sync()
        running_ids = _list_running_plugin_ids_sync()
        added: list[str] = []
        updated: list[str] = []
        unchanged: list[str] = []
        refreshed_ids: set[str] = set()
        snapshot = _discover_registry_snapshot_sync(roots, force=force)
        # 发布整段互斥，不是在门口点个卯。只在进入前认号的话，先认号的那次可以
        # 认完就被调度出去，让后认号的那次把新快照发布完，然后自己醒过来把剩下
        # 的旧记录和删除接着写进去——注册表照样被旧的一份盖掉（codex）。锁从认号
        # 一直握到提交结束，发布之间才真的有序。
        with _registry_publication(
            ticket, forced=force, clears_at_start=clears_at_start
        ) as may_publish:
            if not may_publish:
                # 我们扫描期间已经有更晚开始的刷新把结果发布出去了。手上这份是照着更旧
                # 的盘面读出来的，写进注册表就是把它盖回去。
                logger.info(
                    "registry refresh #{} superseded before publishing; discarding {} record(s)",
                    ticket,
                    len(snapshot.records),
                )
                return {
                    "success": True,
                    "added": [],
                    "updated": [],
                    "removed": [],
                    "removed_running": [],
                    "unchanged": [record.plugin_id for record in snapshot.records],
                    "failed": [],
                    "shadowed": [],
                    "scanned_count": len(snapshot.records) + len(snapshot.failures),
                    "superseded": True,
                }
            failed = [
                {
                    "plugin_id": item.plugin_id or "",
                    "config_path": str(item.config_path),
                    "error": item.error,
                }
                for item in snapshot.failures
            ]

            for record in snapshot.records:
                if not _may_publish_record(
                    ticket, record.config_path, forced=force, plugin_id=record.plugin_id
                ):
                    # 这个插件在我们扫描期间被一次更晚的刷新更新过了。别的插件照常
                    # 发布——整轮作废是过度反应，那正是按插件分号要避免的。
                    unchanged.append(record.plugin_id)
                    refreshed_ids.add(record.plugin_id)
                    continue
                try:
                    previous_runtime_plugin_id = _find_existing_runtime_plugin_id_by_config_path(
                        record.config_path,
                        existing_snapshot,
                    )
                    if record.meta_payload.get("shadowed_builtin_path"):
                        # A valid user override always owns the declared ID. Clean
                        # up aliases left by the legacy conflict renamer instead of
                        # perpetuating ``study_companion_1``.
                        previous_runtime_plugin_id = record.plugin_id
                    previous_plugin_id = previous_runtime_plugin_id or record.plugin_id
                    previous_managed = _select_managed_fields(existing_snapshot.get(previous_plugin_id, {}))
                    resolved_id, payload = _apply_discovery_record_sync(
                        _keep_known_entries_on_deferred_scan(
                            record, existing_snapshot.get(previous_plugin_id)
                        ),
                        existing_snapshot=existing_snapshot,
                        preferred_runtime_plugin_id=previous_runtime_plugin_id,
                    )
                    _claim_resolved_runtime_id(ticket, resolved_id, forced=force)
                    if record.meta_payload.get("shadowed_builtin_path"):
                        _remove_config_path_aliases_sync(record.config_path, keep_plugin_id=resolved_id)
                    refreshed_ids.add(resolved_id)
                    current_managed = _select_managed_fields(payload)
                    if resolved_id not in existing_snapshot:
                        added.append(resolved_id)
                    elif previous_managed == current_managed:
                        unchanged.append(resolved_id)
                    else:
                        updated.append(resolved_id)
                except ServerDomainError as exc:
                    failed.append(
                        {
                            "plugin_id": record.plugin_id,
                            "config_path": str(record.config_path),
                            "error": exc.message,
                        }
                    )
                except Exception as exc:
                    logger.warning(
                        "refresh_registry failed for plugin {}: err_type={}, err={}",
                        record.plugin_id,
                        type(exc).__name__,
                        str(exc),
                    )
                    failed.append(
                        {
                            "plugin_id": record.plugin_id,
                            "config_path": str(record.config_path),
                            "error": str(exc),
                        }
                    )

            missing_ids = _collect_missing_plugin_ids_sync(existing_snapshot) - refreshed_ids
            missing_ids = {
                plugin_id
                for plugin_id in missing_ids
                if _may_remove_plugin(ticket, plugin_id)
            }
            removed, removed_running = _remove_stale_plugin_metadata_sync(missing_ids, running_ids=running_ids)
            return {
                "success": not failed,
                "added": added,
                "updated": updated,
                "removed": removed,
                "removed_running": removed_running,
                "unchanged": unchanged,
                "failed": failed,
                "shadowed": [
                    {
                        "plugin_id": record.plugin_id,
                        "config_path": str(record.config_path),
                        "source": _source_for_config_path(record.config_path),
                    }
                    for record in snapshot.shadowed
                ],
                "scanned_count": len(snapshot.records) + len(snapshot.failures),
            }

    def _refresh_plugin_sync(
        self, plugin_id: str, force: bool = False
    ) -> dict[str, object]:
        ticket = _take_registry_refresh_ticket()
        clears_at_start = scan_cache_clear_count()
        normalized_plugin_id = plugin_id.strip()
        if not _PLUGIN_ID_PATTERN.fullmatch(normalized_plugin_id):
            raise ServerDomainError(
                code="PLUGIN_INVALID_ID",
                message="Invalid plugin id",
                status_code=400,
                details={"plugin_id": plugin_id},
            )

        roots = tuple(PLUGIN_CONFIG_ROOTS)
        existing_snapshot = _get_registered_plugin_snapshot_sync()
        _prepare_plugin_import_roots(roots, logger)
        existing_config_path = _resolve_meta_config_path(existing_snapshot.get(normalized_plugin_id))
        record: PluginDiscoveryRecord | None = None
        if (
            existing_config_path is not None
            and existing_config_path.exists()
            and not _config_path_belongs_to_roots(existing_config_path, roots)
        ):
            ctx = _parse_single_plugin_config(existing_config_path, set(), logger)
            if ctx is not None:
                record = _build_discovery_record_from_context(ctx, force=force)
        else:
            # force 只落在被点的那个插件上。整轮 discovery 都跟着 force 的话，
            # 刷新一个插件会让其余十几个全部绕过缓存重扫：不相关的慢插件先把
            # 扫描预算吃掉，本来健康的目标插件反而被记成扫描失败；就算一切顺利，
            # 也白付了一次冷启动全量扫描的钱（codex / CodeRabbit）。
            force_targets: frozenset[str] = frozenset()
            if force:
                targets = {normalized_plugin_id}
                if existing_config_path is not None:
                    targets.add(str(_resolve_config_path(existing_config_path)))
                force_targets = frozenset(targets)
            discovery = _discover_registry_snapshot_sync(
                roots, force_targets=force_targets
            )
            record = next(
                (
                    item
                    for item in discovery.records
                    if existing_config_path is not None
                    and _resolve_config_path(item.config_path) == existing_config_path
                ),
                None,
            )
            if record is None:
                record = next(
                    (item for item in discovery.records if item.plugin_id == normalized_plugin_id),
                    None,
                )
        config_path = record.config_path if record is not None else None
        if config_path is None:
            raise ServerDomainError(
                code="PLUGIN_CONFIG_NOT_FOUND",
                message=f"Plugin '{normalized_plugin_id}' configuration not found",
                status_code=404,
                details={"plugin_id": normalized_plugin_id},
            )

        # 单插件刷新写的也是 state.plugins，所以它必须和全量刷新排在同一个顺序里，
        # 否则一次慢的全量刷新醒过来照样能把这条刚更新的记录盖回旧的（codex）。
        # 但它只推**自己这一条**的号：单插件刷新是 start_plugin 的必经之路，让它去
        # 推全局号等于启动一个插件就能作废一次全量刷新。
        with _registry_publication_of(
            config_path,
            ticket,
            forced=force,
            plugin_id=record.plugin_id,
            clears_at_start=clears_at_start,
        ) as may_publish:
            if not may_publish:
                logger.info(
                    "plugin refresh #{} for {} superseded before publishing",
                    ticket,
                    normalized_plugin_id,
                )
                return {
                    "success": True,
                    "plugin_id": normalized_plugin_id,
                    "original_plugin_id": normalized_plugin_id,
                    "status": "unchanged",
                    "config_path": str(config_path),
                    "superseded": True,
                }
            previous_runtime_plugin_id = _find_existing_runtime_plugin_id_by_config_path(
                config_path,
                existing_snapshot,
            )
            if record.meta_payload.get("shadowed_builtin_path"):
                previous_runtime_plugin_id = record.plugin_id
            previous_plugin_id = previous_runtime_plugin_id or normalized_plugin_id
            previous_managed = _select_managed_fields(existing_snapshot.get(previous_plugin_id, {}))
            resolved_id, payload = _apply_discovery_record_sync(
                _keep_known_entries_on_deferred_scan(
                    record, existing_snapshot.get(previous_plugin_id)
                ),
                existing_snapshot=existing_snapshot,
                preferred_runtime_plugin_id=previous_runtime_plugin_id,
            )
            _claim_resolved_runtime_id(ticket, resolved_id, forced=force)
            if record.meta_payload.get("shadowed_builtin_path"):
                _remove_config_path_aliases_sync(config_path, keep_plugin_id=resolved_id)
            current_managed = _select_managed_fields(payload)
            status = "added"
            if previous_plugin_id in existing_snapshot:
                status = "unchanged" if previous_managed == current_managed else "updated"

            return {
                "success": True,
                "plugin_id": resolved_id,
                "original_plugin_id": normalized_plugin_id,
                "status": status,
                "config_path": str(config_path),
            }

    def _order_plugin_ids_sync(self, plugin_ids: list[str]) -> list[str]:
        return _build_ordered_plugin_ids_sync({plugin_id for plugin_id in plugin_ids if isinstance(plugin_id, str)})
