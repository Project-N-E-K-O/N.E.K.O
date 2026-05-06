"""Custom shell completers for shtab integration.

These completers provide dynamic tab-completion for plugin names and
package files.  They are only used when ``shtab`` is installed.

Each completer is a dict mapping shell names to completion expressions.
For shells that support calling external commands during completion,
we invoke ``neko-plugin`` itself with a hidden helper to list candidates.
"""

from __future__ import annotations


def _make_plugin_name_completer() -> dict[str, str]:
    """Completer that lists plugin directory names under plugins_root."""
    return {
        "bash": (
            "$(root=\"${NEKO_PLUGINS_ROOT:-plugin/plugins}\"; "
            "for d in \"$root\"/*; do "
            "[ -f \"$d/plugin.toml\" ] && basename \"$d\"; "
            "done 2>/dev/null)"
        ),
        "zsh": "_files -W ${NEKO_PLUGINS_ROOT:-plugin/plugins} -/",
        "tcsh": "d",
    }


def _make_package_file_completer() -> dict[str, str]:
    """Completer that matches .neko-plugin and .neko-bundle files."""
    return {
        "bash": "$(command ls -1 *.neko-plugin *.neko-bundle 2>/dev/null)",
        "zsh": "_files -g '*.(neko-plugin|neko-bundle)'",
        "tcsh": "f",
    }


try:
    PLUGIN_NAME_COMPLETER = _make_plugin_name_completer()
    PACKAGE_FILE_COMPLETER = _make_package_file_completer()
except Exception:
    PLUGIN_NAME_COMPLETER = {}
    PACKAGE_FILE_COMPLETER = {}
