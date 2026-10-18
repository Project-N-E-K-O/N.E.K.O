
import math
import os
import warnings
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from utils.config_manager import get_plugins_directory
from utils.social_base import validate_http_url as _validate_http_url


def _get_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes", "on")


def _get_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except Exception:
        return default


def _get_float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except Exception:
        return default


def _validate_market_origin(origin: str) -> str:
    origin = origin.strip()
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid NEKO_MARKET_ORIGINS entry: {origin!r}")
    if parsed.username or parsed.password:
        raise ValueError(f"NEKO_MARKET_ORIGINS entry must not include credentials: {origin!r}")
    if parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment:
        raise ValueError(f"NEKO_MARKET_ORIGINS entries must be origins only: {origin!r}")
    hostname = (parsed.hostname or "").lower()
    http_allowed_hosts = {
        "localhost",
        "127.0.0.1",
        "::1",
        "market.project-neko.cn",
        "marketplace.project-neko.cn",
    }
    if parsed.scheme == "http" and hostname not in http_allowed_hosts:
        raise ValueError(
            "NEKO_MARKET_ORIGINS only allows http for localhost development "
            "and official Market hosts; "
            f"use https for trusted remote origins: {origin!r}"
        )
    return f"{parsed.scheme}://{parsed.netloc}"


# ========== Path configuration ==========

def get_builtin_plugin_config_root() -> Path:
    """Get bundled-plugin root, repository plugin/plugins."""
    return (Path(__file__).parent / "plugins").resolve()


PLUGIN_EXEC_STATE_ROOT_COLLISION = "PLUGIN_EXEC_STATE_ROOT_COLLISION"


class PluginExecStateRootCollisionError(ValueError):
    """Raised when executable packages and persistent state share one root."""

    code = PLUGIN_EXEC_STATE_ROOT_COLLISION

    def __init__(self, *, exec_root: Path, state_root: Path) -> None:
        self.exec_root = exec_root
        self.state_root = state_root
        super().__init__(
            f"{self.code}: plugin execution root {exec_root} must not equal "
            f"plugin state root {state_root}"
        )


def get_plugin_state_root() -> Path:
    """Return the SDK-owned persistent plugin state root.

    This path intentionally retains the historical ``<N.E.K.O user root>/plugins``
    layout. Package install, upgrade, rollback and uninstall code must never use
    it as an executable-package replacement target.
    """

    return Path(get_plugins_directory()).resolve()


def get_user_plugin_exec_root(*, state_root: Path | None = None) -> Path:
    """Return the writable root for user-installed plugin code.

    An explicit legacy ``PLUGIN_CONFIG_ROOT`` override is still honoured as the
    execution root. Without one, code is isolated under
    ``.neko-plugin-installations/plugins`` while persistent state remains under
    :func:`get_plugin_state_root`.
    """

    custom_path = os.getenv("PLUGIN_CONFIG_ROOT")
    if custom_path:
        return Path(custom_path).expanduser().resolve()
    return (
        (state_root if state_root is not None else get_plugin_state_root()).parent
        / ".neko-plugin-installations"
        / "plugins"
    ).resolve()


def get_user_plugin_config_root(*, state_root: Path | None = None) -> Path:
    """Compatibility alias for the user plugin execution root.

    New code should use :func:`get_user_plugin_exec_root`. The old helper name
    is retained while callers migrate away from treating package code as
    configuration/state.
    """

    return get_user_plugin_exec_root(state_root=state_root)


def ensure_plugin_exec_state_roots_separated(
    *,
    exec_root: Path | None = None,
    state_root: Path | None = None,
) -> None:
    """Fail closed before a package write can target persistent state."""

    resolved_exec = (exec_root or get_user_plugin_exec_root()).resolve(strict=False)
    resolved_state = (state_root or get_plugin_state_root()).resolve(strict=False)
    if (
        resolved_exec == resolved_state
        or resolved_exec.is_relative_to(resolved_state)
        or resolved_state.is_relative_to(resolved_exec)
    ):
        raise PluginExecStateRootCollisionError(
            exec_root=resolved_exec,
            state_root=resolved_state,
        )


def get_plugin_config_root() -> Path:
    """Deprecated compatibility helper for the legacy single-root API.

    Returns the legacy built-in plugin root for callers that still expect a
    single ``Path``. New code should use ``PLUGIN_CONFIG_ROOTS`` when it needs
    to search all plugin roots, or ``USER_PLUGIN_CONFIG_ROOT`` when it
    specifically needs the writable user plugin directory.
    """
    warnings.warn(
        "plugin.settings.get_plugin_config_root() is deprecated; use "
        "PLUGIN_CONFIG_ROOTS or USER_PLUGIN_CONFIG_ROOT instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return BUILTIN_PLUGIN_CONFIG_ROOT


def get_plugin_config_roots(*, state_root: Path | None = None) -> tuple[Path, ...]:
    """Return executable plugin roots in effective-source priority order."""
    roots: list[Path] = []
    for root in (get_user_plugin_exec_root(state_root=state_root), get_builtin_plugin_config_root()):
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def get_user_package_profiles_root(*, state_root: Path | None = None) -> Path:
    """Get the user plugin-package profile root.

    - Env: ``PACKAGE_PROFILES_ROOT``
    - When legacy PLUGIN_CONFIG_ROOT is explicitly set, use its sibling for compatibility.
      ``.neko-package-profiles``。
    - Otherwise default to <N.E.K.O user root>/.neko-package-profiles.
    """
    custom_path = os.getenv("PACKAGE_PROFILES_ROOT")
    if custom_path:
        return Path(custom_path).expanduser().resolve()
    legacy_plugin_root = os.getenv("PLUGIN_CONFIG_ROOT")
    if legacy_plugin_root:
        return (
            Path(legacy_plugin_root).expanduser().resolve().parent
            / ".neko-package-profiles"
        ).resolve()
    return ((state_root if state_root is not None else get_plugin_state_root()).parent / ".neko-package-profiles").resolve()


def get_user_plugin_packages_root(*, state_root: Path | None = None) -> Path:
    """Get installation directories for user .neko-plugin/.neko-bundle packages.

    - Env: ``PLUGIN_PACKAGES_ROOT``
    - When legacy PLUGIN_CONFIG_ROOT is explicitly set, use its sibling for compatibility.
      ``.neko-plugin-packages``。
    - Otherwise default to <N.E.K.O user root>/.neko-plugin-packages.
    """
    custom_path = os.getenv("PLUGIN_PACKAGES_ROOT")
    if custom_path:
        return Path(custom_path).expanduser().resolve()
    legacy_plugin_root = os.getenv("PLUGIN_CONFIG_ROOT")
    if legacy_plugin_root:
        return (
            Path(legacy_plugin_root).expanduser().resolve().parent
            / ".neko-plugin-packages"
        ).resolve()
    return ((state_root if state_root is not None else get_plugin_state_root()).parent / ".neko-plugin-packages").resolve()


# Resolve one coherent set of default roots. Public helpers stay fresh outside
# this scope, including after environment or storage-policy changes.
BUILTIN_PLUGIN_CONFIG_ROOT = get_builtin_plugin_config_root()
PLUGIN_STATE_ROOT = get_plugin_state_root()
USER_PLUGIN_EXEC_ROOT = get_user_plugin_exec_root(state_root=PLUGIN_STATE_ROOT)
# Compatibility alias: historically this was both code and state. It now
# deliberately names the execution root only.
USER_PLUGIN_CONFIG_ROOT = USER_PLUGIN_EXEC_ROOT
USER_PACKAGE_PROFILES_ROOT = get_user_package_profiles_root(state_root=PLUGIN_STATE_ROOT)
USER_PLUGIN_PACKAGES_ROOT = get_user_plugin_packages_root(state_root=PLUGIN_STATE_ROOT)
PLUGIN_CONFIG_ROOTS = get_plugin_config_roots(state_root=PLUGIN_STATE_ROOT)


# ========== Queue capacities ==========

# Maximum event-queue capacity.
# Env: NEKO_EVENT_QUEUE_MAX, default=1000
# Main-process internal event dispatch, such as plugin lifecycle events.
EVENT_QUEUE_MAX = _get_int_env("NEKO_EVENT_QUEUE_MAX", 1000)

# Maximum lifecycle-event queue capacity.
# Env: NEKO_LIFECYCLE_QUEUE_MAX, default=1000
# Bounds queued lifecycle events such as plugin startup/shutdown.
LIFECYCLE_QUEUE_MAX = _get_int_env("NEKO_LIFECYCLE_QUEUE_MAX", 1000)

# Maximum shared message-queue capacity.
# Env: NEKO_MESSAGE_QUEUE_MAX, default=1000
# Bounds plugin-to-main message queues, preventing unlimited accumulation.
MESSAGE_QUEUE_MAX = _get_int_env("NEKO_MESSAGE_QUEUE_MAX", 1000)
EXPORT_INLINE_BINARY_MAX_BYTES = _get_int_env("NEKO_EXPORT_INLINE_BINARY_MAX_BYTES", 256 * 1024)

RUN_TOKEN_SECRET = os.getenv("NEKO_RUN_TOKEN_SECRET", "dev-insecure-run-token-secret")
RUN_TOKEN_TTL_SECONDS = _get_int_env("NEKO_RUN_TOKEN_TTL_SECONDS", 3600)
# Maximum run execution seconds; automatically mark timeout after expiry.
# Env: NEKO_RUN_EXECUTION_TIMEOUT, default=30.0 (five minutes).
RUN_EXECUTION_TIMEOUT = _get_float_env("NEKO_RUN_EXECUTION_TIMEOUT", 300.0)
# Maximum terminal runs retained by InMemoryRunStore; evict oldest above the cap.
# Env: NEKO_RUN_STORE_MAX_COMPLETED, default=500
RUN_STORE_MAX_COMPLETED = _get_int_env("NEKO_RUN_STORE_MAX_COMPLETED", 500)

BLOB_STORE_DIR = os.getenv("NEKO_BLOB_STORE_DIR", str((Path(__file__).parent / "store" / "blobs").resolve()))
BLOB_UPLOAD_MAX_BYTES = _get_int_env("NEKO_BLOB_UPLOAD_MAX_BYTES", 200 * 1024 * 1024)
BLOB_UPLOAD_SESSION_TTL_SECONDS = _get_float_env("NEKO_BLOB_UPLOAD_SESSION_TTL_SECONDS", 3600.0)


# ========== Timeouts and polling, seconds ==========

# Maximum plugin-entry execution duration.
# Env: NEKO_PLUGIN_EXECUTION_TIMEOUT, default=30.0
# SDK protection for long-running entries, e.g. HTTP-triggered entry points.
PLUGIN_EXECUTION_TIMEOUT = _get_float_env("NEKO_PLUGIN_EXECUTION_TIMEOUT", 30.0)

# Host-to-plugin trigger response timeout.
# Env: NEKO_PLUGIN_TRIGGER_TIMEOUT, default=10.0
# Bounds PluginProcessHost.trigger waiting, returning errors on timeout.
PLUGIN_TRIGGER_TIMEOUT = _get_float_env("NEKO_PLUGIN_TRIGGER_TIMEOUT", 10.0)

# Host -> plugin process startup ready wait timeout.
# Env: NEKO_PLUGIN_STARTUP_TIMEOUT, default=10.0
PLUGIN_STARTUP_TIMEOUT = _get_float_env("NEKO_PLUGIN_STARTUP_TIMEOUT", 10.0)

# Concurrent autostart limit for plugins without declared dependencies.
# Dependents retain their topological startup order; 1 restores serial starts.
# Bound resource contention and per-plugin startup timeouts on smaller machines.
# Env: NEKO_PLUGIN_AUTOSTART_CONCURRENCY; default=min(8, max(2, cpu // 2)).
PLUGIN_AUTOSTART_CONCURRENCY = _get_int_env(
    "NEKO_PLUGIN_AUTOSTART_CONCURRENCY",
    min(8, max(2, (os.cpu_count() or 4) // 2)),
)

# Legacy opt-in: also rewrite the next-launch auto-start preference on explicit
# user start/stop actions from the plugin manager. Off by default -- a one-off
# manual start/stop no longer changes auto-start; users set it with the
# dedicated auto-start switch (PUT /plugin/{id}/auto-start). A manual stop then
# persists nothing at all, since a stored enabled=false would also keep the
# plugin from starting at the next launch. Internal lifecycle
# operations never persist user intent regardless of this flag.
# Env: NEKO_PLUGIN_SYNC_AUTO_START_ON_TOGGLE, default=False
PLUGIN_SYNC_AUTO_START_ON_TOGGLE = _get_bool_env(
    "NEKO_PLUGIN_SYNC_AUTO_START_ON_TOGGLE",
    False,
)

# Source hot reload watches plugin directories for *.py/plugin.toml changes and automatically reloads
# running plugins, including dev-mode source_dir registrations. Off by default,
# mainly for plugin/host development; changes cause a stop+start cycle per affected plugin.
# Env: NEKO_PLUGIN_HOT_RELOAD, default=False
PLUGIN_HOT_RELOAD = _get_bool_env("NEKO_PLUGIN_HOT_RELOAD", False)

# Hot-reload polling interval, seconds.
# Env: NEKO_PLUGIN_HOT_RELOAD_INTERVAL, default=1.0
PLUGIN_HOT_RELOAD_INTERVAL = _get_float_env("NEKO_PLUGIN_HOT_RELOAD_INTERVAL", 1.0)

# Hot-reload debounce, seconds: trigger only after this quiet period,
# avoiding partially written code during multi-file editor saves.
# Env: NEKO_PLUGIN_HOT_RELOAD_DEBOUNCE, default=1.5
PLUGIN_HOT_RELOAD_DEBOUNCE = _get_float_env("NEKO_PLUGIN_HOT_RELOAD_DEBOUNCE", 1.5)

# Non-environment hard polling floor, also used by hot_reload_service minimum ticks,
# keeping validated minimum intervals consistent with actual sleep floors.
PLUGIN_HOT_RELOAD_MIN_INTERVAL_SECONDS = 0.05

# Per-plugin graceful-shutdown timeout.
# Env: NEKO_PLUGIN_SHUTDOWN_TIMEOUT, default=1.5
# host.shutdown escalates to more forceful termination after this limit.
PLUGIN_SHUTDOWN_TIMEOUT = _get_float_env("NEKO_PLUGIN_SHUTDOWN_TIMEOUT", 1.5)

# Maximum total wait for all plugins during server shutdown.
# Env: PLUGIN_SHUTDOWN_TOTAL_TIMEOUT or NEKO_PLUGIN_SHUTDOWN_TOTAL_TIMEOUT, default=3.
_shutdown_total_timeout_str = os.getenv("PLUGIN_SHUTDOWN_TOTAL_TIMEOUT", os.getenv("NEKO_PLUGIN_SHUTDOWN_TOTAL_TIMEOUT", "3"))
try:
    PLUGIN_SHUTDOWN_TOTAL_TIMEOUT = int(_shutdown_total_timeout_str)
except ValueError:
    PLUGIN_SHUTDOWN_TOTAL_TIMEOUT = 3

# Queue-operation timeout (queue.get).
# Env: NEKO_QUEUE_GET_TIMEOUT, default=1.0
# Recommended for all blocking Queue.get(timeout=...) waits.
QUEUE_GET_TIMEOUT = _get_float_env("NEKO_QUEUE_GET_TIMEOUT", 1.0)

# Plugin SDK synchronous response-poll interval.
# Env: NEKO_BUS_SDK_POLL_INTERVAL_SECONDS, default=0.002
# Interval polling shared response_map for bus.*.get and related APIs;
# - Smaller: less latency jitter, higher throughput, more CPU.
# - Larger: less CPU, more response-latency variation.
BUS_SDK_POLL_INTERVAL_SECONDS = _get_float_env("NEKO_BUS_SDK_POLL_INTERVAL_SECONDS", 0.002)

# Maximum status-consumer wait during shutdown.
# Env: NEKO_STATUS_CONSUMER_SHUTDOWN_TIMEOUT, default=0.5
STATUS_CONSUMER_SHUTDOWN_TIMEOUT = _get_float_env("NEKO_STATUS_CONSUMER_SHUTDOWN_TIMEOUT", 0.5)

# Maximum plugin-process graceful-shutdown wait.
# Env: NEKO_PROCESS_SHUTDOWN_TIMEOUT, default=1.0
PROCESS_SHUTDOWN_TIMEOUT = _get_float_env("NEKO_PROCESS_SHUTDOWN_TIMEOUT", 1.0)

# Plugin-process join timeout after forced terminate.
# Env: NEKO_PROCESS_TERMINATE_TIMEOUT, default=0.5
PROCESS_TERMINATE_TIMEOUT = _get_float_env("NEKO_PROCESS_TERMINATE_TIMEOUT", 0.5)


# ========== Plugin marketplace ==========

# Auth platform URL; desktop OAuth uses Auth/Hydra only, no longer Market.
# Env: NEKO_AUTH_URL, default="https://auth.project-neko.cn"
NEKO_AUTH_URL = _validate_http_url(
    os.getenv("NEKO_AUTH_URL", "https://auth.project-neko.cn"),
    name="NEKO_AUTH_URL",
    allow_empty=True,
)

# Desktop OAuth public client ID; must have no client secret.
# Env: NEKO_AUTH_CLIENT_ID, default="neko-desktop"
# NOTE: This is the Plugin Market client. The community / Servers Desktop PKCE
# flow uses a different client id (env NEKO_SERVERS_DESKTOP_CLIENT_ID) and owns
# it in main_routers/community_oauth.py — never reuse neko-desktop there.
NEKO_AUTH_CLIENT_ID = os.getenv("NEKO_AUTH_CLIENT_ID", "neko-desktop").strip() or "neko-desktop"

# Marketplace API URL; configuration enables the marketplace entry in plugin management.
# Env: NEKO_MARKET_API_URL, default="https://market.project-neko.cn"
# Legacy env NEKO_MARKET_URL remains supported; environment overrides serve local development.
MARKET_API_URL = _validate_http_url(
    os.getenv(
        "NEKO_MARKET_API_URL",
        os.getenv("NEKO_MARKET_URL", "https://market.project-neko.cn"),
    ),
    name="NEKO_MARKET_API_URL",
    allow_empty=True,
)

# Marketplace web URL for detail pages; API calls still use MARKET_API_URL + /api/v1.
# Local development defaults to frontend Vite port 5173; unset production configuration
# defaults to MARKET_API_URL's origin.
# Env: NEKO_MARKET_WEB_URL
_market_web_url_env = os.getenv("NEKO_MARKET_WEB_URL")
if _market_web_url_env is not None:
    MARKET_WEB_URL = _validate_http_url(
        _market_web_url_env,
        name="NEKO_MARKET_WEB_URL",
        allow_empty=True,
    )
elif MARKET_API_URL.rstrip("/") in {"http://localhost:8000", "http://127.0.0.1:8000"}:
    MARKET_WEB_URL = "http://localhost:5173"
else:
    MARKET_WEB_URL = MARKET_API_URL

# Allowed Market CORS origins, comma-separated.
# Allow Market frontends to call local /market/* endpoints cross-origin.
# Env: NEKO_MARKET_ORIGINS, default=Project N.E.K.O Market public origins
# This affects CORS security; configure trusted marketplace frontend domains only.
_default_market_origins = (
    "https://market.project-neko.cn,"
    "https://marketplace.project-neko.cn"
)
MARKET_ORIGINS = [
    _validate_market_origin(o)
    for o in os.getenv("NEKO_MARKET_ORIGINS", _default_market_origins).split(",")
    if o.strip()
]


# ========== Thread pool ==========

# Maximum communication-resource-manager worker threads.
# Each plugin's communication manager needs at least three:
# 1. _consume_results continuously reads results.
# 2. _consume_messages continuously reads messages.
# 3. _send_command_and_wait sends plugin commands.
# Formula: max(8,(CPU cores or 1)+4), providing enough threads for multiple plugins.
COMMUNICATION_THREAD_POOL_MAX_WORKERS = max(32, (os.cpu_count() or 1) + 8)


# ========== Default message-fetch limits ==========

# Default max_count for message retrieval.
# Env: NEKO_MESSAGE_QUEUE_DEFAULT_MAX_COUNT, default=100
# Used by bus.messages.get/events.get/lifecycle.get when max_count is unspecified.
MESSAGE_QUEUE_DEFAULT_MAX_COUNT = _get_int_env("NEKO_MESSAGE_QUEUE_DEFAULT_MAX_COUNT", 100)

# Default max_count for status messages.
# Env: NEKO_STATUS_MESSAGE_DEFAULT_MAX_COUNT, default=100
STATUS_MESSAGE_DEFAULT_MAX_COUNT = _get_int_env("NEKO_STATUS_MESSAGE_DEFAULT_MAX_COUNT", 100)


# ========== SDK metadata attributes ==========

# Plugin metadata attribute marking plugin classes.
NEKO_PLUGIN_META_ATTR = "__neko_plugin_meta__"

# Plugin tag marking plugin classes.
NEKO_PLUGIN_TAG = "__neko_plugin__"


# ========== Other runtime configuration ==========

# Status-consumer sleep interval, seconds.
# Fixed CPU/latency tradeoff; usually needs no adjustment.
STATUS_CONSUMER_SLEEP_INTERVAL = 0.1

# Message-consumer sleep interval, seconds.
MESSAGE_CONSUMER_SLEEP_INTERVAL = 0.1

# Whether to print [MESSAGE FORWARD] forwarding logs.
# Env: NEKO_PLUGIN_LOG_MESSAGE_FORWARD, default=True
PLUGIN_LOG_MESSAGE_FORWARD = _get_bool_env("NEKO_PLUGIN_LOG_MESSAGE_FORWARD", True)

# Whether to print synchronous SDK-call warnings ("Sync call '...' may block ...").
# Env: NEKO_PLUGIN_LOG_SYNC_CALL_WARNINGS, default=True
PLUGIN_LOG_SYNC_CALL_WARNINGS = _get_bool_env("NEKO_PLUGIN_LOG_SYNC_CALL_WARNINGS", True)

# Whether to print subscription information on changes.
# Env: NEKO_PLUGIN_LOG_BUS_SUBSCRIPTIONS, default=True
PLUGIN_LOG_BUS_SUBSCRIPTIONS = _get_bool_env("NEKO_PLUGIN_LOG_BUS_SUBSCRIPTIONS", True)

# Whether to print subscription requests.
# Env: NEKO_PLUGIN_LOG_BUS_SUBSCRIBE_REQUESTS, default=True
PLUGIN_LOG_BUS_SUBSCRIBE_REQUESTS = _get_bool_env("NEKO_PLUGIN_LOG_BUS_SUBSCRIBE_REQUESTS", True)

# Whether to warn on SDK-call timeouts.
# Env: NEKO_PLUGIN_LOG_BUS_SDK_TIMEOUT_WARNINGS, default=True
PLUGIN_LOG_BUS_SDK_TIMEOUT_WARNINGS = _get_bool_env("NEKO_PLUGIN_LOG_BUS_SDK_TIMEOUT_WARNINGS", True)

# Whether ctx.status.update prints logs.
# Env: NEKO_PLUGIN_LOG_CTX_STATUS_UPDATE, default=True
PLUGIN_LOG_CTX_STATUS_UPDATE = _get_bool_env("NEKO_PLUGIN_LOG_CTX_STATUS_UPDATE", True)

# Whether ctx.push_message prints logs.
# Env: NEKO_PLUGIN_LOG_CTX_MESSAGE_PUSH, default=True
PLUGIN_LOG_CTX_MESSAGE_PUSH = _get_bool_env("NEKO_PLUGIN_LOG_CTX_MESSAGE_PUSH", True)

# Whether to print verbose server debug logs.
# Env: NEKO_PLUGIN_LOG_SERVER_DEBUG, default=False
PLUGIN_LOG_SERVER_DEBUG = _get_bool_env("NEKO_PLUGIN_LOG_SERVER_DEBUG", False)

# ========== Message Schema validation ==========

# Whether message-bus payload field/type validation is strict.
# Env: NEKO_MESSAGE_SCHEMA_STRICT, default=True
MESSAGE_SCHEMA_STRICT = _get_bool_env("NEKO_MESSAGE_SCHEMA_STRICT", True)

# Whether plugins may mark payloads unsafe to bypass strict validation for high-performance use.
# Env: NEKO_MESSAGE_SCHEMA_ALLOW_UNSAFE, default=True
MESSAGE_SCHEMA_ALLOW_UNSAFE = _get_bool_env("NEKO_MESSAGE_SCHEMA_ALLOW_UNSAFE", True)

# Whether unknown fields outside the schema trigger warnings.
# Env: NEKO_MESSAGE_SCHEMA_WARN_UNKNOWN_FIELDS, default=True
MESSAGE_SCHEMA_WARN_UNKNOWN_FIELDS = _get_bool_env("NEKO_MESSAGE_SCHEMA_WARN_UNKNOWN_FIELDS", True)

# Whether ZeroMQ IPC between plugin/main processes is enabled.
# Env: NEKO_PLUGIN_ZMQ_IPC_ENABLED, default=True
PLUGIN_ZMQ_IPC_ENABLED = _get_bool_env("NEKO_PLUGIN_ZMQ_IPC_ENABLED", True)

# ZeroMQ IPC endpoint address.
# Env: NEKO_PLUGIN_ZMQ_IPC_ENDPOINT, default="tcp://127.0.0.1:38765"
PLUGIN_ZMQ_IPC_ENDPOINT = os.getenv("NEKO_PLUGIN_ZMQ_IPC_ENDPOINT", "tcp://127.0.0.1:38765")

# [MESSAGE FORWARD] deduplication window, seconds.
# Env: NEKO_PLUGIN_MESSAGE_FORWARD_LOG_DEDUP_WINDOW_SECONDS, default=1.0
PLUGIN_MESSAGE_FORWARD_LOG_DEDUP_WINDOW_SECONDS = _get_float_env(
    "NEKO_PLUGIN_MESSAGE_FORWARD_LOG_DEDUP_WINDOW_SECONDS", 1.0
)

# Bus-change log deduplication window, seconds.
# Env: NEKO_PLUGIN_BUS_CHANGE_LOG_DEDUP_WINDOW_SECONDS, default=1.0
PLUGIN_BUS_CHANGE_LOG_DEDUP_WINDOW_SECONDS = _get_float_env(
    "NEKO_PLUGIN_BUS_CHANGE_LOG_DEDUP_WINDOW_SECONDS", 1.0
)

# ========== Message Plane (High-Frequency Bus) ==========

# Message-plane ZeroMQ RPC endpoint for high-frequency bus get/reload/filter requests/responses.
# TCP loopback 127.0.0.1 can be faster than IPC on some systems.
# Env: NEKO_MESSAGE_PLANE_ZMQ_RPC_ENDPOINT, default="tcp://127.0.0.1:38865"
MESSAGE_PLANE_ZMQ_RPC_ENDPOINT = os.getenv(
    "NEKO_MESSAGE_PLANE_ZMQ_RPC_ENDPOINT",
    os.getenv("NEKO_MESSAGE_PLANE_RPC", "tcp://127.0.0.1:38865"),
)


def resolve_message_plane_rpc_endpoint() -> str:
    """The RPC endpoint the plane is actually on, read at call time.

    The constant above is frozen when this module is imported, which happens
    before ``build_message_plane_runner()`` runs. When the configured port is
    occupied that runner moves the plane to a fallback and publishes the new
    address by writing ``NEKO_MESSAGE_PLANE_ZMQ_RPC_ENDPOINT`` back into the
    environment -- so anything holding the constant is pointed at the occupied
    port and every bus read fails.

    Affects the host process as much as a forked plugin child: the constant was
    already computed there too. A spawned child re-imports this module and picks
    the new value up on its own, which is why the symptom is POSIX-shaped.

    This mirrors what ``ProactiveBridge._run`` already does for the PUB
    endpoint (``os.getenv(..., str(MESSAGE_PLANE_ZMQ_PUB_ENDPOINT))``); the RPC
    consumers were the ones left reading the frozen value.
    """
    return os.getenv(
        "NEKO_MESSAGE_PLANE_ZMQ_RPC_ENDPOINT",
        os.getenv("NEKO_MESSAGE_PLANE_RPC", str(MESSAGE_PLANE_ZMQ_RPC_ENDPOINT)),
    )

# Message-plane ZeroMQ PUB endpoint for high-frequency watcher/export-progress subscriptions and pushes.
# TCP loopback 127.0.0.1 can be faster than IPC on some systems.
# Env: NEKO_MESSAGE_PLANE_ZMQ_PUB_ENDPOINT, default="tcp://127.0.0.1:38866"
MESSAGE_PLANE_ZMQ_PUB_ENDPOINT = os.getenv(
    "NEKO_MESSAGE_PLANE_ZMQ_PUB_ENDPOINT",
    os.getenv("NEKO_MESSAGE_PLANE_PUB", "tcp://127.0.0.1:38866"),
)

# Message plane always runs as an embedded thread.
# Retain endpoint settings without supporting external standalone-process mode.

MESSAGE_PLANE_VALIDATE_MODE = os.getenv("NEKO_MESSAGE_PLANE_VALIDATE_MODE", "strict").lower()
if MESSAGE_PLANE_VALIDATE_MODE not in ("off", "warn", "strict"):
    MESSAGE_PLANE_VALIDATE_MODE = "strict"

MESSAGE_PLANE_TOPIC_MAX = _get_int_env("NEKO_MESSAGE_PLANE_TOPIC_MAX", 2000)
MESSAGE_PLANE_TOPIC_NAME_MAX_LEN = _get_int_env("NEKO_MESSAGE_PLANE_TOPIC_NAME_MAX_LEN", 128)
# Shared message-plane per-payload byte limit for plugin push_message and host ingest
# payload_too_large checks, preventing divergent limits.
# 512KiB rather than 256KiB: inline base64 images consume 4/3 of raw bytes, so this budget
# applies after encoding. Supporting the documented 256KiB raw images requires room
# for 341KiB encoded plus text/envelope headroom. 512KiB corresponds to about
# 384KiB raw. Legacy binary_data raw-byte duplicates were removed concurrently; before that,
# images cost ~2.34x, limiting a 256KiB budget to ~110KiB images.
# Env: NEKO_MESSAGE_PLANE_PAYLOAD_MAX_BYTES, default=512*1024
MESSAGE_PLANE_PAYLOAD_MAX_BYTES = _get_int_env("NEKO_MESSAGE_PLANE_PAYLOAD_MAX_BYTES", 512 * 1024)
MESSAGE_PLANE_STORE_MAXLEN = _get_int_env("NEKO_MESSAGE_PLANE_STORE_MAXLEN", 20000)

# Independent frames-store capacity. Never reuse MESSAGE_PLANE_STORE_MAXLEN, the
# per-topic deque length: 20000 ~150KB screenshots retain ~2GB and roughly eight hours
# of user screen history in agent_server. Frames mean only the few latest provider-received
# images, not logs, so retain single digits.
# Minimum two: one lets the next frame replace a just-fetched frame, preventing even one retry.
# Maximum eight: more only prolongs screen-content retention without extra value to pull callers.
# Env: NEKO_MESSAGE_PLANE_FRAMES_STORE_MAXLEN, default=4
MESSAGE_PLANE_FRAMES_STORE_MAXLEN = max(
    2, min(8, _get_int_env("NEKO_MESSAGE_PLANE_FRAMES_STORE_MAXLEN", 4))
)

# A frame enters the plane-bridge queue only below this current-depth watermark.
# The maxsize=4096 bridge queue shares events/lifecycle/runs. Ordinary records are hundreds of bytes,
# frames hundreds of KB. Without a watermark, stalled bridges fill with frames,
# displacing essential small records and growing memory to hundreds of MB. Frames are lossy anyway:
# dropping when behind honors the contract better than delivering stale images minutes later.
# Env: NEKO_MESSAGE_PLANE_FRAMES_BRIDGE_MAX_PENDING, default=64
MESSAGE_PLANE_FRAMES_BRIDGE_MAX_PENDING = max(
    1, _get_int_env("NEKO_MESSAGE_PLANE_FRAMES_BRIDGE_MAX_PENDING", 64)
)
MESSAGE_PLANE_GET_RECENT_MAX_LIMIT = _get_int_env("NEKO_MESSAGE_PLANE_GET_RECENT_MAX_LIMIT", 1000)

MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT = os.getenv(
    "NEKO_MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT",
    os.getenv("NEKO_MESSAGE_PLANE_INGEST", "tcp://127.0.0.1:38867"),
)
MESSAGE_PLANE_INGEST_RCVHWM = _get_int_env("NEKO_MESSAGE_PLANE_INGEST_RCVHWM", 10000)

MESSAGE_PLANE_INGEST_STATS_LOG_ENABLED = _get_bool_env("NEKO_MESSAGE_PLANE_INGEST_STATS_LOG_ENABLED", True)
MESSAGE_PLANE_INGEST_STATS_LOG_INFO = _get_bool_env("NEKO_MESSAGE_PLANE_INGEST_STATS_LOG_INFO", True)
MESSAGE_PLANE_INGEST_STATS_LOG_VERBOSE = _get_bool_env("NEKO_MESSAGE_PLANE_INGEST_STATS_LOG_VERBOSE", False)
MESSAGE_PLANE_INGEST_STATS_INTERVAL_SECONDS = _get_float_env("NEKO_MESSAGE_PLANE_INGEST_STATS_INTERVAL_SECONDS", 1.0)
MESSAGE_PLANE_INGEST_BACKPRESSURE_SLEEP_SECONDS = _get_float_env("NEKO_MESSAGE_PLANE_INGEST_BACKPRESSURE_SLEEP_SECONDS", 0.0)

# Plugin -> message_plane ingest PUSH send timeout (ms). Prevents plugin thread from blocking indefinitely
# under heavy backpressure.
# Env: NEKO_MESSAGE_PLANE_INGEST_SNDTIMEO_MS, default=1000
MESSAGE_PLANE_INGEST_SNDTIMEO_MS = _get_int_env("NEKO_MESSAGE_PLANE_INGEST_SNDTIMEO_MS", 1000)

MESSAGE_PLANE_PUB_ENABLED = _get_bool_env("NEKO_MESSAGE_PLANE_PUB_ENABLED", True)
MESSAGE_PLANE_VALIDATE_PAYLOAD_BYTES = _get_bool_env("NEKO_MESSAGE_PLANE_VALIDATE_PAYLOAD_BYTES", True)

# Clamp >=1 at load too. Python queue.Queue(maxsize=0) is unbounded, not
# reject-all; zero/negative configuration otherwise gives batchers unbounded queues,
# while self._max_queue>0 skips rejection watermarks, disabling both guards
# and leaving backlog limited only by memory.
MESSAGE_PLANE_PUSH_BATCHER_MAX_QUEUE = max(
    1, _get_int_env("NEKO_MESSAGE_PLANE_PUSH_BATCHER_MAX_QUEUE", 100000)
)
MESSAGE_PLANE_PUSH_BATCHER_REJECT_RATIO = _get_float_env("NEKO_MESSAGE_PLANE_PUSH_BATCHER_REJECT_RATIO", 0.9)
MESSAGE_PLANE_PUSH_BATCHER_ENQUEUE_TIMEOUT_SECONDS = _get_float_env(
    "NEKO_MESSAGE_PLANE_PUSH_BATCHER_ENQUEUE_TIMEOUT_SECONDS",
    0.01,
)

MESSAGE_PLANE_BRIDGE_ENABLED = _get_bool_env("NEKO_MESSAGE_PLANE_BRIDGE_ENABLED", True)

# PUSH batch size, entry count.
# Env: NEKO_PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE, default=256
# Clamp >=1 centrally at load rather than independently in consumers: the batcher constructor
# already clamps (_AuthenticatedMessageBatcher.__init__), while host batch checks used raw values.
# Zero/negative values therefore made legal one-entry child batches fail host len(items)>0
# checks, rejecting everything and silently stopping proactive chat. Normalize here so both sides
# read exactly the same value.
PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE = max(
    1, _get_int_env("NEKO_PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE", 256)
)

# Maximum control-uplink frame bytes.
#
# Previously borrowed the message-uplink limit payload_max*batch_max, but batching
# multipliers do not apply to never-batched controls, granting 128MiB per frame
# with a 5000-frame HWM, effectively no useful bound.
#
# Do not guess too low either: downstream controls have no size contract, and low limits silently
# lose real tool results. libzmq drops frames and disconnects peers in its receive engine without recv()
# errors, so hosts see no bytes. Cover all three legal CH_RES components:
#
# Images: _MAX_TOOL_IMAGES * _MAX_TOOL_IMAGE_B64_BYTES = 2 * 2MiB.
# Output: tool output has no limit; allocate the existing message-plane single-payload
# budget MESSAGE_PLANE_PAYLOAD_MAX_BYTES, default 512KiB,
# the repository's established structured-payload size bound rather than an invented number.
# Envelope: msgpack structure + token + vision_prompt, sharing message uplink's 64KiB allowance.
#
# An image-only derivation failed in testing with two maximum images plus merely 60KB output:
# output shares the frame. Omitting it replaces 128MiB frames with silent loss of valid results,
# worse than before. Tests now measure this combination rather than images alone.
#
# Tests enforce relationships (test_zmq_transport_security), so changing image or plane-payload
# limits without updating this fails rather than silently dropping results.
# Env: NEKO_PLUGIN_ZMQ_CONTROL_UPLINK_MAX_BYTES
PLUGIN_ZMQ_CONTROL_UPLINK_MAX_BYTES = _get_int_env(
    "NEKO_PLUGIN_ZMQ_CONTROL_UPLINK_MAX_BYTES",
    4 * 1024 * 1024 + MESSAGE_PLANE_PAYLOAD_MAX_BYTES + 64 * 1024,
)

# PUSH flush milliseconds, balancing small frequent batches versus large infrequent batches.
# Env: NEKO_PLUGIN_ZMQ_MESSAGE_PUSH_FLUSH_INTERVAL_MS, default=5
PLUGIN_ZMQ_MESSAGE_PUSH_FLUSH_INTERVAL_MS = _get_int_env("NEKO_PLUGIN_ZMQ_MESSAGE_PUSH_FLUSH_INTERVAL_MS", 5)

# Global synchronous-call handler policy, "warn" / "reject".
# Env: NEKO_PLUGIN_SYNC_CALL_POLICY, default="warn"
_sync_policy = os.getenv("NEKO_PLUGIN_SYNC_CALL_POLICY", "warn").lower()
if _sync_policy not in ("warn", "reject"):
    _sync_policy = "warn"
SYNC_CALL_IN_HANDLER_POLICY = _sync_policy

# ========== Plugin-state persistence ==========

# Shared plugin-state persistence backend for freeze and autosave.
# - off: disable persistence, default.
# - memory: retain in memory, lost on main-process restart.
# - file: persistent file storage.
# Env: NEKO_PLUGIN_STATE_BACKEND, default="off"
PLUGIN_STATE_BACKEND_DEFAULT = os.getenv("NEKO_PLUGIN_STATE_BACKEND", "off").strip().lower()
if PLUGIN_STATE_BACKEND_DEFAULT not in ("off", "memory", "file"):
    PLUGIN_STATE_BACKEND_DEFAULT = "off"

# ========== Store configuration ==========
# Default Store backend: sqlite/memory/off; default off requires explicit developer opt-in.
PLUGIN_STORE_BACKEND_DEFAULT = os.getenv("NEKO_PLUGIN_STORE_BACKEND", "off")


# ========== Plugin-loading behavior ==========

# Whether plugin dependencies are checked.
# Env: PLUGIN_ENABLE_DEPENDENCY_CHECK, default=False
# - False skips checks and permits unmet dependencies, recommended only for development/debugging.
# - True strictly checks dependencies and rejects unmet requirements.
PLUGIN_ENABLE_DEPENDENCY_CHECK = os.getenv("PLUGIN_ENABLE_DEPENDENCY_CHECK", "false").lower() in ("true", "1", "yes")

# Whether plugin-ID collisions are checked.
# Env: PLUGIN_ENABLE_ID_CONFLICT_CHECK, default=False
# - False permits duplicate declared IDs, potentially unpredictable and recommended only for debugging.
# - True enables strict collision detection and renaming.
PLUGIN_ENABLE_ID_CONFLICT_CHECK = os.getenv("PLUGIN_ENABLE_ID_CONFLICT_CHECK", "false").lower() in ("true", "1", "yes")


# ========== Configuration validation ==========

def validate_config() -> None:
    """
    Validate configuration.
    
    Hard validation raises at module import, detecting invalid configuration before startup.
    If configuration becomes runtime-adjustable, update validation timing and policy together.
    
    Raises:
        ValueError: Invalid configuration.
    """
    if EVENT_QUEUE_MAX <= 0:
        raise ValueError("EVENT_QUEUE_MAX must be positive")
    if EVENT_QUEUE_MAX > 1000000:
        raise ValueError("EVENT_QUEUE_MAX is unreasonably large (max: 1000000)")

    if LIFECYCLE_QUEUE_MAX <= 0:
        raise ValueError("LIFECYCLE_QUEUE_MAX must be positive")
    if LIFECYCLE_QUEUE_MAX > 1000000:
        raise ValueError("LIFECYCLE_QUEUE_MAX is unreasonably large (max: 1000000)")
    
    if MESSAGE_QUEUE_MAX <= 0:
        raise ValueError("MESSAGE_QUEUE_MAX must be positive")
    if MESSAGE_QUEUE_MAX > 1000000:
        raise ValueError("MESSAGE_QUEUE_MAX is unreasonably large (max: 1000000)")
    
    if PLUGIN_EXECUTION_TIMEOUT <= 0:
        raise ValueError("PLUGIN_EXECUTION_TIMEOUT must be positive")
    if PLUGIN_EXECUTION_TIMEOUT > 3600:
        raise ValueError("PLUGIN_EXECUTION_TIMEOUT is unreasonably large (max: 3600s)")
    
    if PLUGIN_TRIGGER_TIMEOUT <= 0:
        raise ValueError("PLUGIN_TRIGGER_TIMEOUT must be positive")
    if PLUGIN_TRIGGER_TIMEOUT > 3600:
        raise ValueError("PLUGIN_TRIGGER_TIMEOUT is unreasonably large (max: 3600s)")

    if not math.isfinite(PLUGIN_STARTUP_TIMEOUT) or PLUGIN_STARTUP_TIMEOUT <= 0:
        raise ValueError("PLUGIN_STARTUP_TIMEOUT must be positive")
    if PLUGIN_STARTUP_TIMEOUT > 300:
        raise ValueError("PLUGIN_STARTUP_TIMEOUT is unreasonably large (max: 300s)")

    if PLUGIN_AUTOSTART_CONCURRENCY < 1:
        raise ValueError("PLUGIN_AUTOSTART_CONCURRENCY must be >= 1 (1 = serial)")
    if PLUGIN_AUTOSTART_CONCURRENCY > 64:
        raise ValueError("PLUGIN_AUTOSTART_CONCURRENCY is unreasonably large (max: 64)")

    if PLUGIN_SHUTDOWN_TIMEOUT <= 0:
        raise ValueError("PLUGIN_SHUTDOWN_TIMEOUT must be positive")
    if PLUGIN_SHUTDOWN_TIMEOUT > 300:
        raise ValueError("PLUGIN_SHUTDOWN_TIMEOUT is unreasonably large (max: 300s)")
    
    if PLUGIN_SHUTDOWN_TOTAL_TIMEOUT <= 0:
        raise ValueError("PLUGIN_SHUTDOWN_TOTAL_TIMEOUT must be positive")
    if PLUGIN_SHUTDOWN_TOTAL_TIMEOUT > 300:
        raise ValueError("PLUGIN_SHUTDOWN_TOTAL_TIMEOUT is unreasonably large (max: 300s)")

    if not math.isfinite(PLUGIN_HOT_RELOAD_INTERVAL) or not PLUGIN_HOT_RELOAD_MIN_INTERVAL_SECONDS <= PLUGIN_HOT_RELOAD_INTERVAL <= 60:
        raise ValueError(
            f"PLUGIN_HOT_RELOAD_INTERVAL must be in "
            f"[{PLUGIN_HOT_RELOAD_MIN_INTERVAL_SECONDS}, 60] seconds"
        )
    if not math.isfinite(PLUGIN_HOT_RELOAD_DEBOUNCE) or not 0.0 <= PLUGIN_HOT_RELOAD_DEBOUNCE <= 60:
        raise ValueError("PLUGIN_HOT_RELOAD_DEBOUNCE must be in [0, 60] seconds")

    if QUEUE_GET_TIMEOUT <= 0:
        raise ValueError("QUEUE_GET_TIMEOUT must be positive")
    if QUEUE_GET_TIMEOUT > 60:
        raise ValueError("QUEUE_GET_TIMEOUT is unreasonably large (max: 60s)")

    if BUS_SDK_POLL_INTERVAL_SECONDS < 0:
        raise ValueError("BUS_SDK_POLL_INTERVAL_SECONDS must be >= 0")
    if BUS_SDK_POLL_INTERVAL_SECONDS > 1:
        raise ValueError("BUS_SDK_POLL_INTERVAL_SECONDS is unreasonably large (max: 1s)")

    if PLUGIN_MESSAGE_FORWARD_LOG_DEDUP_WINDOW_SECONDS < 0:
        raise ValueError("PLUGIN_MESSAGE_FORWARD_LOG_DEDUP_WINDOW_SECONDS must be >= 0")
    if PLUGIN_MESSAGE_FORWARD_LOG_DEDUP_WINDOW_SECONDS > 3600:
        raise ValueError("PLUGIN_MESSAGE_FORWARD_LOG_DEDUP_WINDOW_SECONDS is unreasonably large (max: 3600s)")

    if PLUGIN_BUS_CHANGE_LOG_DEDUP_WINDOW_SECONDS < 0:
        raise ValueError("PLUGIN_BUS_CHANGE_LOG_DEDUP_WINDOW_SECONDS must be >= 0")
    if PLUGIN_BUS_CHANGE_LOG_DEDUP_WINDOW_SECONDS > 3600:
        raise ValueError("PLUGIN_BUS_CHANGE_LOG_DEDUP_WINDOW_SECONDS is unreasonably large (max: 3600s)")

    if STATUS_CONSUMER_SHUTDOWN_TIMEOUT <= 0:
        raise ValueError("STATUS_CONSUMER_SHUTDOWN_TIMEOUT must be positive")
    if STATUS_CONSUMER_SHUTDOWN_TIMEOUT > 300:
        raise ValueError("STATUS_CONSUMER_SHUTDOWN_TIMEOUT is unreasonably large (max: 300s)")

    if PROCESS_SHUTDOWN_TIMEOUT <= 0:
        raise ValueError("PROCESS_SHUTDOWN_TIMEOUT must be positive")
    if PROCESS_SHUTDOWN_TIMEOUT > 300:
        raise ValueError("PROCESS_SHUTDOWN_TIMEOUT is unreasonably large (max: 300s)")

    if PROCESS_TERMINATE_TIMEOUT <= 0:
        raise ValueError("PROCESS_TERMINATE_TIMEOUT must be positive")
    if PROCESS_TERMINATE_TIMEOUT > 60:
        raise ValueError("PROCESS_TERMINATE_TIMEOUT is unreasonably large (max: 60s)")

    _validate_http_url(NEKO_AUTH_URL, name="NEKO_AUTH_URL", allow_empty=True)
    _validate_http_url(MARKET_API_URL, name="MARKET_API_URL", allow_empty=True)
    _validate_http_url(MARKET_WEB_URL, name="MARKET_WEB_URL", allow_empty=True)
    for origin in MARKET_ORIGINS:
        _validate_market_origin(origin)
    
    if COMMUNICATION_THREAD_POOL_MAX_WORKERS <= 0:
        raise ValueError("COMMUNICATION_THREAD_POOL_MAX_WORKERS must be positive")
    if COMMUNICATION_THREAD_POOL_MAX_WORKERS > 100:
        raise ValueError("COMMUNICATION_THREAD_POOL_MAX_WORKERS is unreasonably large (max: 100)")
    
    if MESSAGE_QUEUE_DEFAULT_MAX_COUNT <= 0:
        raise ValueError("MESSAGE_QUEUE_DEFAULT_MAX_COUNT must be positive")
    if MESSAGE_QUEUE_DEFAULT_MAX_COUNT > 10000:
        raise ValueError("MESSAGE_QUEUE_DEFAULT_MAX_COUNT is unreasonably large (max: 10000)")
    
    if STATUS_MESSAGE_DEFAULT_MAX_COUNT <= 0:
        raise ValueError("STATUS_MESSAGE_DEFAULT_MAX_COUNT must be positive")
    if STATUS_MESSAGE_DEFAULT_MAX_COUNT > 10000:
        raise ValueError("STATUS_MESSAGE_DEFAULT_MAX_COUNT is unreasonably large (max: 10000)")


# Validate configuration at module load.
validate_config()


# ========== Legacy-plugin compatibility aliases ==========
# The host no longer reads these names, but installed plugins may still
# import from plugin.settings or read corresponding get_system_config() keys.
# Removing them breaks users' installed plugins, so resolve lazily with identical old values,
# emitting DeprecationWarning only when actually accessed to guide migration.
# Name -> (getter, replacement; None means the host no longer uses it and has no replacement).
_DEPRECATED_ALIASES: dict[str, tuple[Callable[[], object], str | None]] = {
    "PLUGIN_CONFIG_ROOT": (
        lambda: BUILTIN_PLUGIN_CONFIG_ROOT,
        "PLUGIN_CONFIG_ROOTS or USER_PLUGIN_CONFIG_ROOT",
    ),
    "MARKET_URL": (lambda: MARKET_API_URL, "MARKET_API_URL"),
    "RESULT_CONSUMER_SLEEP_INTERVAL": (lambda: 0.1, None),
    "PLUGIN_LOG_LEVEL": (lambda: "INFO", None),
    "PLUGIN_LOG_MAX_BYTES": (lambda: 5 * 1024 * 1024, None),
    "PLUGIN_LOG_BACKUP_COUNT": (lambda: 10, None),
    "PLUGIN_LOG_MAX_FILES": (lambda: 20, None),
    "NEKO_LOGURU_LEVEL": (lambda: os.getenv("NEKO_LOGURU_LEVEL", "INFO"), None),
}


def __getattr__(name: str) -> object:
    """Resolve deprecated aliases so already-installed plugins keep loading."""
    alias = _DEPRECATED_ALIASES.get(name)
    if alias is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    resolve, replacement = alias
    hint = f"use {replacement} instead" if replacement else "the host no longer reads it"
    warnings.warn(
        f"plugin.settings.{name} is deprecated; {hint}.",
        DeprecationWarning,
        stacklevel=2,
    )
    return resolve()


def get_public_system_config_value(key: str) -> object:
    """Resolve one ``PUBLIC_SYSTEM_CONFIG_KEYS`` entry for the admin snapshot.

    Deprecated aliases resolve silently here: the host building the snapshot is
    not the caller that has to migrate. Unknown keys raise ``AttributeError``.
    """
    alias = _DEPRECATED_ALIASES.get(key)
    if alias is not None:
        return alias[0]()
    try:
        return globals()[key]
    except KeyError:
        raise AttributeError(key) from None


# ========== Exports ==========

__all__ = [
    # Path configuration.
    "BUILTIN_PLUGIN_CONFIG_ROOT",
    "USER_PLUGIN_CONFIG_ROOT",
    "USER_PACKAGE_PROFILES_ROOT",
    "USER_PLUGIN_PACKAGES_ROOT",
    "PLUGIN_CONFIG_ROOTS",
    "NEKO_AUTH_URL",
    "NEKO_AUTH_CLIENT_ID",
    "MARKET_API_URL",
    "MARKET_WEB_URL",
    "MARKET_ORIGINS",
    "get_builtin_plugin_config_root",
    "get_plugin_config_root",
    "get_plugin_config_roots",
    "get_user_plugin_config_root",
    "get_user_package_profiles_root",
    "get_user_plugin_packages_root",
    
    # Queue configuration.
    "EVENT_QUEUE_MAX",
    "LIFECYCLE_QUEUE_MAX",
    "MESSAGE_QUEUE_MAX",
    
    # Timeout configuration.
    "PLUGIN_EXECUTION_TIMEOUT",
    "PLUGIN_TRIGGER_TIMEOUT",
    "PLUGIN_STARTUP_TIMEOUT",
    "PLUGIN_SHUTDOWN_TIMEOUT",
    "PLUGIN_SHUTDOWN_TOTAL_TIMEOUT",
    "PLUGIN_HOT_RELOAD",
    "PLUGIN_HOT_RELOAD_INTERVAL",
    "PLUGIN_HOT_RELOAD_DEBOUNCE",
    "QUEUE_GET_TIMEOUT",
    "BUS_SDK_POLL_INTERVAL_SECONDS",
    "STATUS_CONSUMER_SHUTDOWN_TIMEOUT",
    "PROCESS_SHUTDOWN_TIMEOUT",
    "PROCESS_TERMINATE_TIMEOUT",
    
    # Thread-pool configuration.
    "COMMUNICATION_THREAD_POOL_MAX_WORKERS",
    
    # Message-queue configuration.
    "MESSAGE_QUEUE_DEFAULT_MAX_COUNT",
    "STATUS_MESSAGE_DEFAULT_MAX_COUNT",
    
    # SDK metadata attributes.
    "NEKO_PLUGIN_META_ATTR",
    "NEKO_PLUGIN_TAG",
    
    # Message-schema validation.
    "MESSAGE_SCHEMA_STRICT",
    "MESSAGE_SCHEMA_ALLOW_UNSAFE",
    "MESSAGE_SCHEMA_WARN_UNKNOWN_FIELDS",
    
    # Other configuration.
    "STATUS_CONSUMER_SLEEP_INTERVAL",
    "MESSAGE_CONSUMER_SLEEP_INTERVAL",
    "PLUGIN_LOG_MESSAGE_FORWARD",
    "PLUGIN_LOG_SYNC_CALL_WARNINGS",
    "PLUGIN_LOG_BUS_SUBSCRIPTIONS",
    "PLUGIN_LOG_BUS_SUBSCRIBE_REQUESTS",
    "PLUGIN_LOG_BUS_SDK_TIMEOUT_WARNINGS",
    "PLUGIN_LOG_CTX_STATUS_UPDATE",
    "PLUGIN_LOG_CTX_MESSAGE_PUSH",
    "PLUGIN_LOG_SERVER_DEBUG",
    "PLUGIN_MESSAGE_FORWARD_LOG_DEDUP_WINDOW_SECONDS",
    "PLUGIN_BUS_CHANGE_LOG_DEDUP_WINDOW_SECONDS",
    "SYNC_CALL_IN_HANDLER_POLICY",

    # Message plane
    "MESSAGE_PLANE_ZMQ_RPC_ENDPOINT",
    "MESSAGE_PLANE_ZMQ_PUB_ENDPOINT",
    "MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT",
    "MESSAGE_PLANE_VALIDATE_MODE",

    # State-persistence configuration.
    "PLUGIN_STATE_BACKEND_DEFAULT",

    # Run configuration.
    "RUN_EXECUTION_TIMEOUT",
    "RUN_STORE_MAX_COMPLETED",

    # Validation functions.
    "validate_config",

    # Legacy-plugin aliases resolved lazily by module __getattr__; see _DEPRECATED_ALIASES.
    "PLUGIN_CONFIG_ROOT",  # noqa: F822
    "MARKET_URL",  # noqa: F822
    "RESULT_CONSUMER_SLEEP_INTERVAL",  # noqa: F822
    "PLUGIN_LOG_LEVEL",  # noqa: F822
    "PLUGIN_LOG_MAX_BYTES",  # noqa: F822
    "PLUGIN_LOG_BACKUP_COUNT",  # noqa: F822
    "PLUGIN_LOG_MAX_FILES",  # noqa: F822
]


# Admin API: explicit allowlist for externally exposable system settings.
PUBLIC_SYSTEM_CONFIG_KEYS = (
    "BUILTIN_PLUGIN_CONFIG_ROOT",
    "USER_PLUGIN_CONFIG_ROOT",
    "USER_PACKAGE_PROFILES_ROOT",
    "USER_PLUGIN_PACKAGES_ROOT",
    "PLUGIN_CONFIG_ROOTS",
    "NEKO_AUTH_URL",
    "NEKO_AUTH_CLIENT_ID",
    "MARKET_API_URL",
    "MARKET_WEB_URL",
    "EVENT_QUEUE_MAX",
    "LIFECYCLE_QUEUE_MAX",
    "MESSAGE_QUEUE_MAX",
    "PLUGIN_EXECUTION_TIMEOUT",
    "PLUGIN_TRIGGER_TIMEOUT",
    "PLUGIN_STARTUP_TIMEOUT",
    "PLUGIN_SHUTDOWN_TIMEOUT",
    "PLUGIN_SHUTDOWN_TOTAL_TIMEOUT",
    "QUEUE_GET_TIMEOUT",
    "BUS_SDK_POLL_INTERVAL_SECONDS",
    "STATUS_CONSUMER_SHUTDOWN_TIMEOUT",
    "PROCESS_SHUTDOWN_TIMEOUT",
    "PROCESS_TERMINATE_TIMEOUT",
    "PLUGIN_HOT_RELOAD",
    "PLUGIN_HOT_RELOAD_INTERVAL",
    "PLUGIN_HOT_RELOAD_DEBOUNCE",
    "COMMUNICATION_THREAD_POOL_MAX_WORKERS",
    "MESSAGE_QUEUE_DEFAULT_MAX_COUNT",
    "STATUS_MESSAGE_DEFAULT_MAX_COUNT",
    "NEKO_PLUGIN_META_ATTR",
    "NEKO_PLUGIN_TAG",
    "MESSAGE_SCHEMA_STRICT",
    "MESSAGE_SCHEMA_ALLOW_UNSAFE",
    "MESSAGE_SCHEMA_WARN_UNKNOWN_FIELDS",
    "STATUS_CONSUMER_SLEEP_INTERVAL",
    "MESSAGE_CONSUMER_SLEEP_INTERVAL",
    "PLUGIN_LOG_MESSAGE_FORWARD",
    "PLUGIN_LOG_SYNC_CALL_WARNINGS",
    "PLUGIN_LOG_BUS_SUBSCRIPTIONS",
    "PLUGIN_LOG_BUS_SUBSCRIBE_REQUESTS",
    "PLUGIN_LOG_BUS_SDK_TIMEOUT_WARNINGS",
    "PLUGIN_LOG_CTX_STATUS_UPDATE",
    "PLUGIN_LOG_CTX_MESSAGE_PUSH",
    "PLUGIN_LOG_SERVER_DEBUG",
    "PLUGIN_MESSAGE_FORWARD_LOG_DEDUP_WINDOW_SECONDS",
    "PLUGIN_BUS_CHANGE_LOG_DEDUP_WINDOW_SECONDS",
    "SYNC_CALL_IN_HANDLER_POLICY",
    "MESSAGE_PLANE_ZMQ_RPC_ENDPOINT",
    "MESSAGE_PLANE_ZMQ_PUB_ENDPOINT",
    "MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT",
    "MESSAGE_PLANE_VALIDATE_MODE",
    "PLUGIN_STATE_BACKEND_DEFAULT",
    "RUN_EXECUTION_TIMEOUT",
    "RUN_STORE_MAX_COMPLETED",
    # Legacy-plugin compatibility: installed plugins may read these get_system_config() keys.
    "PLUGIN_CONFIG_ROOT",
    "MARKET_URL",
    "RESULT_CONSUMER_SLEEP_INTERVAL",
    "PLUGIN_LOG_LEVEL",
    "PLUGIN_LOG_MAX_BYTES",
    "PLUGIN_LOG_BACKUP_COUNT",
    "PLUGIN_LOG_MAX_FILES",
)
