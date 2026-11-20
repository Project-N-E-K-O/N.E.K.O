"""What a dsh process is started with: configuration, guard, environment, directory."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from nekoclaw.dsh import profile
from nekoclaw.dsh.profile import Shape

SHAPE = Shape(context_window=128_000, max_output_tokens=8_192, supports_images=False)


def _rows(shape: Shape = SHAPE, slots: int = 4, home: Path = Path("home")) -> dict[str, dict[str, Any]]:
    patch = profile.build_patch(shape=shape, endpoint="http://127.0.0.1:5000/model/v1", slots=slots, home=home)
    return {row["id"]: row for row in patch if "id" in row}


def _inserted(shape: Shape = SHAPE) -> list[dict[str, Any]]:
    patch = profile.build_patch(shape=shape, endpoint="http://127.0.0.1:5000/model/v1", slots=2, home=Path("home"))
    return [item for row in patch for item in row.get("insert") or []]


def test_nothing_is_uploaded() -> None:
    rows = _rows()
    # dsh sends the session log and the plugin list along with model requests unless told not to.
    assert rows["session-log-deepseek"]["config"] == {"enabled": False}
    assert rows["plugin-package-inventory-deepseek"]["config"] == {"enabled": False}
    assert rows["session-telemetry-otel"] == {"id": "session-telemetry-otel", "disabled": True}
    environment = profile.child_environment({}, home=Path("home"), token="t")
    assert environment["DSH_TELEMETRY_DISABLED"] == "1"


def test_the_model_is_reached_only_through_the_drivers_endpoint() -> None:
    rows = _rows(slots=3)
    providers = rows["llm-pi-ai"]["config"]["providers"]
    assert list(providers) == [profile.PROVIDER]
    provider = providers[profile.PROVIDER]
    assert provider["baseURL"] == "http://127.0.0.1:5000/model/v1" and provider["api"] == "openai-completions"
    # The "key" is read from a variable, and is the endpoint's token.
    assert provider["apiKeyEnv"] == profile.KEY_VARIABLE and "apiKey" not in provider
    assert [model["id"] for model in provider["models"]] == [profile.slot_model(index) for index in range(3)]
    assert all(model["contextWindow"] == 128_000 and model["maxTokens"] == 8_192 and model["input"] == ["text"] for model in provider["models"])
    assert rows["acp"]["config"] == {"provider": profile.PROVIDER, "model": profile.slot_model(0)}
    assert rows["agent-default-model"]["config"] == rows["acp"]["config"]


def test_dshs_own_web_tools_are_off_and_none_of_its_tools_reaches_the_network_unasked() -> None:
    rows = _rows(Shape(context_window=128_000, max_output_tokens=8_192, supports_images=False))
    # Its search needs a key of its vendor's. Its fetch reaches every address, the
    # loopback interface and private networks included; the host's, which is offered
    # in its place, reaches public addresses only, as in the built-in runtime.
    assert rows["tool-web"]["config"] == {"search": False, "fetch": False}
    assert "disabled" not in rows["tool-web"]
    # Were it ever switched on again, it would be asked about like any tool the driver does not know.
    assert "web_fetch" not in profile.UNASKED_TOOLS and "web_search" not in profile.UNASKED_TOOLS


def test_a_model_that_sees_pictures_is_declared_so() -> None:
    rows = _rows(Shape(context_window=128_000, max_output_tokens=8_192, supports_images=True))
    models = rows["llm-pi-ai"]["config"]["providers"][profile.PROVIDER]["models"]
    assert all(model["input"] == ["text", "image"] for model in models)


def test_the_model_option_is_written_the_way_dsh_writes_it() -> None:
    assert profile.model_option(7) == '["nekoclaw","slot-7"]'


def test_the_sandbox_is_off_and_every_call_the_host_rules_on_is_asked_about() -> None:
    from nekoclaw.runtime.permissions import classify

    rows = _rows()
    assert rows["sandbox-policy"]["config"] == {"mode": "danger-full-access"}
    assert rows["approval"]["config"] == {"policy": "ask"}
    # dsh refuses to start when its sandbox and approval settings match no preset.
    preset = rows["permission"]["config"]
    assert preset["presets"][preset["defaultPreset"]] == {"sandbox": "danger-full-access", "approval": "ask"}
    guard = next(item for item in _inserted() if item["id"] == "nekoclaw-guard")
    assert guard["name"] == f"./{profile.GUARD_FILE}"
    assert guard["config"] == {
        "unasked": list(profile.UNASKED_TOOLS),
        "unaskedPrefixes": ["mcp__host__"],
        # The one tool of the host's that may have to wait for the user is asked about all the same.
        "asked": ["mcp__host__web_search"],
    }
    # The tools the built-in runtime's gate has a category for are asked about, each of them.
    workdir = Path("work").resolve()
    for tool in ("read", "read_image", "glob", "grep", "write", "edit", "pwsh", "bash", "web_fetch", "todo_write", "job_output", "skill"):
        ruled_on = classify(tool, {"file_path": "x", "path": "x", "command": "x"}, workdir).category != "other"
        assert ruled_on == (tool in profile.GATED_TOOLS), tool
    assert not set(profile.GATED_TOOLS) & set(profile.UNASKED_TOOLS)
    # So are the gateway's tools (the gateway rules on them, before the call) and
    # whatever a dsh plugin brings: neither is among what passes unasked.
    assert not any(tool.startswith("mcp__") for tool in profile.UNASKED_TOOLS)
    assert f"mcp__{profile.GATEWAY_SERVICE}__".startswith("mcp__host__") is False


def test_the_guard_asks_about_everything_it_is_not_told_to_let_pass() -> None:
    source = profile.GUARD_SOURCE
    assert "ctx.on('tools/pre-execute'" in source and "kind: 'ask'" in source
    # Asking is what is left when nothing lets the call pass: no configuration, nothing passes.
    body = source[source.index("ctx.on(") :]
    assert body.index("return next()") < body.index("return { kind: 'ask'")
    assert "Array.isArray(config?.unasked) ? config.unasked : []" in source
    assert "Array.isArray(config?.unaskedPrefixes) ? config.unaskedPrefixes : []" in source
    # What it is told to ask about is asked about though its name would let it pass.
    assert "Array.isArray(config?.asked) ? config.asked : []" in source
    assert "if (!asked.has(exec.name) && (unasked.has(exec.name) || prefixes.some(" in source
    # It says it was loaded only after it listens.
    assert source.index("ctx.on(") < source.index(profile.GUARD_READY)
    assert f"process.stderr.write('{profile.GUARD_READY}\\n')" in source


def test_where_dsh_keeps_the_long_results_of_one_session() -> None:
    import hashlib

    home = Path("home")
    session = "6fb9e6f3-9e7f-44e0-a874-583885aa9787"
    # As dsh names the directory: after a digest of the session's id.
    expected = profile.spill_dir(home) / f"session-{hashlib.sha256(session.encode()).hexdigest()[:12]}"
    assert profile.session_spill_dir(home, session) == expected
    assert profile.session_spill_dir(home, "another-session") != expected


def test_what_cannot_work_under_this_host_is_switched_off() -> None:
    rows = _rows()
    for row in profile.DISABLED_ROWS:
        assert rows[row] == {"id": row, "disabled": True}
    for row in ("tool-subagent", "tool-subagent-fork", "tool-workflow", "plan-mode", "tool-working-directory", "mcp-resources"):
        assert row in profile.DISABLED_ROWS


def test_the_system_prompt_is_ours_and_names_the_hosts_tools_as_dsh_shows_them() -> None:
    from config.prompts.prompts_nekoclaw import TASK_AGENT_IDENTITY

    prompt = _rows()["system-prompt"]["config"]
    assert prompt["includeHarnessIdentity"] is False and prompt["personaPrefix"] == TASK_AGENT_IDENTITY
    suffix = prompt["personaSuffix"]
    assert "mcp__host__report" in suffix and "mcp__host__note_lesson" in suffix
    assert "ask_user_question" not in suffix and "`waiting`" in suffix
    # Shared by every session of the process, also those offered no subagent tool: told in a session's first message instead.
    assert "mcp__host__subagent" not in suffix and "mcp__host__wait_agent" not in suffix
    # dsh fills {{name}} with values of its own and refuses a name it does not know.
    assert "{{" not in prompt["personaPrefix"] and "{{" not in suffix


@pytest.mark.parametrize(("window", "headroom"), [(128_000, 12_800), (1_000_000, 65_536), (16_000, 1_600)])
def test_the_summary_margin_follows_the_window(window: int, headroom: int) -> None:
    rows = _rows(Shape(context_window=window, max_output_tokens=8_192, supports_images=False))
    compaction = rows["compaction-basic"]["config"]
    # dsh's fixed 65536 would leave nothing of a small window.
    assert compaction["headroomTokens"] == headroom
    assert compaction["maxTokens"] == min(8_192, headroom)
    assert window - 8_192 - headroom > 0


def test_dsh_sums_a_conversation_up_from_where_the_built_in_runtime_does() -> None:
    from nekoclaw.runtime.context import COMPACT_RATIO, ContextPolicy

    # The owner's numbers (2026-11-18): 200,000, summed up from 170,000 on.
    shape = Shape(context_window=200_000, max_output_tokens=8_192, supports_images=False)
    assert shape.compact_ratio == COMPACT_RATIO
    compaction = _rows(shape)["compaction-basic"]["config"]
    # dsh takes the smaller of the part of the window and what the output and the margin leave.
    by_ratio = round(200_000 * compaction["thresholdRatio"])
    by_reserve = 200_000 - 8_192 - compaction["headroomTokens"]
    assert min(by_ratio, by_reserve) == 170_000 == ContextPolicy(window=200_000, max_output=8_192).threshold

    # Told another point for a model, dsh is told the same.
    told = Shape(context_window=200_000, max_output_tokens=8_192, supports_images=False, compact_ratio=0.75)
    assert _rows(told)["compaction-basic"]["config"]["thresholdRatio"] == 0.75


def test_long_results_are_kept_below_dshs_home() -> None:
    home = Path("some") / "home"
    assert _rows(home=home)["spill-local"]["config"]["root"] == str(profile.spill_dir(home))


def test_the_profile_is_written_as_json_next_to_the_guard(tmp_path: Path) -> None:
    patch = profile.build_patch(shape=SHAPE, endpoint="http://127.0.0.1:1/model/v1", slots=2, home=tmp_path)

    first = profile.write_profile(tmp_path, patch, tag="aaaa")
    second = profile.write_profile(tmp_path, patch, tag="bbbb")

    assert first.parent == second.parent == profile.config_dir(tmp_path) and first != second
    assert json.loads(first.read_text(encoding="utf-8")) == patch
    assert (first.parent / profile.GUARD_FILE).read_text(encoding="utf-8") == profile.GUARD_SOURCE
    assert profile.launch_arguments(Path("dsh.exe"), first) == ["dsh.exe", "--profile", "acp", "--patch", str(first)]
    assert profile.launch_arguments(Path("python"), first, ("fake.py",))[:2] == ["python", "fake.py"]


def test_the_environment_is_the_hosts_without_what_would_configure_dsh() -> None:
    base = {
        "PATH": "p",
        "DSH_HOME": "somewhere-else",
        "dsh_permission_mode": "read-only",
        "DEEPSEEK_API_KEY": "sk-user",
        "DEEPSEEK_BASE_URL": "https://x",
        "OPENAI_API_KEY": "kept-as-for-the-built-in-runtime",
    }
    home = Path("root") / "home"

    environment = profile.child_environment(base, home=home, token="endpoint-token")

    assert environment["PATH"] == "p" and environment["OPENAI_API_KEY"] == "kept-as-for-the-built-in-runtime"
    assert "DEEPSEEK_API_KEY" not in environment and "DEEPSEEK_BASE_URL" not in environment
    assert "dsh_permission_mode" not in environment
    assert environment["DSH_HOME"] == str(home) and environment[profile.KEY_VARIABLE] == "endpoint-token"
    # The user's own ~/.agents is not read, and what dsh unpacks or downloads stays below our directory.
    assert environment["DSH_AGENTS_HOME"] == str(home / "agents")
    assert Path(environment["DSH_RESOURCE_CACHE"]).parent == Path(environment["PKG_NATIVE_CACHE_PATH"]).parent == home.parent
    assert base["DSH_HOME"] == "somewhere-else"


def test_the_process_starts_in_a_directory_of_ours(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = profile.start_directory(tmp_path / "home")
    assert directory.is_dir() and list(directory.iterdir()) == []
    if sys.platform == "win32":
        assert directory.drive.upper() == "C:"


@pytest.mark.skipif(sys.platform != "win32", reason="the drive matters on Windows only")
@pytest.mark.parametrize(
    ("temp", "expected"),
    [
        # The user's temporary directory, where it is on drive C:.
        ("C:/Users/someone/AppData/Local/Temp", "C:/Users/someone/AppData/Local/Temp/nekoclaw-dsh"),
        # Moved to another drive (as on some machines, and on CI's): the system's own.
        ("D:/Temp", "C:/Windows/Temp/nekoclaw-dsh"),
    ],
)
def test_a_home_on_another_drive_still_starts_the_process_on_c(
    monkeypatch: pytest.MonkeyPatch, temp: str, expected: str
) -> None:
    import tempfile

    # Whatever drive this machine's temporary directory is on: the places are made up, and nothing is created.
    monkeypatch.setattr(tempfile, "gettempdir", lambda: temp)
    monkeypatch.setenv("SystemRoot", "C:/Windows")
    made: list[Path] = []
    monkeypatch.setattr(Path, "mkdir", lambda self, *args, **kwargs: made.append(self))

    # dsh cannot create a session when it was started on another drive.
    directory = profile.start_directory(Path("Q:/data/nekoclaw/dsh/home"))

    assert directory == Path(expected) and made == [Path(expected)]


@pytest.mark.skipif(sys.platform != "win32", reason="the drive matters on Windows only")
def test_no_directory_on_c_means_dsh_cannot_be_used(monkeypatch: pytest.MonkeyPatch) -> None:
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: "Q:/temp")
    monkeypatch.setenv("SystemRoot", "Q:/Windows")
    with pytest.raises(profile.DshUnavailable):
        profile.start_directory(Path("Q:/data/home"))


def test_the_settings_fit_together() -> None:
    from config import nekoclaw_settings as settings

    # A closing session still holds its model name while the next one starts.
    assert settings.DSH_SESSION_SLOTS > settings.TASK_AGENT_MAX_CONCURRENT
    # dsh ends a call of a handed service's tool after 60 seconds.
    assert settings.DSH_HOST_TOOL_SECONDS < 60
    assert set(settings.DSH_WHEELS) == {"win-x64", "macos-arm64", "macos-x64", "linux-x64", "linux-arm64"}
    for wheel in settings.DSH_WHEELS.values():
        assert re.fullmatch(r"[0-9a-f]{64}", str(wheel["sha256"]))
        assert str(wheel["file"]).startswith(f"deepseek_harness_runtime_bin-{settings.DSH_VERSION}-") and str(wheel["file"]).endswith(".whl")
        assert re.fullmatch(r"[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{60}", str(wheel["path"]))
        assert 50_000_000 < int(wheel["size"]) < 200_000_000
    assert all(host.startswith("https://") for host in settings.DSH_DOWNLOAD_HOSTS)


def test_the_host_looks_for_the_note_under_the_name_the_driver_writes_it() -> None:
    from nekoclaw.host.paths import DSH_SESSION_NAME

    assert DSH_SESSION_NAME == profile.SESSION_FILE


def test_knowing_where_dsh_is_installed_does_not_bring_the_driver_in() -> None:
    """The host makes a ``DshInstallation`` when it starts, for every user; the driver is for those who use dsh."""
    import subprocess
    import sys

    code = (
        "import sys\n"
        "import nekoclaw.dsh.install\n"
        "from nekoclaw.dsh import DshInstallation, DshLaunch, DshUnavailable\n"
        "heavy = [name for name in ('nekoclaw.dsh.driver', 'nekoclaw.dsh.forward', 'nekoclaw.dsh.endpoint',"
        " 'nekoclaw.dsh.host_tools', 'nekoclaw.dsh.acp', 'nekoclaw.runtime', 'httpx', 'uvicorn', 'pydantic')"
        " if name in sys.modules]\n"
        "print(','.join(heavy))\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == ""
