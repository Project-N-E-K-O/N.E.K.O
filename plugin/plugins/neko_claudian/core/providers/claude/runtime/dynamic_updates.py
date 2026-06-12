# Ported from claudian/src/providers/claude/runtime/ClaudeDynamicUpdates.ts
# Original author: Claudian contributors
# License: MIT

"""
ClaudeDynamicUpdates — Apply dynamic configuration updates to persistent query.

Handles model, effort level, permission mode, and MCP server updates
without requiring a full query restart.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, Set

logger = logging.getLogger(__name__)


@dataclass
class PersistentQueryConfig:
    """Configuration for a persistent Claude query."""
    model: str = ""
    effort_level: Optional[str] = None
    permission_mode: str = "default"
    sdk_permission_mode: Optional[str] = None
    enable_auto_mode: bool = False
    mcp_servers_key: str = ""
    external_context_paths: list[str] = field(default_factory=list)


@dataclass
class ChatRuntimeQueryOptions:
    """Options for a chat runtime query."""
    model: Optional[str] = None
    mcp_mentions: Optional[Set[str]] = None
    enabled_mcp_servers: Optional[Set[str]] = None
    external_context_paths: Optional[list[str]] = None
    effort_level: Optional[str] = None


@dataclass
class ClosePersistentQueryOptions:
    """Options for closing a persistent query."""
    preserve_handlers: bool = False


@dataclass
class ClaudeEnsureReadyOptions:
    """Options for ensureReady."""
    external_context_paths: Optional[list[str]] = None
    preserve_handlers: bool = False
    force: bool = False


def resolve_effort_level(model: str, settings_effort: Optional[str]) -> Optional[str]:
    """Resolve the effort level for a given model.

    Some models support effort levels (low, medium, high, max).
    """
    if not settings_effort:
        return None

    # Models that support effort levels
    effort_capable_models = {"claude-sonnet-4-20250514", "claude-opus-4-20250514"}

    # Check if model supports effort (by prefix match)
    supports_effort = any(model.startswith(prefix) for prefix in ["claude-sonnet", "claude-opus"])

    if supports_effort:
        return settings_effort
    return None


@dataclass
class ClaudeDynamicUpdateDeps:
    """Dependencies for applyClaudeDynamicUpdates."""
    get_persistent_query: Callable[[], Any]  # Returns Query or None
    get_current_config: Callable[[], Optional[PersistentQueryConfig]]
    mutate_current_config: Callable[[Callable[[PersistentQueryConfig], None]], None]
    get_workspace_path: Callable[[], Optional[str]]
    get_cli_path: Callable[[], Optional[str]]
    get_scoped_settings: Callable[[], Any]  # Returns settings object
    get_permission_mode: Callable[[], str]
    resolve_sdk_permission_mode: Callable[[str], str]
    mcp_manager: Any  # McpServerManager instance
    build_persistent_query_config: Callable[..., PersistentQueryConfig]
    needs_restart: Callable[[PersistentQueryConfig], bool]
    ensure_ready: Callable[[ClaudeEnsureReadyOptions], Any]  # async
    set_current_external_context_paths: Callable[[list[str]], None]
    notify_failure: Callable[[str], None]


async def apply_claude_dynamic_updates(
    deps: ClaudeDynamicUpdateDeps,
    query_options: Optional[ChatRuntimeQueryOptions] = None,
    restart_options: Optional[ClosePersistentQueryOptions] = None,
    allow_restart: bool = True
) -> None:
    """Apply dynamic updates to the persistent query.

    Updates model, effort level, permission mode, and MCP servers
    without restarting the query when possible.

    Ported from ClaudeDynamicUpdates.ts applyClaudeDynamicUpdates.
    """
    persistent_query = deps.get_persistent_query()
    if not persistent_query:
        return

    workspace_path = deps.get_workspace_path()
    if not workspace_path:
        return

    cli_path = deps.get_cli_path()
    if not cli_path:
        return

    settings = deps.get_scoped_settings()
    selected_model = (query_options.model if query_options and query_options.model else getattr(settings, 'model', ''))
    permission_mode = deps.get_permission_mode()

    # Update model if changed
    current_config = deps.get_current_config()
    if current_config and selected_model != current_config.model:
        try:
            await persistent_query.set_model(selected_model)
            deps.mutate_current_config(lambda config: setattr(config, 'model', selected_model))
        except Exception:
            deps.notify_failure("Failed to update model")

    # Update effort level if changed
    settings_effort = getattr(settings, 'effort_level', None)
    effort_level = resolve_effort_level(selected_model, settings_effort)
    current_effort = deps.get_current_config().effort_level if deps.get_current_config() else None

    if effort_level != current_effort:
        try:
            await persistent_query.apply_flag_settings({"effortLevel": effort_level})
            deps.mutate_current_config(lambda config: setattr(config, 'effort_level', effort_level))
        except Exception:
            deps.notify_failure("Failed to update effort level")

    # Update permission mode
    config_before_permission = deps.get_current_config()
    if config_before_permission:
        sdk_mode = deps.resolve_sdk_permission_mode(permission_mode)
        current_sdk_mode = config_before_permission.sdk_permission_mode
        requires_auto_restart = (
            sdk_mode == "auto" and not config_before_permission.enable_auto_mode
        )

        if requires_auto_restart:
            # Auto mode requires restart - handled below
            pass
        elif sdk_mode != current_sdk_mode:
            try:
                await persistent_query.set_permission_mode(sdk_mode)
                deps.mutate_current_config(lambda config: (
                    setattr(config, 'permission_mode', permission_mode),
                    setattr(config, 'sdk_permission_mode', sdk_mode)
                ))
            except Exception:
                deps.notify_failure("Failed to update permission mode")
        else:
            deps.mutate_current_config(lambda config: (
                setattr(config, 'permission_mode', permission_mode),
                setattr(config, 'sdk_permission_mode', sdk_mode)
            ))

    # Update MCP servers
    mcp_mentions = query_options.mcp_mentions if query_options and query_options.mcp_mentions else set()
    ui_enabled = query_options.enabled_mcp_servers if query_options and query_options.enabled_mcp_servers else set()
    combined_mentions = mcp_mentions | ui_enabled
    mcp_servers = deps.mcp_manager.get_active_servers(combined_mentions)
    mcp_servers_key = json.dumps(mcp_servers, sort_keys=True)

    current_cfg = deps.get_current_config()
    if current_cfg and mcp_servers_key != current_cfg.mcp_servers_key:
        server_configs = {name: config for name, config in mcp_servers.items()}
        try:
            await persistent_query.set_mcp_servers(server_configs)
            deps.mutate_current_config(lambda config: setattr(config, 'mcp_servers_key', mcp_servers_key))
        except Exception:
            deps.notify_failure("Failed to update MCP servers")

    # Update external context paths
    new_external_context_paths = (
        query_options.external_context_paths
        if query_options and query_options.external_context_paths
        else []
    )
    deps.set_current_external_context_paths(new_external_context_paths)

    # Check if restart is needed
    if not allow_restart:
        return

    new_config = deps.build_persistent_query_config(
        workspace_path, cli_path, new_external_context_paths
    )
    if not deps.needs_restart(new_config):
        return

    # Perform restart
    restarted = await deps.ensure_ready(ClaudeEnsureReadyOptions(
        external_context_paths=new_external_context_paths,
        preserve_handlers=restart_options.preserve_handlers if restart_options else False,
        force=True
    ))

    if restarted and deps.get_persistent_query():
        # Re-apply updates after restart (without allowing another restart)
        await apply_claude_dynamic_updates(deps, query_options, restart_options, False)
