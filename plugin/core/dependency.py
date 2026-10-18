"""
Plugin dependency checking and topological sorting.

Extracted from registry.py; responsible for:
- Version-spec parsing.
- Dependency lookup by entry points and custom events.
- Dependency checking.
- Topological sorting.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING


from plugin.core.state import state
from plugin._types.models import PluginDependency
from plugin._types.events import STANDARD_EVENT_TYPES

if TYPE_CHECKING:
    from plugin.core.registry import PluginContext

try:
    from packaging.version import Version, InvalidVersion
    from packaging.specifiers import SpecifierSet, InvalidSpecifier
except ImportError:  # pragma: no cover
    Version = None  # type: ignore
    InvalidVersion = Exception  # type: ignore
    SpecifierSet = None  # type: ignore
    InvalidSpecifier = Exception  # type: ignore

def _parse_specifier(spec: Optional[str], logger: Any) -> Optional[Any]:
    """Parse a version-specification string."""
    if not spec or SpecifierSet is None:
        return None
    try:
        return SpecifierSet(spec)
    except InvalidSpecifier as e:
        logger.error("Invalid sdk specifier '{}': {}", spec, e)
        return None


def _version_matches(spec: Optional[Any], version: Any) -> bool:
    """Check whether a version matches a specification."""
    if spec is None:
        return False
    try:
        return version in spec
    except Exception:
        return False


def _find_plugins_by_entry(entry_id: str) -> List[tuple[str, Dict[str, Any]]]:
    """
    Find all plugins providing an entry ID (only @plugin_entry).
    
    Args:
        entry_id: Entry-point ID.
    
    Returns:
        List of (plugin ID, plugin metadata).
    """
    matching_plugins = []
    
    with state.acquire_event_handlers_read_lock():
        event_handlers_copy = dict(state.event_handlers)
    
    # Find every plugin providing this entry point.
    found_plugin_ids = set()
    for key, eh in event_handlers_copy.items():
        # Check key format: plugin_id.entry_id or plugin_id:plugin_entry:entry_id.
        if "." in key:
            parts = key.split(".", 1)
            if len(parts) == 2 and parts[1] == entry_id:
                # Verify plugin_entry type.
                meta = getattr(eh, "meta", None)
                if meta and getattr(meta, "event_type", None) == "plugin_entry":
                    found_plugin_ids.add(parts[0])
        elif ":" in key:
            parts = key.split(":", 2)
            if len(parts) == 3 and parts[1] == "plugin_entry" and parts[2] == entry_id:
                found_plugin_ids.add(parts[0])
    
    # Get these plugins' metadata.
    with state.acquire_plugins_read_lock():
        for pid in found_plugin_ids:
            if pid in state.plugins:
                meta = state.plugins[pid]
                # Disabled plugins are visible but should not participate in dependency satisfaction.
                if isinstance(meta, dict) and meta.get("runtime_enabled") is False:
                    continue
                matching_plugins.append((pid, meta))
    
    return matching_plugins


def _find_plugins_by_custom_event(event_type: str, event_id: str) -> List[tuple[str, Dict[str, Any]]]:
    """
    Find all plugins providing a custom-event type/ID (only @custom_event).
    
    Args:
        event_type: Custom-event type.
        event_id: Event ID.
    
    Returns:
        List of (plugin ID, plugin metadata).
    """
    matching_plugins = []
    
    with state.acquire_event_handlers_read_lock():
        event_handlers_copy = dict(state.event_handlers)
    
    # Find every plugin providing this custom event.
    found_plugin_ids = set()
    for key, _eh in event_handlers_copy.items():
        # Check key format: plugin_id:event_type:event_id.
        if ":" in key:
            parts = key.split(":", 2)
            if len(parts) == 3:
                pid, etype, eid = parts
                if etype == event_type and eid == event_id:
                    # Verify nonstandard type.
                    if etype not in STANDARD_EVENT_TYPES:
                        found_plugin_ids.add(pid)
    
    # Get these plugins' metadata.
    with state.acquire_plugins_read_lock():
        for pid in found_plugin_ids:
            if pid in state.plugins:
                meta = state.plugins[pid]
                # Disabled plugins are visible but should not participate in dependency satisfaction.
                if isinstance(meta, dict) and meta.get("runtime_enabled") is False:
                    continue
                matching_plugins.append((pid, meta))
    
    return matching_plugins


def _check_single_plugin_version(
    dep_id: str,
    dep_plugin_meta: Dict[str, Any],
    dependency: PluginDependency,
    logger: Any,
    plugin_id: str
) -> tuple[bool, Optional[str]]:
    """
    Check one plugin's version against dependency requirements.
    
    Args:
        dep_id: Dependency plugin ID.
        dep_plugin_meta: Dependency plugin metadata.
        dependency: Dependency configuration.
        logger: Logger.
        plugin_id: Current plugin ID for logs.
    
    Returns:
        (Satisfied, error message).
    """
    dep_version_str = dep_plugin_meta.get("version", "0.0.0")
    
    # If conflicts is a list, check whether the version lies in its conflict ranges.
    if isinstance(dependency.conflicts, list) and dependency.conflicts:
        if Version and SpecifierSet:
            try:
                dep_version_obj = Version(dep_version_str)
                conflict_specs = [
                    _parse_specifier(conf, logger) for conf in dependency.conflicts
                ]
                if any(spec and _version_matches(spec, dep_version_obj) for spec in conflict_specs):
                    return False, f"Dependency plugin '{dep_id}' version {dep_version_str} conflicts with required ranges: {dependency.conflicts}"
            except InvalidVersion:
                logger.warning("Cannot parse dependency plugin '{}' version '{}'", dep_id, dep_version_str)
    
    # Dependency configurations require untested.
    if dependency.untested is None:
        return False, "Dependency configuration requires 'untested' field"
    
    # Check whether version is in untested range.
    if Version and SpecifierSet:
        try:
            dep_version_obj = Version(dep_version_str)
            untested_spec = _parse_specifier(dependency.untested, logger)
            
            if untested_spec is None:
                return False, f"Invalid dependency 'untested' specifier: {dependency.untested!r}"
            if untested_spec:
                in_untested = _version_matches(untested_spec, dep_version_obj)
                if not in_untested:
                    # Check supported range.
                    supported_spec = _parse_specifier(dependency.supported, logger)
                    in_supported = _version_matches(supported_spec, dep_version_obj) if supported_spec else False
                    
                    if not in_supported:
                        return False, (
                            f"Dependency plugin '{dep_id}' version {dep_version_str} "
                            f"does not match untested range '{dependency.untested}' "
                            f"(or supported range '{dependency.supported or 'N/A'}')"
                        )
            
            # Check recommended range (warning).
            if dependency.recommended:
                recommended_spec = _parse_specifier(dependency.recommended, logger)
                if recommended_spec and not _version_matches(recommended_spec, dep_version_obj):
                    logger.warning(
                        "Plugin {}: dependency '{}' version {} is outside recommended range {}",
                        plugin_id, dep_id, dep_version_str, dependency.recommended
                    )
        except InvalidVersion:
            logger.warning("Cannot parse dependency plugin '{}' version '{}'", dep_id, dep_version_str)
    
    return True, None


def _check_plugin_dependency(
    dependency: PluginDependency,
    logger: Any,
    plugin_id: str
) -> tuple[bool, Optional[str]]:
    """
    Check plugin dependency satisfaction.
    
    Support four dependency modes:
    1. Depend on a specific plugin ID: id = \"plugin_id\"
    2. Depend on a specific entry point: entry = \"entry_id\" or entry = \"plugin_id:entry_id\" (may reference only @plugin_entry)
    3. Depend on a specific custom event: custom_event = \"event_type:event_id\" or custom_event = \"plugin_id:event_type:event_id\" (may reference only @custom_event)
    4. Depend on multiple candidate plugins: providers = [\"plugin1\", \"plugin2\"] (any one is sufficient)
    
    Note: entry and custom_event are mutually exclusive.
    
    Args:
        dependency: Dependency configuration.
        logger: Logger.
        plugin_id: Current plugin ID for logs.
    
    Returns:
        (Satisfied, error message).
    """
    def _runtime_enabled(pid: str) -> bool:
        with state.acquire_plugins_read_lock():
            meta = state.plugins.get(pid)
        if isinstance(meta, dict) and meta.get("runtime_enabled") is False:
            return False
        return meta is not None

    # conflicts=true means disallowed conflict.
    if dependency.conflicts is True:
        if not dependency.id:
            return False, "Dependency with conflicts=True requires 'id' field"

        if dependency.id:
            # Check whether the dependency plugin exists.
            if _runtime_enabled(str(dependency.id)):
                return False, f"Dependency plugin '{dependency.id}' conflicts (conflicts=true) but plugin exists"
        return True, None  # Simplified format: missing plugins satisfy the condition.
    
    # Determine plugins to check.
    plugins_to_check: List[tuple[str, Dict[str, Any]]] = []
    
    if dependency.providers:
        # Mode 3: multiple candidate plugins, any one sufficient.
        with state.acquire_plugins_read_lock():
            for provider_id in dependency.providers:
                meta = state.plugins.get(provider_id)
                if meta is None:
                    continue
                if isinstance(meta, dict) and meta.get("runtime_enabled") is False:
                    continue
                plugins_to_check.append((provider_id, meta))
        
        if not plugins_to_check:
            return False, f"None of the provider plugins {dependency.providers} found"
        
        # Check whether any plugin satisfies requirements.
        for dep_id, dep_plugin_meta in plugins_to_check:
            satisfied, _ = _check_single_plugin_version(
                dep_id, dep_plugin_meta, dependency, logger, plugin_id
            )
            if satisfied:
                logger.debug("Plugin {}: dependency satisfied by provider '{}'", plugin_id, dep_id)
                return True, None
        
        # No candidate satisfies requirements.
        return False, f"None of the provider plugins {dependency.providers} satisfy version requirements"
    
    elif dependency.entry:
        # Mode 2: specific entry-point dependency, referencing @plugin_entry only.
        # Check mutually exclusive custom_event is not also specified.
        if dependency.custom_event:
            return False, "Cannot specify both 'entry' and 'custom_event' in dependency (they are mutually exclusive)"
        
        entry_spec = dependency.entry
        if ":" in entry_spec:
            # Format: plugin_id:entry_id.
            parts = entry_spec.split(":", 1)
            if len(parts) != 2:
                return False, f"Invalid entry format: '{entry_spec}', expected 'plugin_id:entry_id' or 'entry_id'"
            target_plugin_id, target_entry_id = parts

            with state.acquire_plugins_read_lock():
                if target_plugin_id not in state.plugins:
                    return False, f"Dependency entry '{entry_spec}': plugin '{target_plugin_id}' not found"

            # Verify that plugin really provides the entry as @plugin_entry.
            matching_plugins = _find_plugins_by_entry(target_entry_id)
            if not any(pid == target_plugin_id for pid, _ in matching_plugins):
                return False, (
                    f"Dependency entry '{entry_spec}': plugin '{target_plugin_id}' does not provide entry '{target_entry_id}'"
                )

            with state.acquire_plugins_read_lock():
                plugins_to_check = [(target_plugin_id, state.plugins[target_plugin_id])]
        else:
            # Format: entry_id, provided by any plugin.
            entry_id = entry_spec
            matching_plugins = _find_plugins_by_entry(entry_id)
            if not matching_plugins:
                return False, f"Dependency entry '{entry_id}' not found in any plugin"
            plugins_to_check = matching_plugins
        
        # Check version requirements for entry providers.
        # With multiple providers, any one is sufficient.
        for dep_id, dep_plugin_meta in plugins_to_check:
            satisfied, _ = _check_single_plugin_version(
                dep_id, dep_plugin_meta, dependency, logger, plugin_id
            )
            if satisfied:
                logger.debug("Plugin {}: dependency entry '{}' satisfied by plugin '{}'", plugin_id, entry_spec, dep_id)
                return True, None
        
        # No entry provider meets version requirements.
        return False, f"Dependency entry '{entry_spec}' found but version requirements not satisfied"
    
    elif dependency.custom_event:
        # Mode 3: specific custom-event dependency, referencing @custom_event only.
        custom_event_spec = dependency.custom_event
        if ":" in custom_event_spec:
            # Parse formats:
            # - event_type:event_id (exactly one ':').
            # - plugin_id:event_type:event_id (event_id may contain ':'; split only twice).
            if custom_event_spec.count(":") == 1:
                # Format: event_type:event_id, provided by any plugin.
                event_type, event_id = custom_event_spec.split(":", 1)
                matching_plugins = _find_plugins_by_custom_event(event_type, event_id)
                if not matching_plugins:
                    return False, f"Dependency custom_event '{custom_event_spec}' not found in any plugin"
                plugins_to_check = matching_plugins
            else:
                # Format: plugin_id:event_type:event_id, provided by the specified plugin.
                target_plugin_id, rest = custom_event_spec.split(":", 1)
                if ":" not in rest:
                    return False, f"Invalid custom_event format: '{custom_event_spec}', expected 'event_type:event_id' or 'plugin_id:event_type:event_id'"
                event_type, event_id = rest.split(":", 1)
                # Check existence and read metadata under lock first, avoiding deadlocks from later nested plugins_lock acquisition.
                with state.acquire_plugins_read_lock():
                    dep_meta = state.plugins.get(target_plugin_id)
                if dep_meta is None:
                    return False, f"Dependency custom_event '{custom_event_spec}': plugin '{target_plugin_id}' not found"
                # Ensure disabled plugins do not satisfy dependencies.
                if isinstance(dep_meta, dict) and dep_meta.get("runtime_enabled") is False:
                    return False, f"Dependency custom_event '{custom_event_spec}': plugin '{target_plugin_id}' does not provide event '{event_type}.{event_id}'"

                # Call outside the lock, avoiding _find_plugins_by_custom_event reacquiring plugins_lock.
                matching_plugins = _find_plugins_by_custom_event(event_type, event_id)
                if not any(pid == target_plugin_id for pid, _ in matching_plugins):
                    return False, (
                        f"Dependency custom_event '{custom_event_spec}': plugin '{target_plugin_id}' does not provide event '{event_type}.{event_id}'"
                    )
                plugins_to_check = [(target_plugin_id, dep_meta)]
        else:
            return False, f"Invalid custom_event format: '{custom_event_spec}', expected 'event_type:event_id' or 'plugin_id:event_type:event_id'"
        
        # Check custom-event providers' versions.
        # With multiple providers, any one is sufficient.
        for dep_id, dep_plugin_meta in plugins_to_check:
            satisfied, _ = _check_single_plugin_version(
                dep_id, dep_plugin_meta, dependency, logger, plugin_id
            )
            if satisfied:
                logger.debug("Plugin {}: dependency custom_event '{}' satisfied by plugin '{}'", plugin_id, custom_event_spec, dep_id)
                return True, None
        
        # No event provider meets version requirements.
        return False, f"Dependency custom_event '{custom_event_spec}' found but version requirements not satisfied"
    
    elif dependency.id:
        # Mode 1: specific plugin ID dependency.
        dep_id = dependency.id
        with state.acquire_plugins_read_lock():
            if dep_id not in state.plugins:
                return False, f"Dependency plugin '{dep_id}' not found"
            dep_plugin_meta = state.plugins[dep_id]

        # Disabled plugins are visible but must not satisfy dependencies.
        if isinstance(dep_plugin_meta, dict) and dep_plugin_meta.get("runtime_enabled") is False:
            return False, f"Dependency plugin '{dep_id}' not found"
        
        return _check_single_plugin_version(
            dep_id, dep_plugin_meta, dependency, logger, plugin_id
        )
    
    else:
        return False, "Dependency must specify at least one of 'id', 'entry', 'custom_event', or 'providers'"


def _parse_plugin_dependencies(
    conf: Dict[str, Any],
    logger: Any,
    plugin_id: str
) -> List[PluginDependency]:
    """
    Parse plugin dependency configuration.
    
    Support three formats:
    1. [plugin].dependencies = [\"plugin_id\"]: simple plugin-ID list.
    2. [[plugin.dependency]]: full format.
    3. [[plugin.dependency]] with conflicts=true: simplified format.
    
    Args:
        conf: TOML configuration dictionary.
        logger: Logger.
        plugin_id: Plugin ID for logs.
    
    Returns:
        Dependency list.
    """
    dependencies: List[PluginDependency] = []

    plugin_table = conf.get("plugin", {})
    if not isinstance(plugin_table, dict):
        plugin_table = {}

    simple_dependencies = plugin_table.get("dependencies")
    if simple_dependencies is not None:
        if not isinstance(simple_dependencies, list):
            logger.warning("Plugin {}: [plugin].dependencies must be a list of plugin id strings, skipping", plugin_id)
        else:
            seen_simple_ids: set[str] = set()
            for dep_id in simple_dependencies:
                if not isinstance(dep_id, str) or not dep_id.strip():
                    logger.warning("Plugin {}: [plugin].dependencies entries must be non-empty strings, skipping", plugin_id)
                    continue
                normalized_dep_id = dep_id.strip()
                if normalized_dep_id in seen_simple_ids:
                    continue
                seen_simple_ids.add(normalized_dep_id)
                if normalized_dep_id == plugin_id:
                    logger.warning("Plugin {}: [plugin].dependencies must not include the plugin itself, skipping", plugin_id)
                    continue
                try:
                    dependencies.append(
                        PluginDependency(
                            id=normalized_dep_id,
                            untested=">=0",
                        )
                    )
                except Exception:
                    try:
                        logger.exception(
                            "Plugin {}: failed to parse simple dependency id '{}', skipping",
                            plugin_id,
                            normalized_dep_id,
                        )
                    except Exception:
                        pass
    
    # TOML [[plugin.dependency]] array-of-table syntax parses as a conf["plugin"]["dependency"] list.
    dep_configs = plugin_table.get("dependency", [])
    
    # Convert nonlists to lists.
    if not isinstance(dep_configs, list):
        if isinstance(dep_configs, dict):
            dep_configs = [dep_configs]
        else:
            return dependencies
    
    for dep_config in dep_configs:
        if not isinstance(dep_config, dict):
            logger.warning("Plugin {}: invalid dependency config (not a dict), skipping", plugin_id)
            continue
        
        # Support four dependency modes: id, entry, custom_event, providers (at least one required).
        dep_id = dep_config.get("id")
        dep_entry = dep_config.get("entry")
        dep_custom_event = dep_config.get("custom_event")
        dep_providers = dep_config.get("providers")
        
        if not dep_id and not dep_entry and not dep_custom_event and not dep_providers:
            logger.warning("Plugin {}: dependency config must have at least one of 'id', 'entry', 'custom_event', or 'providers' field, skipping", plugin_id)
            continue
        
        # Check entry/custom_event mutual exclusion.
        if dep_entry and dep_custom_event:
            logger.warning("Plugin {}: dependency config cannot have both 'entry' and 'custom_event' fields (they are mutually exclusive), skipping", plugin_id)
            continue
        
        # Handle simplified conflicts=true format (id mode only).
        conflicts = dep_config.get("conflicts")
        if conflicts is True:
            if not dep_id:
                logger.warning("Plugin {}: dependency with conflicts=true requires 'id' field, skipping", plugin_id)
                continue
            # Simplified format: only id and conflicts=true.
            try:
                dependencies.append(
                    PluginDependency(
                        id=dep_id,
                        conflicts=True,
                    )
                )
            except Exception:
                try:
                    logger.exception(
                        "Plugin {}: failed to parse dependency config (conflicts=true), skipping: {}",
                        plugin_id,
                        dep_config,
                    )
                except Exception:
                    import sys
                    try:
                        sys.stderr.write(
                            f"[registry] Failed to log exception for plugin {plugin_id!s} (conflicts=true)\n"
                        )
                    except Exception:
                        pass
            continue
        
        # Full format: parse all fields.
        # Dependency configurations require untested except in simplified format.
        untested = dep_config.get("untested")
        if untested is None:
            logger.warning(
                "Plugin {}: dependency missing required 'untested' field, skipping",
                plugin_id
            )
            continue
        
        # Handle conflicts lists.
        conflicts_list = None
        raw_conflicts = dep_config.get("conflicts")
        if isinstance(raw_conflicts, list):
            conflicts_list = [str(c) for c in raw_conflicts if c]
        elif isinstance(raw_conflicts, str) and raw_conflicts.strip():
            conflicts_list = [raw_conflicts.strip()]
        
        # Handle providers lists.
        providers_list = None
        if isinstance(dep_providers, list):
            providers_list = [str(p) for p in dep_providers if p]
        elif isinstance(dep_providers, str) and dep_providers.strip():
            providers_list = [dep_providers.strip()]

        try:
            dependencies.append(
                PluginDependency(
                    id=dep_id,
                    entry=dep_entry,
                    custom_event=dep_custom_event,
                    providers=providers_list,
                    recommended=dep_config.get("recommended"),
                    supported=dep_config.get("supported"),
                    untested=untested,
                    conflicts=conflicts_list,
                )
            )
        except Exception:
            try:
                logger.exception(
                    "Plugin {}: failed to parse dependency config, skipping: {}",
                    plugin_id,
                    dep_config,
                )
            except Exception:
                pass

    return dependencies


def _get_dependency_plugin_ids(dep: PluginDependency, logger: Any) -> List[str]:
    """
    Extract possible plugin IDs from dependency configuration.
    
    Args:
        dep: Dependency configuration.
        logger: Logger.
    
    Returns:
        List of possible dependency plugin IDs.
    """
    if getattr(dep, "conflicts", None) is True:
        return []
    
    out: List[str] = []
    
    if getattr(dep, "id", None):
        out.append(str(dep.id))
    
    providers = getattr(dep, "providers", None)
    if isinstance(providers, list):
        for p in providers:
            if p:
                out.append(str(p))
    
    entry = getattr(dep, "entry", None)
    if isinstance(entry, str):
        if ":" in entry:
            try:
                pid_part, _rest = entry.split(":", 1)
                if pid_part:
                    out.append(pid_part)
            except Exception:
                logger.debug("Failed to parse dependency entry spec '{}'", entry, exc_info=True)
    
    custom_event = getattr(dep, "custom_event", None)
    if isinstance(custom_event, str):
        try:
            parts = custom_event.split(":", 2)
            if len(parts) >= 3 and parts[0]:
                out.append(parts[0])
        except Exception:
            logger.debug("Failed to parse dependency custom_event spec '{}'", custom_event, exc_info=True)
    
    return out


def _topological_sort_plugins(
    plugin_contexts: List["PluginContext"],
    pid_to_context: Dict[str, "PluginContext"],
    logger: Any,
) -> List[str]:
    """
    Phase 2: topologically sort plugins by dependencies.
    
    Args:
        plugin_contexts: Plugin-context list.
        pid_to_context: pid -> context mapping.
        logger: Logger.
    
    Returns:
        Sorted plugin-ID list.
    """
    logger.info("Sorting {} plugins based on dependencies...", len(plugin_contexts))
    
    # Build dependency graph.
    graph: Dict[str, set] = {ctx.pid: set() for ctx in plugin_contexts}

    # Build entry-provider mappings only from the current loading set, independent of global runtime state.
    # Approximate provider relationships with declared entries in conf/pdata.
    entry_providers: Dict[str, set[str]] = {}
    for pctx in plugin_contexts:
        entries = pctx.conf.get("entries") or pctx.pdata.get("entries") or []
        for ent in entries:
            try:
                entry_id = ent.get("id") if isinstance(ent, dict) else str(ent)
            except Exception:
                entry_id = None
            if entry_id:
                entry_providers.setdefault(str(entry_id), set()).add(pctx.pid)
    
    for ctx in plugin_contexts:
        for dep in ctx.dependencies:
            for dep_pid in _get_dependency_plugin_ids(dep, logger):
                if dep_pid in pid_to_context:
                    graph[ctx.pid].add(dep_pid)
                    logger.debug("Dependency edge: {} -> {}", ctx.pid, dep_pid)
            entry_spec = getattr(dep, "entry", None)
            if isinstance(entry_spec, str) and ":" not in entry_spec and entry_spec:
                for provider_pid in entry_providers.get(entry_spec, set()):
                    if provider_pid != ctx.pid and provider_pid in pid_to_context:
                        graph[ctx.pid].add(provider_pid)
                        logger.debug("Dependency edge (entry): {} -> {} via entry '{}'", ctx.pid, provider_pid, entry_spec)
    
    # Kahn's algorithm: build adjacency and indegree tables.
    adj_list: Dict[str, List[str]] = {pid: [] for pid in pid_to_context}
    in_degree: Dict[str, int] = {pid: 0 for pid in pid_to_context}
    
    for ctx in plugin_contexts:
        dependent = ctx.pid
        for dep in ctx.dependencies:
            for dependency in _get_dependency_plugin_ids(dep, logger):
                if dependency in pid_to_context:
                    adj_list[dependency].append(dependent)
                    in_degree[dependent] += 1
            entry_spec = getattr(dep, "entry", None)
            if isinstance(entry_spec, str) and ":" not in entry_spec and entry_spec:
                for provider_pid in entry_providers.get(entry_spec, set()):
                    if provider_pid != dependent and provider_pid in pid_to_context:
                        adj_list[provider_pid].append(dependent)
                        in_degree[dependent] += 1
    
    def _queue_sort_key(pid: str) -> tuple[int, int, int, str]:
        """
        Same-level ordering (preserves topology, affecting load order within one indegree level only):
        1) Enabled first.
        2) Adapters before ordinary plugins.
        3) Adapters by ascending adapter.priority (smaller starts first).
        4) Lexicographic pid fallback.
        """
        ctx = pid_to_context.get(pid)
        if ctx is None:
            return (1, 1, 0, str(pid))

        enabled_rank = 0 if ctx.enabled else 1
        plugin_type = str(ctx.pdata.get("type", "plugin")) if isinstance(ctx.pdata, dict) else "plugin"

        if plugin_type == "adapter":
            adapter_rank = 0
            adapter_conf = ctx.conf.get("adapter", {}) if isinstance(ctx.conf, dict) else {}
            priority_raw = adapter_conf.get("priority", 0) if isinstance(adapter_conf, dict) else 0
            try:
                adapter_priority = int(priority_raw)
            except (TypeError, ValueError):
                adapter_priority = 0
        else:
            adapter_rank = 1
            adapter_priority = 0

        return (enabled_rank, adapter_rank, adapter_priority, str(pid))

    # Initialize queue with zero-indegree nodes.
    queue = [pid for pid in pid_to_context if in_degree[pid] == 0]
    queue.sort(key=_queue_sort_key)
    
    final_order: List[str] = []
    while queue:
        u = queue.pop(0)
        final_order.append(u)
        
        for v in adj_list[u]:
            in_degree[v] -= 1
            if in_degree[v] == 0:
                queue.append(v)
        queue.sort(key=_queue_sort_key)
    
    # Check cyclic dependencies.
    if len(final_order) != len(plugin_contexts):
        loaded_set = set(final_order)
        missing = [ctx.pid for ctx in plugin_contexts if ctx.pid not in loaded_set]
        cycle_info = []
        try:
            for pid in missing:
                deps = [d for d in graph.get(pid, set()) if d in missing]
                if deps:
                    cycle_info.append(f"{pid} -> {deps}")
        except Exception:
            cycle_info = []
        logger.error(
            "Circular dependency detected or failed sort! Missing plugins: {}. Dependency chains: {}. "
            "These plugins will be loaded in undefined order and may fail.",
            missing, cycle_info,
        )
        final_order.extend(missing)
    
    logger.debug("Plugin load order: {}", final_order)
    return final_order
