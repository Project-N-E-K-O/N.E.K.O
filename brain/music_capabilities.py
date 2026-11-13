"""Music schemas and execution permissions; no natural-language routing."""

from copy import deepcopy
from utils.music_permissions import CLOUD_WRITE_ACTIONS


def scoped_music_catalog(plugins, text="", context="", *, proactive=False):
    result = deepcopy(plugins)
    if proactive:
        for plugin in result:
            if plugin.get("id") != "netease_music":
                continue
            for entry in plugin.get("entries", []):
                action = (
                    (entry.get("input_schema") or {})
                    .get("properties", {})
                    .get("action", {})
                )
                forbidden = (
                    CLOUD_WRITE_ACTIONS
                    if entry.get("id") == "cloud_music"
                    else {"save", "save_current"}
                )
                if "enum" in action:
                    action["enum"] = [
                        item for item in action["enum"] if item not in forbidden
                    ]
    return result
