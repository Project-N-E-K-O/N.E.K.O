"""Locate the workbench tool resources in a packaged N.E.K.O. backend."""

from pathlib import Path


def plugin_tool_resources_dir(backend: Path) -> Path:
    directory = backend.resolve().parent
    contents = directory.parent
    if directory.name == "MacOS" and contents.name == "Contents" and contents.parent.suffix == ".app":
        # Keep data in the resource directory covered by the bundle signature.
        directory = contents / "Resources"
    return directory / "plugin-tools"
