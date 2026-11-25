"""The host hands the manifest's entry ids to a real plugin process."""

from __future__ import annotations

import pytest

from plugin.core import host as host_module
from plugin.core.host import PluginHost

pytestmark = pytest.mark.plugin_integration

FIXTURE = '''
from plugin.core import entry_executor
from plugin.sdk.plugin import NekoPluginBase, plugin_entry

# This process only: undeclared methods are refused, so a call that runs
# shows the id arrived as declared.
entry_executor.UNDECLARED_ENTRY_GRACE = False


class ManifestEntriesFixturePlugin(NekoPluginBase):
    @plugin_entry(id="registered")
    async def registered(self, **_):
        return "registered"

    async def declared(self, **_):
        return "declared"

    async def undeclared(self, **_):
        return "undeclared"
'''


@pytest.fixture
def plugin_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(host_module, "_refresh_child_storage_layout_env", lambda logger: None)
    directory = tmp_path / "manifest_entries_fixture"
    directory.mkdir()
    (directory / "__init__.py").write_text(FIXTURE, encoding="utf-8")
    entry = "plugins.manifest_entries_fixture:ManifestEntriesFixturePlugin"
    (directory / "plugin.toml").write_text(
        f"[plugin]\nid='manifest_entries_fixture'\nname='manifest_entries_fixture'\nentry='{entry}'\n"
        "[[plugin.entries]]\nid='declared'\n",
        encoding="utf-8",
    )
    return directory, entry


async def test_a_manifest_declared_method_runs_in_the_plugin_process(plugin_dir):
    directory, entry = plugin_dir
    host = PluginHost("manifest_entries_fixture", entry, directory / "plugin.toml", source_only=True)
    host.set_manifest_entry_ids(["declared"])
    await host.start(startup_timeout=10, startup_failure="abort")
    try:
        assert await host.trigger("registered", {}, timeout=5) == "registered"
        assert await host.trigger("declared", {}, timeout=5) == "declared"
        with pytest.raises(Exception, match="not found"):
            await host.trigger("undeclared", {}, timeout=5)
    finally:
        await host.shutdown(timeout=2)
