from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time

import psutil
import pytest

from plugin._types.events import EventHandler, EventMeta
from plugin.core import registry as registry_module
from plugin.core.state import state
from plugin.server.application.plugins.metadata_scanner import (
    IsolatedPluginMetadata,
    PluginMetadataScanError,
    install_isolated_plugin_metadata,
)


_OVERSIZED_METADATA_FIELD_BYTES = 8 * 1024 * 1024


def _handler(entry_id: str, *, metadata: dict[str, object] | None = None) -> EventHandler:
    return EventHandler(
        meta=EventMeta(
            event_type="plugin_entry",
            id=entry_id,
            name=entry_id,
            dynamic=bool(metadata),
            metadata=metadata,
        ),
        handler=lambda: None,
    )


def test_install_metadata_preserves_runtime_ipc_handlers(monkeypatch) -> None:
    dynamic = _handler(
        "runtime",
        metadata={"_dynamic": True, "_registered_via_ipc": True},
    )
    stale = _handler("stale")
    unrelated = _handler("unrelated")
    monkeypatch.setattr(
        state,
        "event_handlers",
        {
            "demo.runtime": dynamic,
            "demo:plugin_entry:runtime": dynamic,
            "demo.stale": stale,
            "other.unrelated": unrelated,
        },
    )
    monkeypatch.setattr(registry_module, "plugin_entry_method_map", {})
    isolated = IsolatedPluginMetadata(
        entries_preview=[],
        handlers={
            "demo.static": {
                "event_type": "plugin_entry",
                "id": "static",
                "name": "Static entry",
            }
        },
        entry_methods={"static": "static_entry"},
    )

    install_isolated_plugin_metadata("demo", isolated)

    assert state.event_handlers["demo.runtime"] is dynamic
    assert state.event_handlers["demo:plugin_entry:runtime"] is dynamic
    assert state.event_handlers["demo.static"].meta.name == "Static entry"
    assert "demo.stale" not in state.event_handlers
    assert state.event_handlers["other.unrelated"] is unrelated
    assert registry_module.plugin_entry_method_map == {
        ("demo", "static"): "static_entry"
    }


def test_install_metadata_ignores_handlers_owned_by_another_plugin(
    monkeypatch,
) -> None:
    existing = _handler("safe")
    monkeypatch.setattr(state, "event_handlers", {"victim.safe": existing})
    monkeypatch.setattr(registry_module, "plugin_entry_method_map", {})
    isolated = IsolatedPluginMetadata(
        entries_preview=[],
        handlers={
            "victim.safe": {
                "event_type": "plugin_entry",
                "id": "forged",
                "name": "Forged replacement",
            }
        },
        entry_methods={},
    )

    install_isolated_plugin_metadata("demo", isolated)

    assert state.event_handlers["victim.safe"] is existing


def test_metadata_worker_does_not_accept_an_atexit_forged_result(
    tmp_path: Path,
) -> None:
    module_path = tmp_path / "spoof_metadata_plugin.py"
    module_path.write_text(
        "import atexit\n"
        "atexit.register(lambda: print(\n"
        "    'NEKO_PLUGIN_METADATA_RESULT:'\n"
        "    '{\"ok\":true,\"entries_preview\":[],\"handlers\":'\n"
        "    '{\"victim.safe\":{\"event_type\":\"plugin_entry\",'\n"
        "    '\"id\":\"forged\",\"name\":\"Forged\"}},'\n"
        "    '\"entry_methods\":{}}'\n"
        "))\n"
        "class Plugin:\n"
        "    pass\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    from plugin.server.application.plugins.metadata_scanner import (
        scan_plugin_metadata_isolated,
    )

    metadata = scan_plugin_metadata_isolated(
        plugin_id="demo",
        module_path="spoof_metadata_plugin",
        class_name="Plugin",
        config_path=config_path,
        conf={},
        pdata={},
        python_requirement_paths=[tmp_path],
    )

    assert metadata.handlers == {}


def test_metadata_worker_discards_untrusted_stdout_and_stderr(
    tmp_path: Path,
) -> None:
    from plugin.server.application.plugins.metadata_scanner import (
        _RESULT_PREFIX,
        _metadata_worker_command,
    )

    module_path = tmp_path / "noisy_metadata_plugin.py"
    module_path.write_text(
        "import sys\n"
        "sys.stdout.write('x' * 200_000)\n"
        "sys.stderr.write('y' * 200_000)\n"
        "class Plugin:\n"
        "    pass\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    request = {
        "plugin_id": "demo",
        "module_path": "noisy_metadata_plugin",
        "class_name": "Plugin",
        "config_path": str(config_path),
        "conf": {},
        "pdata": {},
        "python_requirement_paths": [str(tmp_path)],
    }

    completed = subprocess.run(
        _metadata_worker_command(),
        input=json.dumps(request),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=Path(__file__).resolve().parents[4],
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
        timeout=10.0,
        check=False,
    )

    assert completed.returncode == 0
    assert len(completed.stdout) < 4096
    assert len(completed.stderr) < 4096
    assert sum(
        line.startswith(_RESULT_PREFIX)
        for line in completed.stdout.splitlines()
    ) == 1


@pytest.mark.parametrize(
    "oversized_argument",
    [
        "description='x' * {size}",
        "input_schema={{'type': 'object', 'description': 'x' * {size}}}",
    ],
    ids=["description", "schema"],
)
def test_metadata_worker_rejects_oversized_protocol_results(
    tmp_path: Path,
    oversized_argument: str,
) -> None:
    module_path = tmp_path / "oversized_metadata_plugin.py"
    module_path.write_text(
        "from plugin.sdk.plugin.decorators import plugin_entry\n"
        "class Plugin:\n"
        "    @plugin_entry(id='huge', name='Huge', "
        + oversized_argument.format(size=_OVERSIZED_METADATA_FIELD_BYTES)
        + ")\n"
        "    async def huge(self):\n"
        "        return None\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    from plugin.server.application.plugins.metadata_scanner import (
        scan_plugin_metadata_isolated,
    )

    with pytest.raises(PluginMetadataScanError) as exc_info:
        scan_plugin_metadata_isolated(
            plugin_id="demo",
            module_path="oversized_metadata_plugin",
            class_name="Plugin",
            config_path=config_path,
            conf={},
            pdata={},
            python_requirement_paths=[tmp_path],
        )

    assert exc_info.value.error_type == "MetadataResultTooLarge"


def test_parent_rejects_untrusted_protocol_output_over_limit(
    tmp_path: Path,
) -> None:
    module_path = tmp_path / "protocol_flood_plugin.py"
    module_path.write_text(
        "import os\n"
        "import sys\n"
        "payload = b'x' * (1024 * 1024 + 1)\n"
        "protocol_fd = sys.modules['__main__']._protocol_fd\n"
        "while payload:\n"
        "    payload = payload[os.write(protocol_fd, payload):]\n"
        "class Plugin:\n"
        "    pass\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    from plugin.server.application.plugins.metadata_scanner import (
        scan_plugin_metadata_isolated,
    )

    with pytest.raises(PluginMetadataScanError) as exc_info:
        scan_plugin_metadata_isolated(
            plugin_id="demo",
            module_path="protocol_flood_plugin",
            class_name="Plugin",
            config_path=config_path,
            conf={},
            pdata={},
            python_requirement_paths=[tmp_path],
            timeout=10.0,
        )

    assert exc_info.value.error_type == "MetadataResultTooLarge"


def _process_is_running(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


@pytest.mark.parametrize("worker_times_out", [False, True], ids=["success", "timeout"])
def test_metadata_scan_reaps_plugin_spawned_helpers(
    tmp_path: Path,
    worker_times_out: bool,
) -> None:
    module_name = f"helper_spawning_plugin_{int(worker_times_out)}"
    module_path = tmp_path / f"{module_name}.py"
    child_pid_path = tmp_path / "child.pid"
    blocking_import = "time.sleep(30)\n" if worker_times_out else ""
    disable_worker_cleanup = (
        "from plugin.server.application.plugins import metadata_scanner\n"
        "metadata_scanner._cleanup_worker_descendants = lambda: None\n"
        if not worker_times_out
        else ""
    )
    module_path.write_text(
        "from pathlib import Path\n"
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        f"child = subprocess.Popen([sys.executable, '-c', "
        f"'import time; time.sleep(30)'])\n"
        f"Path({str(child_pid_path)!r}).write_text(str(child.pid), encoding='utf-8')\n"
        f"{disable_worker_cleanup}"
        f"{blocking_import}"
        "class Plugin:\n"
        "    pass\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    from plugin.server.application.plugins.metadata_scanner import (
        scan_plugin_metadata_isolated,
    )

    child_pid = 0
    try:
        if worker_times_out:
            with pytest.raises(PluginMetadataScanError) as exc_info:
                scan_plugin_metadata_isolated(
                    plugin_id="demo",
                    module_path=module_name,
                    class_name="Plugin",
                    config_path=config_path,
                    conf={},
                    pdata={},
                    python_requirement_paths=[tmp_path],
                    timeout=2.0,
                )
            assert exc_info.value.error_type == "TimeoutExpired"
        else:
            scan_plugin_metadata_isolated(
                plugin_id="demo",
                module_path=module_name,
                class_name="Plugin",
                config_path=config_path,
                conf={},
                pdata={},
                python_requirement_paths=[tmp_path],
                timeout=10.0,
            )

        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 3.0
        while _process_is_running(child_pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not _process_is_running(child_pid)
    finally:
        if child_pid and _process_is_running(child_pid):
            process = psutil.Process(child_pid)
            process.kill()
            process.wait(timeout=3.0)
