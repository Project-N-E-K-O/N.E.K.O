# Ported from claudian/src/core/types/settings.ts
# Original author: Claudian contributors
# License: MIT

"""
Settings type definitions — application settings, permission modes, slash commands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ProviderId is just a string alias
ProviderId = str


class PermissionMode(str, Enum):
    """Permission mode for tool execution."""
    YOLO = "yolo"
    PLAN = "plan"
    NORMAL = "normal"


class TabBarPosition(str, Enum):
    """Tab bar position setting."""
    INPUT = "input"
    HEADER = "header"


class EnvironmentScope(str, Enum):
    """Scope for environment variable storage."""
    SHARED = "shared"
    # Provider-specific scopes use "provider:<id>" format


class SlashCommandSource(str, Enum):
    """Source of a slash command."""
    BUILTIN = "builtin"
    USER = "user"
    PLUGIN = "plugin"
    SDK = "sdk"


class ChatViewPlacement(str, Enum):
    """Workspace location for the chat view."""
    RIGHT_SIDEBAR = "right-sidebar"
    LEFT_SIDEBAR = "left-sidebar"
    MAIN_TAB = "main-tab"


# ApprovalDecision is a union type in TypeScript
# In Python we use a string or a dict
ApprovalDecision = str  # "allow" | "allow-always" | "deny" | "cancel"


@dataclass
class ApprovalSelectionDecision:
    """Selection-based approval decision."""
    type: str = "select-option"
    value: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "value": self.value}


@dataclass
class EnvSnippet:
    """Saved environment variable configuration."""
    id: str = ""
    name: str = ""
    description: str = ""
    env_vars: str = ""
    scope: Optional[str] = None
    context_limits: Optional[Dict[str, int]] = None
    model_aliases: Optional[Dict[str, str]] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "envVars": self.env_vars,
        }
        if self.scope:
            out["scope"] = self.scope
        if self.context_limits:
            out["contextLimits"] = self.context_limits
        if self.model_aliases:
            out["modelAliases"] = self.model_aliases
        return out


@dataclass
class SlashCommand:
    """Slash command configuration."""
    id: str = ""
    name: str = ""
    description: Optional[str] = None
    argument_hint: Optional[str] = None
    allowed_tools: Optional[List[str]] = None
    model: Optional[str] = None
    content: str = ""
    source: Optional[str] = None  # SlashCommandSource value
    kind: Optional[str] = None  # "command" | "skill"
    disable_model_invocation: bool = False
    user_invocable: bool = True
    context: Optional[str] = None  # "fork"
    agent: Optional[str] = None
    hooks: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "content": self.content,
        }
        if self.description:
            out["description"] = self.description
        if self.argument_hint:
            out["argumentHint"] = self.argument_hint
        if self.allowed_tools:
            out["allowedTools"] = self.allowed_tools
        if self.model:
            out["model"] = self.model
        if self.source:
            out["source"] = self.source
        if self.kind:
            out["kind"] = self.kind
        if self.disable_model_invocation:
            out["disableModelInvocation"] = True
        if not self.user_invocable:
            out["userInvocable"] = False
        if self.context:
            out["context"] = self.context
        if self.agent:
            out["agent"] = self.agent
        if self.hooks:
            out["hooks"] = self.hooks
        return out


@dataclass
class KeyboardNavigationSettings:
    """Keyboard navigation settings for vim-style scrolling."""
    scroll_up_key: str = "w"
    scroll_down_key: str = "s"
    focus_input_key: str = "i"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scrollUpKey": self.scroll_up_key,
            "scrollDownKey": self.scroll_down_key,
            "focusInputKey": self.focus_input_key,
        }


@dataclass
class InstructionRefineResult:
    """Result from instruction refinement agent query."""
    success: bool = False
    refined_instruction: Optional[str] = None
    clarification: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"success": self.success}
        if self.refined_instruction:
            out["refinedInstruction"] = self.refined_instruction
        if self.clarification:
            out["clarification"] = self.clarification
        if self.error:
            out["error"] = self.error
        return out


@dataclass
class ClaudianSettings:
    """Application settings stored in settings.json.

    Ported from claudian/src/core/types/settings.ts ClaudianSettings.
    """
    # User preferences
    user_name: str = ""

    # Security
    permission_mode: str = "normal"

    # Model & thinking
    model: str = "claude-sonnet-4-20250514"
    thinking_budget: str = "10000"
    effort_level: str = "high"
    service_tier: str = "auto"
    enable_auto_title_generation: bool = True
    title_generation_model: str = ""

    # Content settings
    excluded_tags: List[str] = field(default_factory=list)
    media_folder: str = ""
    system_prompt: str = ""
    persistent_external_context_paths: List[str] = field(default_factory=list)

    # Environment
    shared_environment_variables: str = ""
    env_snippets: List[EnvSnippet] = field(default_factory=list)
    custom_context_limits: Dict[str, int] = field(default_factory=dict)
    custom_model_aliases: Dict[str, str] = field(default_factory=dict)

    # UI settings
    keyboard_navigation: KeyboardNavigationSettings = field(default_factory=KeyboardNavigationSettings)
    require_command_or_control_enter_to_send: bool = False

    # Internationalization
    locale: str = "en"

    # Provider-owned settings
    provider_configs: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Provider selection
    settings_provider: str = "claude"
    saved_provider_model: Dict[str, str] = field(default_factory=dict)
    saved_provider_effort: Dict[str, str] = field(default_factory=dict)
    saved_provider_service_tier: Dict[str, str] = field(default_factory=dict)
    saved_provider_thinking_budget: Dict[str, str] = field(default_factory=dict)
    saved_provider_permission_mode: Dict[str, str] = field(default_factory=dict)

    # State
    last_custom_model: Optional[str] = None

    # UI preferences
    max_tabs: int = 5
    tab_bar_position: str = "input"
    enable_auto_scroll: bool = True
    defer_math_rendering_during_streaming: bool = True
    expand_file_edits_by_default: bool = False
    chat_view_placement: str = "right-sidebar"

    # Provider command visibility
    hidden_provider_commands: Dict[str, List[str]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON storage."""
        return {
            "userName": self.user_name,
            "permissionMode": self.permission_mode,
            "model": self.model,
            "thinkingBudget": self.thinking_budget,
            "effortLevel": self.effort_level,
            "serviceTier": self.service_tier,
            "enableAutoTitleGeneration": self.enable_auto_title_generation,
            "titleGenerationModel": self.title_generation_model,
            "excludedTags": self.excluded_tags,
            "mediaFolder": self.media_folder,
            "systemPrompt": self.system_prompt,
            "persistentExternalContextPaths": self.persistent_external_context_paths,
            "sharedEnvironmentVariables": self.shared_environment_variables,
            "envSnippets": [s.to_dict() for s in self.env_snippets],
            "customContextLimits": self.custom_context_limits,
            "customModelAliases": self.custom_model_aliases,
            "keyboardNavigation": self.keyboard_navigation.to_dict(),
            "requireCommandOrControlEnterToSend": self.require_command_or_control_enter_to_send,
            "locale": self.locale,
            "providerConfigs": self.provider_configs,
            "settingsProvider": self.settings_provider,
            "savedProviderModel": self.saved_provider_model,
            "savedProviderEffort": self.saved_provider_effort,
            "savedProviderServiceTier": self.saved_provider_service_tier,
            "savedProviderThinkingBudget": self.saved_provider_thinking_budget,
            "savedProviderPermissionMode": self.saved_provider_permission_mode,
            "maxTabs": self.max_tabs,
            "tabBarPosition": self.tab_bar_position,
            "enableAutoScroll": self.enable_auto_scroll,
            "deferMathRenderingDuringStreaming": self.defer_math_rendering_during_streaming,
            "expandFileEditsByDefault": self.expand_file_edits_by_default,
            "chatViewPlacement": self.chat_view_placement,
            "hiddenProviderCommands": self.hidden_provider_commands,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ClaudianSettings:
        """Deserialize from dict."""
        settings = cls()
        settings.user_name = data.get("userName", "")
        settings.permission_mode = data.get("permissionMode", "normal")
        settings.model = data.get("model", "claude-sonnet-4-20250514")
        settings.thinking_budget = data.get("thinkingBudget", "10000")
        settings.effort_level = data.get("effortLevel", "high")
        settings.service_tier = data.get("serviceTier", "auto")
        settings.enable_auto_title_generation = data.get("enableAutoTitleGeneration", True)
        settings.title_generation_model = data.get("titleGenerationModel", "")
        settings.excluded_tags = data.get("excludedTags", [])
        settings.media_folder = data.get("mediaFolder", "")
        settings.system_prompt = data.get("systemPrompt", "")
        settings.persistent_external_context_paths = data.get("persistentExternalContextPaths", [])
        settings.shared_environment_variables = data.get("sharedEnvironmentVariables", "")
        settings.env_snippets = [EnvSnippet(**s) for s in data.get("envSnippets", [])]
        settings.custom_context_limits = data.get("customContextLimits", {})
        settings.custom_model_aliases = data.get("customModelAliases", {})
        if "keyboardNavigation" in data:
            kn = data["keyboardNavigation"]
            settings.keyboard_navigation = KeyboardNavigationSettings(
                scroll_up_key=kn.get("scrollUpKey", "w"),
                scroll_down_key=kn.get("scrollDownKey", "s"),
                focus_input_key=kn.get("focusInputKey", "i"),
            )
        settings.require_command_or_control_enter_to_send = data.get("requireCommandOrControlEnterToSend", False)
        settings.locale = data.get("locale", "en")
        settings.provider_configs = data.get("providerConfigs", {})
        settings.settings_provider = data.get("settingsProvider", "claude")
        settings.saved_provider_model = data.get("savedProviderModel", {})
        settings.saved_provider_effort = data.get("savedProviderEffort", {})
        settings.saved_provider_service_tier = data.get("savedProviderServiceTier", {})
        settings.saved_provider_thinking_budget = data.get("savedProviderThinkingBudget", {})
        settings.saved_provider_permission_mode = data.get("savedProviderPermissionMode", {})
        settings.last_custom_model = data.get("lastCustomModel")
        settings.max_tabs = data.get("maxTabs", 5)
        settings.tab_bar_position = data.get("tabBarPosition", "input")
        settings.enable_auto_scroll = data.get("enableAutoScroll", True)
        settings.defer_math_rendering_during_streaming = data.get("deferMathRenderingDuringStreaming", True)
        settings.expand_file_edits_by_default = data.get("expandFileEditsByDefault", False)
        settings.chat_view_placement = data.get("chatViewPlacement", "right-sidebar")
        settings.hidden_provider_commands = data.get("hiddenProviderCommands", {})
        return settings
