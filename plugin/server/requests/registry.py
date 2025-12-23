from __future__ import annotations

from typing import Dict

from plugin.server.requests.typing import RequestHandler

def build_request_handlers() -> Dict[str, RequestHandler]:
    from plugin.server.requests.plugin_to_plugin import handle_plugin_to_plugin
    from plugin.server.requests.plugin_query import handle_plugin_query
    from plugin.server.requests.plugin_config import (
        handle_plugin_config_get,
        handle_plugin_config_update,
    )

    return {
        "PLUGIN_TO_PLUGIN": handle_plugin_to_plugin,
        "PLUGIN_QUERY": handle_plugin_query,
        "PLUGIN_CONFIG_GET": handle_plugin_config_get,
        "PLUGIN_CONFIG_UPDATE": handle_plugin_config_update,
    }
