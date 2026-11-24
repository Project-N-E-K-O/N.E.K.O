"""``neko-dsh-plugin``: how a task agent on dsh adds dsh plugins, pnpm fetched on first use.

The scripts are run for real against a registry served here, with a made-up
pnpm and a stand-in for dsh that records what it was started with. Nothing
is fetched from the network.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from nekopower.dsh import install as install_module
from nekopower.dsh import plugin_command
from nekopower.dsh import profile
from nekopower.dsh.install import INSTALLED_FILE, DshInstallation, executable_name

TAG = "win-x64" if sys.platform == "win32" else "linux-x64"
WINDOWS = sys.platform == "win32"
PROGRAM_NAME = "pnpm.exe" if WINDOWS else "pnpm"

# What the stand-in for dsh writes down: its arguments and the variables that matter.
FAKE_DSH = """
import json, os, shutil, sys
names = ("DSH_HOME", "DSH_RESOURCE_CACHE", "pnpm_config_store_dir", "pnpm_config_cache_dir",
         "pnpm_config_state_dir", "pnpm_config_registry", "pnpm_config_update_notifier")
seen = {"argv": sys.argv[1:], "env": {name: os.environ.get(name) for name in names},
        "path": os.environ.get("PATH", "").split(os.pathsep)[0], "pnpm": shutil.which("pnpm")}
here = os.path.dirname(os.path.abspath(__file__))
calls = os.path.join(here, "calls.json")
done = json.load(open(calls, encoding="utf-8")) if os.path.exists(calls) else []
json.dump([*done, seen], open(calls, "w", encoding="utf-8"))
# A registry named in failing.txt stands for one that answers but does not deliver.
failing = os.path.join(here, "failing.txt")
if os.path.exists(failing) and (os.environ.get("pnpm_config_registry") or "").startswith(open(failing).read()):
    sys.exit(9)
sys.exit(0)
"""


def _tgz(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(f"package/{name}")
            info.size = len(data)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


MAIN = _tgz({"dist/pnpm.mjs": b"// pnpm itself\n", "pnpm": b"placeholder\n", "package.json": b"{}"})
PLATFORM = _tgz({PROGRAM_NAME: b"not really pnpm\n" * 64, "package.json": b"{}"})


class Registry:
    """A registry served on the loopback address: ``files`` by path, or a status for every path."""

    def __init__(self, files: dict[str, bytes] | None = None, *, status: int = 200, slow: str = "") -> None:
        self.files = files or {}
        self.status = status
        # A path answered only after a few seconds: a download still under way.
        self.slow = slow
        self.seen: list[str] = []
        registry = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
                registry.seen.append(self.path)
                if registry.slow and self.path.endswith(registry.slow):
                    import time

                    time.sleep(30)
                data = registry.files.get(self.path, b"{}" if self.path == "/-/ping" else None)
                if registry.status != 200 or data is None:
                    self.send_response(registry.status if registry.status != 200 else 404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # A held-back answer is not waited for when the test ends.
        self.server.daemon_threads = True
        self.server.block_on_close = False
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _packages(main: bytes = MAIN, platform: bytes = PLATFORM) -> dict[str, dict[str, str | int]]:
    def entry(name: str, file: str, data: bytes) -> dict[str, str | int]:
        return {"name": name, "file": file, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}

    return {"main": entry("@pnpm/exe", "exe-1.tgz", main), TAG: entry(f"@pnpm/{TAG}", f"{TAG}-1.tgz", platform)}


def _files_of(packages: dict[str, dict[str, str | int]], main: bytes = MAIN, platform: bytes = PLATFORM) -> dict[str, bytes]:
    return {
        f"/{packages['main']['name']}/-/{packages['main']['file']}": main,
        f"/{packages[TAG]['name']}/-/{packages[TAG]['file']}": platform,
    }


@pytest.fixture
def registries(monkeypatch: pytest.MonkeyPatch):
    from config import nekopower_settings

    made: list[Registry] = []

    def use(*serving: Registry, packages: dict | None = None) -> None:
        made.extend(serving)
        monkeypatch.setattr(nekopower_settings, "DSH_PNPM_REGISTRIES", tuple(item.url for item in serving))
        monkeypatch.setattr(nekopower_settings, "DSH_PNPM_PACKAGES", packages or _packages())

    yield use
    for registry in made:
        registry.close()


def _write(tmp_path: Path) -> Path:
    stand_in = tmp_path / "fake-dsh" / "dsh.py"
    stand_in.parent.mkdir()
    stand_in.write_text(FAKE_DSH, encoding="utf-8")
    return plugin_command.write_command(
        tmp_path / "dsh", executable=Path(sys.executable), leading=(str(stand_in),), home=tmp_path / "dsh" / "home",
        tag=TAG,
    )


def _run(directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
    if WINDOWS:
        command = [str(directory / f"{plugin_command.COMMAND}.cmd"), *arguments]
    else:
        command = [str(directory / plugin_command.COMMAND), *arguments]
    return subprocess.run(command, capture_output=True, text=True, timeout=120, env=environment)


def _calls(tmp_path: Path) -> list[dict]:
    path = tmp_path / "fake-dsh" / "calls.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _called(tmp_path: Path) -> dict:
    return _calls(tmp_path)[-1]


needs_shell = pytest.mark.skipif(
    (WINDOWS and not shutil.which("powershell.exe")) or (not WINDOWS and not shutil.which("tar")),
    reason="the script's own shell is not here",
)


@needs_shell
def test_the_first_plugin_fetches_pnpm_once_and_dsh_gets_it_with_the_data_roots_directories(tmp_path, registries):
    registry = Registry(_files_of(_packages()))
    registries(registry)
    directory = _write(tmp_path)

    done = _run(directory, "add", "dsh-free-search")

    assert done.returncode == 0, done.stderr
    root = tmp_path / "dsh"
    pnpm = next((root / "pnpm").glob("*/" + PROGRAM_NAME)).parent
    assert (pnpm / PROGRAM_NAME).read_bytes() == b"not really pnpm\n" * 64
    assert (pnpm / "dist" / "pnpm.mjs").is_file()
    # Nothing half made is left; beside pnpm, which registry delivered it.
    assert sorted(item.name for item in (root / "pnpm").iterdir()) == sorted([pnpm.name, plugin_command.REGISTRY_FILE])
    assert (root / "pnpm" / plugin_command.REGISTRY_FILE).read_text(encoding="utf-8") == registry.url
    called = _called(tmp_path)
    assert called["argv"] == ["plugin", "--profile", profile.PROFILE, "add", "dsh-free-search"]
    assert Path(called["path"]) == pnpm and Path(called["pnpm"]).parent == pnpm
    environment = called["env"]
    assert environment["DSH_HOME"] == str(root / "home")
    assert environment["DSH_RESOURCE_CACHE"] == str(root / "resources")
    # pnpm keeps nothing in the user's own directories.
    for name in ("store", "cache", "state"):
        assert environment[f"pnpm_config_{name}_dir"] == str(root / "pnpm" / name)
    assert environment["pnpm_config_registry"] == registry.url + "/"
    assert environment["pnpm_config_update_notifier"] == "false"
    fetched = [path for path in registry.seen if path != "/-/ping"]
    assert len(fetched) == 2

    asked = len(registry.seen)
    again = _run(directory, "list")
    assert again.returncode == 0, again.stderr
    # Fetched once; and listing asks no registry at all, so it never waits for one.
    assert len(registry.seen) == asked
    assert _called(tmp_path)["argv"][-1] == "list"
    # Given the registry that delivered, as pnpm keeps it, though that one was not asked.
    assert _called(tmp_path)["env"]["pnpm_config_registry"] == registry.url + "/"
    assert len(_calls(tmp_path)) == 2


@needs_shell
def test_a_plugin_added_from_the_mirror_is_removed_with_the_mirror_and_without_asking_it(tmp_path, registries):
    down = Registry(status=503)
    mirror = Registry(_files_of(_packages()))
    registries(down, mirror)
    directory = _write(tmp_path)
    assert _run(directory, "add", "x").returncode == 0
    asked = len(mirror.seen) + len(down.seen)

    done = _run(directory, "remove", "x")

    # pnpm would refuse another registry than its packages came from, or wait for one that does not answer.
    assert done.returncode == 0, done.stderr
    assert _called(tmp_path)["env"]["pnpm_config_registry"] == mirror.url + "/"
    assert len(mirror.seen) + len(down.seen) == asked
    # Nothing remembered (pnpm put there before): a local command names no registry.
    (tmp_path / "dsh" / "pnpm" / plugin_command.REGISTRY_FILE).unlink()
    assert _run(directory, "remove", "x").returncode == 0
    assert _called(tmp_path)["env"]["pnpm_config_registry"] is None
    # Something else written there is not taken for a registry.
    (tmp_path / "dsh" / "pnpm" / plugin_command.REGISTRY_FILE).write_text("http://elsewhere.test", encoding="utf-8")
    assert _run(directory, "remove", "x").returncode == 0
    assert _called(tmp_path)["env"]["pnpm_config_registry"] is None


@needs_shell
def test_a_registry_that_does_not_answer_is_followed_by_the_mirror(tmp_path, registries):
    down = Registry(status=503)
    mirror = Registry(_files_of(_packages()))
    registries(down, mirror)

    done = _run(_write(tmp_path), "add", "x")

    assert done.returncode == 0, done.stderr
    assert _called(tmp_path)["env"]["pnpm_config_registry"] == mirror.url + "/"


def _same_size_but_other() -> bytes:
    """The platform's file with one byte changed: only its SHA-256 tells it apart."""
    data = bytearray(PLATFORM)
    data[len(data) // 2] ^= 0xFF
    return bytes(data)


@needs_shell
@pytest.mark.parametrize(
    "tampered",
    [_tgz({PROGRAM_NAME: b"something else\n", "package.json": b"{}"}), _same_size_but_other()],
    ids=["other-size", "same-size"],
)
def test_a_file_that_is_not_the_one_recorded_is_never_used(tmp_path, registries, tampered):
    packages = _packages()
    first = Registry(_files_of(packages, platform=tampered))
    mirror = Registry(_files_of(packages))
    registries(first, mirror, packages=packages)

    done = _run(_write(tmp_path), "add", "x")

    # The tampered file was thrown away and the mirror's taken.
    assert done.returncode == 0, done.stderr
    pnpm = next((tmp_path / "dsh" / "pnpm").glob("*/" + PROGRAM_NAME))
    assert pnpm.read_bytes() == b"not really pnpm\n" * 64
    # The first registry answered but did not deliver: the plugin comes from the one that did.
    assert [call["env"]["pnpm_config_registry"] for call in _calls(tmp_path)] == [mirror.url + "/"]


@needs_shell
def test_a_plugin_that_one_registry_does_not_deliver_is_fetched_from_the_next(tmp_path, registries):
    first = Registry(_files_of(_packages()))
    mirror = Registry(_files_of(_packages()))
    registries(first, mirror)
    directory = _write(tmp_path)
    (tmp_path / "fake-dsh" / "failing.txt").write_text(first.url, encoding="utf-8")

    done = _run(directory, "add", "x")

    assert done.returncode == 0, done.stderr
    tried = [call["env"]["pnpm_config_registry"] for call in _calls(tmp_path)]
    assert tried == [first.url + "/", mirror.url + "/"]
    # When all fail, the answer of the last is handed on; a local command is never tried twice.
    (tmp_path / "fake-dsh" / "failing.txt").write_text("http://127.0.0.1", encoding="utf-8")
    assert _run(directory, "add", "x").returncode == 9
    assert len(_calls(tmp_path)) == 4
    (tmp_path / "fake-dsh" / "calls.json").unlink()
    (tmp_path / "fake-dsh" / "failing.txt").unlink()
    assert _run(directory, "remove", "x").returncode == 0
    assert len(_calls(tmp_path)) == 1
    # The registry remembered is the last that delivered: the mirror, which did the first add.
    assert _called(tmp_path)["env"]["pnpm_config_registry"] == mirror.url + "/"


@needs_shell
def test_when_no_registry_delivers_pnpm_nothing_is_run_and_nothing_left(tmp_path, registries):
    packages = _packages()
    tampered = _tgz({PROGRAM_NAME: b"something else\n", "package.json": b"{}"})
    registries(Registry(_files_of(packages, platform=tampered)), packages=packages)

    done = _run(_write(tmp_path), "add", "x")

    assert done.returncode == 1
    assert "neko-dsh-plugin" in done.stderr
    assert _calls(tmp_path) == []
    left = tmp_path / "dsh" / "pnpm"
    assert not left.exists() or list(left.iterdir()) == []


def test_a_platform_pnpm_is_not_built_for_says_so(tmp_path):
    for windows in (True, False):
        directory = plugin_command.write_command(
            tmp_path / ("w" if windows else "p"), executable=Path("dsh"), leading=(), home=tmp_path / "home",
            tag="macos-x64", windows=windows,
        )
        text = _text(directory)
        assert "not built for this computer" in text
        assert "@pnpm/" not in text


def test_paths_with_quotes_are_kept_whole_in_both_scripts(tmp_path):
    odd = tmp_path / "it's here"
    for windows in (True, False):
        directory = plugin_command.write_command(
            odd / ("w" if windows else "p"), executable=odd / "dsh", leading=(), home=odd / "home", tag=TAG,
            windows=windows,
        )
        text = _text(directory)
        quoted = "''" if windows else "'\\''"
        assert f"it{quoted}s here" in text


def test_the_command_is_written_when_dsh_starts_and_put_in_front_of_its_path(tmp_path, monkeypatch):
    monkeypatch.setattr(install_module, "platform_tag", lambda: TAG)
    dsh = DshInstallation(tmp_path / "dsh")
    runtime = dsh.runtime_dir()
    runtime.mkdir(parents=True)
    (runtime / executable_name(TAG)).write_bytes(b"dsh")
    (runtime / INSTALLED_FILE).write_text("{}", encoding="utf-8")

    launch = dsh.launch()

    assert launch.path == (tmp_path / "dsh" / "bin",)
    written = _stamps(launch.path[0])
    assert written and all(Path(name).name.startswith(plugin_command.COMMAND) for name in written)
    text = _text(launch.path[0])
    assert str(runtime / executable_name(TAG)) in text and str(tmp_path / "dsh" / "home") in text
    # Started again: the same text is not written again.
    dsh.launch()
    assert _stamps(launch.path[0]) == written

    environment = profile.child_environment({"Path": "C:/somewhere"}, home=launch.home, token="t", path=launch.path)
    assert environment["Path"] == os.pathsep.join([str(launch.path[0]), "C:/somewhere"])
    assert "PATH" not in environment
    assert profile.child_environment({}, home=launch.home, token="t", path=launch.path)["PATH"] == str(launch.path[0])


def _text(directory: Path) -> str:
    return "".join(path.read_text(encoding="utf-8-sig") for path in sorted(directory.rglob("*")) if path.is_file())


def _stamps(directory: Path) -> dict[str, int]:
    return {str(path.relative_to(directory)): path.stat().st_mtime_ns for path in directory.rglob("*") if path.is_file()}


def test_on_the_path_there_is_the_cmd_alone_so_powershell_does_not_run_the_script_by_its_policy(tmp_path):
    directory = plugin_command.write_command(
        tmp_path / "dsh", executable=Path("dsh.exe"), leading=(), home=tmp_path / "dsh" / "home", tag="win-x64",
        windows=True,
    )

    # PowerShell picks a .ps1 on the PATH before the .cmd of the same name, and a .ps1 runs only
    # when the execution policy lets it; the .cmd runs it with the policy stepped around.
    assert sorted(path.name for path in directory.iterdir() if path.is_file()) == [f"{plugin_command.COMMAND}.cmd"]
    script = directory / plugin_command.SCRIPT_DIR / f"{plugin_command.COMMAND}.ps1"
    assert script.is_file()
    cmd = (directory / f"{plugin_command.COMMAND}.cmd").read_text(encoding="ascii")
    assert f'-ExecutionPolicy Bypass -File "%~dp0{plugin_command.SCRIPT_DIR}' + chr(92) + f'{plugin_command.COMMAND}.ps1"' in cmd


@pytest.mark.skipif(not (WINDOWS and shutil.which("powershell.exe")), reason="Windows PowerShell only")
def test_powershell_that_runs_no_scripts_still_runs_the_command_and_keeps_its_variables(tmp_path, registries):
    registries(Registry(_files_of(_packages())))
    directory = _write(tmp_path)
    # As a task agent's shell would call it by its name, with a policy that runs no script.
    line = (f"$env:PATH = '{directory};' + $env:PATH; neko-dsh-plugin add x; "
            "Write-Output \"code=$LASTEXITCODE home=$env:DSH_HOME\"")
    done = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Restricted", "-Command", line],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"},
    )

    assert "code=0 home=" in done.stdout, done.stdout + done.stderr
    assert "home=\n" in done.stdout or done.stdout.rstrip().endswith("home=")  # nothing set in the caller's shell
    assert _called(tmp_path)["argv"][-2:] == ["add", "x"]


def test_a_command_that_cannot_be_written_again_leaves_the_last_one_on_the_path(tmp_path, monkeypatch):
    monkeypatch.setattr(install_module, "platform_tag", lambda: TAG)
    dsh = DshInstallation(tmp_path / "dsh")
    runtime = dsh.runtime_dir()
    runtime.mkdir(parents=True)
    (runtime / executable_name(TAG)).write_bytes(b"dsh")
    (runtime / INSTALLED_FILE).write_text("{}", encoding="utf-8")

    written = plugin_command.write_command
    root = tmp_path / "dsh"

    def refuse(*args, **kwargs):
        raise PermissionError("in use")

    monkeypatch.setattr(plugin_command, "write_command", refuse)
    # Never written: nothing to put on the PATH.
    assert dsh.launch().path == ()
    # Written at an earlier start for another dsh (a version since replaced): not used either.
    written(root, executable=root / "runtime" / "0.1" / executable_name(TAG), leading=(), home=dsh.home(), tag=TAG)
    assert dsh.launch().path == ()
    # Written for this one: it serves.
    written(root, executable=runtime / executable_name(TAG), leading=(), home=dsh.home(), tag=TAG)
    assert dsh.launch().path == (root / "bin",)


@needs_shell
def test_a_dsh_that_is_not_there_is_no_success(tmp_path, registries):
    registries(Registry(_files_of(_packages())))
    directory = plugin_command.write_command(
        tmp_path / "dsh", executable=tmp_path / "gone" / "dsh", leading=(), home=tmp_path / "dsh" / "home", tag=TAG,
    )

    assert _run(directory, "add", "x").returncode != 0


@needs_shell
def test_what_a_run_killed_outright_left_is_removed_by_the_next(tmp_path, registries):
    registries(Registry(_files_of(_packages())))
    directory = _write(tmp_path)
    pnpm = tmp_path / "dsh" / "pnpm"
    version = plugin_command_version()
    # Left by a process that is gone, and by one that still runs (this one): only the first goes.
    gone = pnpm / f"{version}.partial-999999"
    alive = pnpm / f"{version}.partial-{os.getpid()}"
    for left in (gone, alive):
        (left / "program").mkdir(parents=True)

    assert _run(directory, "add", "x").returncode == 0

    assert not gone.exists() and alive.exists()
    assert (pnpm / version / PROGRAM_NAME).is_file()


def plugin_command_version() -> str:
    from config.nekopower_settings import DSH_PNPM_VERSION

    return DSH_PNPM_VERSION


@pytest.mark.skipif(WINDOWS or not shutil.which("tar"), reason="the sh script, with POSIX signals")
def test_ending_the_script_while_it_fetches_pnpm_leaves_nothing_half_made(tmp_path, registries):
    import signal
    import time

    packages = _packages()
    registry = Registry(_files_of(packages), slow=str(packages[TAG]["file"]))
    registries(registry, packages=packages)
    directory = _write(tmp_path)
    script = subprocess.Popen([str(directory / plugin_command.COMMAND), "add", "x"],
                              env={**os.environ, "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
    deadline = time.monotonic() + 30
    while not any(path.endswith(str(packages[TAG]["file"])) for path in registry.seen) and time.monotonic() < deadline:
        time.sleep(0.05)

    started = time.monotonic()
    script.send_signal(signal.SIGTERM)

    # At once, not when the download (held back for 30 s) would be over.
    assert script.wait(timeout=20) == 143
    assert time.monotonic() - started < 10
    left = tmp_path / "dsh" / "pnpm"
    assert not left.exists() or [path.name for path in left.iterdir()] == []
    assert _calls(tmp_path) == []


@pytest.mark.skipif(WINDOWS or not shutil.which("tar"), reason="the sh script, with POSIX signals")
def test_ending_the_script_ends_dsh_too(tmp_path, registries):
    import signal
    import time

    registries(Registry(_files_of(_packages())))
    started = tmp_path / "started.pid"
    stand_in = tmp_path / "slow-dsh.py"
    stand_in.write_text(
        "import os, sys, time\n"
        f"open({str(started)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    directory = plugin_command.write_command(
        tmp_path / "dsh", executable=Path(sys.executable), leading=(str(stand_in),), home=tmp_path / "dsh" / "home",
        tag=TAG,
    )
    script = subprocess.Popen([str(directory / plugin_command.COMMAND), "add", "x"],
                              env={**os.environ, "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
    deadline = time.monotonic() + 60
    while not started.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    child = int(started.read_text())

    script.send_signal(signal.SIGTERM)
    script.wait(timeout=10)

    # dsh, waited for by the script, is ended with it instead of running on behind it.
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(child, signal.SIGKILL)
        pytest.fail("dsh kept running after the script was ended")


def test_a_file_that_cannot_be_replaced_leaves_no_temporary_file(tmp_path, monkeypatch):
    target = tmp_path / "command"

    def refuse(*args):
        raise PermissionError("in use")

    monkeypatch.setattr(plugin_command.os, "replace", refuse)
    with pytest.raises(PermissionError):
        plugin_command._write(target, "text", encoding="utf-8")
    assert list(tmp_path.iterdir()) == []


async def test_an_uninstall_removes_the_command_and_its_pnpm(tmp_path):
    root = tmp_path / "dsh"
    for name in ("bin", "pnpm/11.29.0", "pnpm/store", "home/profiles"):
        (root / name).mkdir(parents=True)

    await DshInstallation(root).uninstall()

    assert sorted(item.name for item in root.iterdir()) == ["home"]


# The same cases as the frontend's recognizer is tested with (``askWarning``).
_COMMANDS = json.loads(
    (Path(__file__).resolve().parents[3] / "frontend" / "plugin-manager" / "src" / "components" / "power"
     / "dshPluginCommands.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize(
    ("command", "adds"),
    [(command, True) for command in _COMMANDS["adds"]] + [(command, False) for command in _COMMANDS["local"]],
)
def test_which_commands_add_a_plugin(command, adds):
    assert plugin_command.adds_plugin(command) is adds


@pytest.mark.parametrize("lang", ["zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"])
def test_the_question_about_adding_a_plugin_says_what_a_plugin_may_do(lang):
    from config.nekopower_settings import QUERY_PERMISSION_ROW_MAX_TOKENS
    from config.prompts.prompts_nekopower_brain import PERMISSION_CATEGORY_TEXTS, PERMISSION_OPTION_NAMES
    from nekopower.host.reports import describe_permission, render_question
    from utils.tokenize import count_tokens

    command = "neko-dsh-plugin add dsh-free-search && echo done"
    question = {"kind": "permission", "category": "run_command", "detail": {"command": command},
                "options": ["always", "session", "once", "deny"]}
    words = PERMISSION_CATEGORY_TEXTS[lang]["dsh_plugin_add"].format(detail=command)

    assert words in render_question("add a search plugin", question, lang)
    # The whole command stays in the question: what else it does is seen.
    assert words in describe_permission(question, lang)
    # In a line of a query the answers on offer are kept whatever the words take.
    long = {**question, "detail": {"command": command + " " + "x" * 2000}}
    line = describe_permission(long, lang, max_tokens=QUERY_PERMISSION_ROW_MAX_TOKENS - 40)
    names = " / ".join(PERMISSION_OPTION_NAMES[lang].values())
    assert line.endswith(f"({names})") and count_tokens(line) <= QUERY_PERMISSION_ROW_MAX_TOKENS - 40
    # Any other command is asked about as before.
    other = render_question("t", {**question, "detail": {"command": "neko-dsh-plugin list"}}, lang)
    assert PERMISSION_CATEGORY_TEXTS[lang]["run_command"].format(detail="neko-dsh-plugin list") in other


def test_a_task_agent_on_dsh_is_told_how_to_add_a_plugin_and_what_to_say_first():
    from config.prompts.prompts_nekopower_dsh import DSH_HOST_TOOLS_SECTION
    from utils.tokenize import count_tokens

    assert f"`{plugin_command.COMMAND} add <package>`" in DSH_HOST_TOOLS_SECTION
    assert "no sandbox" in DSH_HOST_TOOLS_SECTION and "agree" in DSH_HOST_TOOLS_SECTION
    assert "{{" not in DSH_HOST_TOOLS_SECTION
    paragraph = DSH_HOST_TOOLS_SECTION[DSH_HOST_TOOLS_SECTION.index("To add a dsh plugin"):]
    # In every session's system prompt: kept short.
    assert count_tokens(paragraph) < 90
