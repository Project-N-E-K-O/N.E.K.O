"""Plugin flavor decorators exports."""

from plugin.sdk_v2.shared.core import decorators as _shared_dec

for _export_name in _shared_dec.__all__:
    if _export_name == "plugin":
        continue
    globals()[_export_name] = getattr(_shared_dec, _export_name)

# Explicit binding for static analyzers; remains monkeypatchable at module scope.
plugin_entry = _shared_dec.plugin_entry


class _PluginDecorators:
    @staticmethod
    def entry(**kwargs: object):
        # Keep plugin.entry() patchable against plugin module symbol.
        return plugin_entry(**kwargs)


plugin = _PluginDecorators()

__all__ = [name for name in _shared_dec.__all__ if name != "plugin"] + ["plugin"]
