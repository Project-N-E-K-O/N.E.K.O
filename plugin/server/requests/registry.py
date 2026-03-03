from __future__ import annotations

from typing import Dict

from plugin.server.requests.typing import RequestHandler

def build_request_handlers() -> Dict[str, RequestHandler]:
    from plugin.server.requests.plugin_to_plugin import handle_plugin_to_plugin
    from plugin.server.requests.plugin_query import handle_plugin_query
    from plugin.server.requests.plugin_config import (
        handle_plugin_config_get,
        handle_plugin_config_base_get,
        handle_plugin_config_profiles_get,
        handle_plugin_config_profile_get,
        handle_plugin_config_effective_get,
        handle_plugin_config_update,
    )
    from plugin.server.requests.system_config import handle_plugin_system_config_get
    from plugin.server.requests.memory import handle_memory_query
    from plugin.server.requests.user_context import handle_user_context_get
    from plugin.server.requests.export import handle_export_push
    from plugin.server.requests.run_update import handle_run_update
    from plugin.server.requests.events import handle_event_get
    from plugin.server.requests.lifecycle import handle_lifecycle_get
    from plugin.server.requests.bus_delete import (
        handle_event_del,
        handle_lifecycle_del,
        handle_message_del,
    )
    from plugin.server.requests.bus_subscribe import (
        handle_bus_subscribe,
        handle_bus_unsubscribe,
    )

    return {
        "PLUGIN_TO_PLUGIN": handle_plugin_to_plugin,
        "PLUGIN_QUERY": handle_plugin_query,
        "PLUGIN_CONFIG_GET": handle_plugin_config_get,
        "PLUGIN_CONFIG_BASE_GET": handle_plugin_config_base_get,
        "PLUGIN_CONFIG_PROFILES_GET": handle_plugin_config_profiles_get,
        "PLUGIN_CONFIG_PROFILE_GET": handle_plugin_config_profile_get,
        "PLUGIN_CONFIG_EFFECTIVE_GET": handle_plugin_config_effective_get,
        "PLUGIN_CONFIG_UPDATE": handle_plugin_config_update,
        "PLUGIN_SYSTEM_CONFIG_GET": handle_plugin_system_config_get,
        "MEMORY_QUERY": handle_memory_query,
        "USER_CONTEXT_GET": handle_user_context_get,
        "EXPORT_PUSH": handle_export_push,
        "RUN_UPDATE": handle_run_update,
        "EVENT_GET": handle_event_get,
        "LIFECYCLE_GET": handle_lifecycle_get,
        "MESSAGE_DEL": handle_message_del,
        "EVENT_DEL": handle_event_del,
        "LIFECYCLE_DEL": handle_lifecycle_del,
        "BUS_SUBSCRIBE": handle_bus_subscribe,
        "BUS_UNSUBSCRIBE": handle_bus_unsubscribe,
    }
