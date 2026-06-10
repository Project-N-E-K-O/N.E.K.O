"""跨 Provider 的原生音色注册表。

带内置 TTS 音色的 core_api_type（例如 Gemini、StepFun，以及后续可能接入的
OpenAI/Qwen 原生音色）会在这里注册 NativeVoiceProvider。
配置校验、角色 UI、TTS worker 分发和实时语音路由都通过这个注册表查询，
避免到处硬编码 core_api_type 判断。

注册分两层，避免循环导入：
  1. Provider 元数据模块只在 import 时创建并注册 NativeVoiceProvider。
  2. TTS worker 模块等 worker 定义完之后再注册 worker 与鉴权解析函数，
     避免元数据模块提前加载 httpx、soxr 等重依赖。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

if TYPE_CHECKING:
    from utils.config_manager import ConfigManager


VoiceIdExists = Callable[[str], bool]
TTSWorkerResolver = Callable[["ConfigManager"], "tuple[Callable[..., Any], str]"]


@dataclass(frozen=True)
class NativeVoiceProvider:
    """单个 core API 内置 TTS 音色目录的元数据。

    key 对应代码里的 core_api_type / realtime api_type。catalog 的 key 是上游
    API 接收的规范音色名，value 默认作为补充标签；aliases 用于把用户友好的
    输入映射回规范音色名。
    """

    key: str
    catalog: Mapping[str, str]
    aliases: Mapping[str, str]
    default_voice: str
    default_male_voice: str
    catalog_prefix: str
    catalog_value_is_display_name: bool = False
    _voice_lookup: dict[str, str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_voice_lookup",
            {name.casefold(): name for name in self.catalog},
        )

    def normalize(self, voice_id: str | None) -> tuple[str, bool]:
        """返回 (规范音色名, 是否识别)。

        空值按未识别处理，方便调用方区分“用户明确选择了原生音色”和
        “系统使用默认值”。
        """
        normalized = (voice_id or "").strip()
        if not normalized:
            return self.default_voice, False

        exact = self._voice_lookup.get(normalized.casefold())
        if exact:
            return exact, True

        alias = self.aliases.get(normalized.casefold())
        if alias:
            return alias, True

        return self.default_voice, False

    def is_voice(self, voice_id: str | None) -> bool:
        return self.normalize(voice_id)[1]

    def voice_catalog_for_ui(self) -> dict[str, dict[str, str | bool]]:
        """返回角色 UI 需要的音色列表结构。"""
        def format_prefix(voice_name: str, group: str, display_name: str) -> str:
            if self.catalog_value_is_display_name:
                return display_name
            return f"{self.catalog_prefix} {display_name} ({group})"

        def split_catalog_value(voice_name: str, value: str) -> tuple[str, str]:
            if self.catalog_value_is_display_name:
                return "", value or voice_name
            return value, voice_name

        catalog_for_ui: dict[str, dict[str, str | bool]] = {}
        for voice_name, catalog_value in self.catalog.items():
            gender, display_name = split_catalog_value(voice_name, catalog_value)
            catalog_for_ui[voice_name] = {
                "prefix": format_prefix(voice_name, gender, display_name),
                "provider": self.key,
                "provider_label": self.catalog_prefix,
                "gender": gender,
                "display_name": display_name,
                "builtin": True,
            }
        return catalog_for_ui

    def resolve_for_routing(
        self,
        voice_id: str | None,
        voice_id_exists: VoiceIdExists | None = None,
    ) -> tuple[str, bool]:
        """返回 (音色, 是否使用原生音色)。

        输入未命中当前 Provider 目录时，返回 strip 后的原始输入，避免把用户自定义
        音色悄悄替换成默认原生音色。

        如果规范音色名和用户克隆音色冲突，则返回规范音色名但禁用原生路由，
        让调用方按自定义音色处理。
        """
        normalized_voice, recognized = self.normalize(voice_id)
        if not recognized:
            return (voice_id or "").strip(), False

        if voice_id_exists is None:
            return normalized_voice, True

        candidates = {voice_id, normalized_voice}
        has_collision = any(
            voice_id_exists(candidate)
            for candidate in candidates
            if candidate
        )
        return normalized_voice, not has_collision


_PROVIDERS: dict[str, NativeVoiceProvider] = {}
_TTS_WORKER_RESOLVERS: dict[str, TTSWorkerResolver] = {}


def register_provider(provider: NativeVoiceProvider) -> None:
    """Register a provider's voice catalog. Idempotent: re-registering the
    same key replaces the previous entry (useful for tests / hot-reload)."""
    _PROVIDERS[provider.key] = provider


def register_tts_worker_resolver(
    provider_key: str,
    resolver: TTSWorkerResolver,
) -> None:
    """Register the TTS worker callable + api-key resolver for a provider.

    The resolver is invoked with the active ConfigManager and returns
    (worker_callable, api_key) — `get_native_tts_worker` packages this with
    the provider key for the dispatcher.
    """
    _TTS_WORKER_RESOLVERS[provider_key] = resolver


NativeTTSApiKeySource = Literal['core_api_key', 'tts_default_api_key']


def make_native_tts_resolver(
    worker: Callable[..., Any],
    api_key_source: NativeTTSApiKeySource,
    *,
    worker_kwargs: Mapping[str, Any] | None = None,
) -> TTSWorkerResolver:
    """Build a `register_tts_worker_resolver`-compatible resolver from the
    two axes shared by every native-voice provider so far:

    * `worker` — the TTS worker callable to dispatch to.
    * `api_key_source` — where to read the api key from on the active
      `ConfigManager`:
        - ``'core_api_key'`` for providers whose native voices bill against
          the same key as the realtime/LLM endpoint (Gemini, Grok).
        - ``'tts_default_api_key'`` for providers using the TTS slot the
          user configured separately (StepFun, free-mode lanlan TTS).
    * `worker_kwargs` (optional) — bound via `partial` for variants that
      share a worker but flip a mode flag (e.g. `free_mode=True` for the
      free-tier StepFun route).

    Future providers whose api key sourcing falls outside these two
    branches can register a hand-written resolver directly via
    `register_tts_worker_resolver`; adding a third literal here is also
    fine when the new source generalizes.
    """
    def resolver(cm: "ConfigManager") -> "tuple[Callable[..., Any], str]":
        if api_key_source == 'core_api_key':
            api_key = (cm.get_core_config() or {}).get('CORE_API_KEY', '')
        elif api_key_source == 'tts_default_api_key':
            api_key = cm.get_model_api_config('tts_default').get('api_key', '')
        else:
            raise ValueError(f"unknown api_key_source: {api_key_source!r}")
        bound = partial(worker, **dict(worker_kwargs)) if worker_kwargs else worker
        return bound, api_key

    return resolver


def get_provider(key: str | None) -> NativeVoiceProvider | None:
    if not key:
        return None
    return _PROVIDERS.get(key)


def list_providers() -> list[str]:
    return list(_PROVIDERS.keys())


def is_native_voice(
    voice_id: str | None,
    provider_key: str | None = None,
) -> bool:
    """Check catalog membership.

    With `provider_key`, check that provider only. Without, check whether the
    voice belongs to *any* registered provider (used by validators that don't
    know which provider the voice came from).
    """
    if provider_key is not None:
        provider = _PROVIDERS.get(provider_key)
        return bool(provider and provider.is_voice(voice_id))
    return any(provider.is_voice(voice_id) for provider in _PROVIDERS.values())


def normalize_native_voice(
    provider_key: str,
    voice_id: str | None,
) -> tuple[str, bool]:
    """Normalize through a specific provider. Raises KeyError if unknown."""
    return _PROVIDERS[provider_key].normalize(voice_id)


def get_native_voice_catalog_for_ui(
    provider_key: str | None,
) -> dict[str, dict[str, str | bool]] | None:
    provider = get_provider(provider_key)
    if provider is None:
        return None
    return provider.voice_catalog_for_ui()


def is_free_lanlan_app_route(
    core_api_type: str | None,
    realtime_base_url: str | None,
) -> bool:
    """是否为海外免费路由（core_api_type='free' 且 host 落在 lanlan.app 域）。

    海外免费上游是 lanlan.app 的 Gemini 代理，可选音色是 Gemini 全量 + 品牌
    yui，因此这条路由的"有效 native voice provider"不是阶跃系的 'free'，而是
    'free_intl'（见 _effective_native_provider_key）。host 判定集中在这里，
    避免散落到 cross-cutting 文件。
    """
    raw_url = str(realtime_base_url or "").strip()
    parsed = urlparse(raw_url if "://" in raw_url else f"//{raw_url}")
    hostname = (parsed.hostname or "").lower()
    return bool(
        str(core_api_type or "").lower() == "free"
        and (hostname == "lanlan.app" or hostname.endswith(".lanlan.app"))
    )


def _effective_native_provider_key(
    core_api_type: str | None,
    realtime_base_url: str | None,
) -> str | None:
    """把 (core_api_type, host) 归一化成实际查表用的 native voice provider key。

    唯一的 host 依赖分歧：海外免费（free + *.lanlan.app）→ 'free_intl'
    （Gemini 全量 + yui）。其余情况 provider key == core_api_type。集中在
    registry 内做重映射，cross-cutting 调用方只需把 base_url 透传进来，不必
    自己 if host == ...。
    """
    if is_free_lanlan_app_route(core_api_type, realtime_base_url):
        return "free_intl"
    return core_api_type


def resolve_native_voice_for_routing(
    core_api_type: str | None,
    voice_id: str | None,
    voice_id_exists: VoiceIdExists | None = None,
    realtime_base_url: str | None = None,
) -> tuple[str, bool]:
    """Look up provider by core_api_type, then delegate to its resolver.

    Returns (voice_or_input, use_native). When core_api_type isn't a
    registered native-voice provider, returns the stripped input verbatim
    with use_native=False so callers fall through to custom TTS routing.

    传入 realtime_base_url 时，海外免费路由（free + *.lanlan.app）会被重映射
    到 'free_intl'（Gemini 全量 + yui），让 yui / Gemini 音色在该路由下被认成
    native；不传则按 core_api_type 原样查表（向后兼容旧调用与非 free 路由）。
    """
    provider_key = _effective_native_provider_key(core_api_type, realtime_base_url)
    provider = get_provider(provider_key)
    if provider is None:
        return (voice_id or "").strip(), False
    return provider.resolve_for_routing(voice_id, voice_id_exists)


def is_free_preset_voice_id(voice_id: str | None) -> bool:
    """判断 voice_id 是否属于 api_providers.json 的 free_voices 列表。"""
    from utils.api_config_loader import get_free_voices  # 延迟导入避免循环

    voice = (voice_id or "").strip()
    if not voice:
        return False
    return voice in set(get_free_voices().values())


def _read_realtime_api_type(cm: "ConfigManager") -> str | None:
    try:
        return cm.get_model_api_config('realtime').get('api_type')
    except Exception:
        return (cm.get_core_config() or {}).get('CORE_API_TYPE')


def _read_realtime_base_url(cm: "ConfigManager") -> str:
    base_url = ""
    try:
        base_url = str(cm.get_model_api_config('realtime').get('base_url') or '')
    except Exception:
        base_url = ""
    if not base_url:
        try:
            base_url = str((cm.get_core_config() or {}).get('CORE_URL') or '')
        except Exception:
            base_url = ""
    return base_url


def _read_tts_native_provider_for_ui(cm: "ConfigManager") -> str | None:
    try:
        core_config = cm.get_core_config() or {}
    except Exception:
        core_config = {}

    assist_api = str(core_config.get('assistApi') or '').strip().lower()
    if assist_api == 'mimo' and assist_api in _PROVIDERS:
        return assist_api

    tts_provider = str(
        core_config.get('TTS_PROVIDER') or core_config.get('ttsProvider') or ''
    ).strip().lower()
    if tts_provider in _PROVIDERS:
        return tts_provider

    return None


def get_active_realtime_native_provider(cm: "ConfigManager") -> str | None:
    """返回当前 realtime API 注册的 native voice provider key（route-agnostic）。

    仅看 api_type 是否对应已注册 provider，不做 host 重映射 —— 海外免费下
    api_type 仍是 'free'。validate / cleanup 这条链路用 route-agnostic 版叠加
    `is_saveable_native_voice` 的 free_intl 候选，既认海外 Gemini/yui 音色，
    又保留切线路时 Step 原生音色不被误清的宽容度。
    """
    api_type = _read_realtime_api_type(cm)
    return api_type if api_type in _PROVIDERS else None


def is_saveable_native_voice(cm: "ConfigManager", voice_id: str | None) -> bool:
    """voice_id 是否是当前线路下可保存的 native 音色。

    候选 provider = registered api_type（route-agnostic，避免用户切线路时把
    characters.json 里存的 Step 原生音色误清）∪ host 重映射后的有效 provider
    （海外免费叠加 free_intl 的 Gemini 全量 + yui）。命中任一即合法。
    """
    api_type = _read_realtime_api_type(cm)
    base_url = _read_realtime_base_url(cm)
    candidates = {
        api_type,
        _effective_native_provider_key(api_type, base_url),
        _read_tts_native_provider_for_ui(cm),
    }
    return any(is_native_voice(voice_id, key) for key in candidates if key)


def get_active_realtime_native_provider_for_ui(cm: "ConfigManager") -> str | None:
    """返回 /voices 端点和原生音色 preview 应展示的有效 provider key。

    与 route-agnostic 版的差别：这里做 host 重映射 —— 海外免费（free +
    *.lanlan.app）展示 'free_intl'（Gemini 全量 + yui），国内免费展示 'free'
    （阶跃原生）。UI 只暴露该线路实际可用的音色目录。
    """
    tts_provider = _read_tts_native_provider_for_ui(cm)
    if tts_provider:
        return tts_provider

    api_type = _read_realtime_api_type(cm)
    base_url = _read_realtime_base_url(cm)
    key = _effective_native_provider_key(api_type, base_url)
    return key if key in _PROVIDERS else None


_BUILTIN_PROVIDER_MODULES: tuple[str, ...] = (
    "utils.gemini_tts_voices",
    "utils.stepfun_tts_voices",
    "utils.grok_tts_voices",
    "utils.mimo_tts_voices",
)


def ensure_builtin_native_voice_providers_loaded() -> None:
    """Force-import built-in provider adapters so their `register_provider`
    side effects fire before any registry query.

    Called once when this module is imported (see bottom of file). The reason
    auto-bootstrap lives here, not in cross-cutting callers: a callsite that
    runs before any TTS/realtime client has imported a provider module would
    otherwise query an empty registry, and the failure mode (silent
    fall-through to external TTS) is non-obvious.

    To add a new built-in provider: write the adapter module (it must call
    `register_provider(...)` at import time) and append its dotted name to
    `_BUILTIN_PROVIDER_MODULES`. No edits in `config_manager` / `core` /
    `characters_router` / `tts_client` are required for the metadata side.
    """
    import importlib

    for module_name in _BUILTIN_PROVIDER_MODULES:
        importlib.import_module(module_name)


def get_native_tts_worker(
    core_api_type: str | None,
    cm: "ConfigManager",
    voice_id: str | None,
) -> tuple[Callable[..., Any], str, str] | None:
    """Resolve (worker, api_key, provider_key) when the user has selected a
    native voice for an active native-voice provider, else None.

    Used by `tts_client.get_tts_worker` to short-circuit the worker dispatch
    when the user explicitly picked a built-in voice (e.g. Gemini "Puck"):
    we must use the provider's native worker even if no voice clone exists,
    otherwise the fallthrough would route to GPT-SoVITS or local CosyVoice
    with the wrong api key.
    """
    if not core_api_type:
        return None
    # host 重映射：海外免费（free + *.lanlan.app）的 yui/Gemini 音色走 free_intl
    # worker（Gemini 代理），否则 provider key == core_api_type。
    provider_key = _effective_native_provider_key(core_api_type, _read_realtime_base_url(cm))
    provider = _PROVIDERS.get(provider_key)
    if provider is None or not provider.is_voice(voice_id):
        return None
    resolver = _TTS_WORKER_RESOLVERS.get(provider_key)
    if resolver is None:
        return None
    worker, api_key = resolver(cm)
    return worker, api_key, provider_key


# Auto-bootstrap on module import: any consumer of this registry gets a
# populated provider list without each cross-cutting file having to remember a
# `from utils import gemini_tts_voices  # noqa: F401` side-effect import.
# Trades a one-line coupling (registry knows the dotted module names of its
# built-in providers via `_BUILTIN_PROVIDER_MODULES`) for "no caller can forget
# to bootstrap." Adapter modules import this registry to call
# `register_provider`, so by the time we reach this line the registry's public
# API is fully defined and the circular import resolves cleanly.
ensure_builtin_native_voice_providers_loaded()
