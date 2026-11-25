"""The plugin process's system.* audit hook (plugin/core/system_audit.py).

In-process tests call ``SystemAuditor.hook`` directly (an audit hook cannot be
removed, so none is installed in the test process); who made a use is read
from the stack, so plugin code is simulated by a module loaded from a
temporary plugin directory. The real hook runs in a subprocess.
Only temporary directories and loopback addresses are used.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from plugin.core import system_audit
from plugin.core.system_audit import (
    FS_READ,
    FS_WRITE,
    NETWORK_CONNECT,
    PROCESS_SPAWN,
    SystemAuditor,
    program_name,
    redact_path,
)
from plugin.sdk.shared.models.exceptions import SystemPermissionDeniedError
from plugin.sdk.shared.permissions.runtime import PluginPermissions

pytestmark = pytest.mark.plugin_unit

REPO_ROOT = Path(__file__).resolve().parents[4]

PLUGIN_CODE = '''
def call(hook, event, args):
    hook(event, args)
'''

OTHER_CODE = '''
def call(hook, event, args):
    hook(event, args)
'''

# Stands in for SDK / host code: a ``plugin.*`` module under the project root.
HOST_CODE = '''
def call(hook, event, args):
    hook(event, args)

def dispatch(handler, *args):
    return handler(*args)
'''


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(granted=(), *, enforced=True):
    states = {}
    for permission in (NETWORK_CONNECT, PROCESS_SPAWN, FS_READ, FS_WRITE):
        status = "granted" if permission in granted else "undecided"
        states[permission] = {"status": status, "background": False, "enforced": enforced}
    return {"revision": 1, "states": states}


class Env:
    def __init__(self, tmp_path: Path, granted=(), *, enforced=True):
        self.plugin_dir = tmp_path / "plugins" / "demo"
        self.plugin_dir.mkdir(parents=True)
        self.data_dir = tmp_path / "data" / "plugins" / "demo"
        self.data_dir.mkdir(parents=True)
        self.outside = tmp_path / "outside"
        self.outside.mkdir()
        (self.plugin_dir / "plug.py").write_text(PLUGIN_CODE, encoding="utf-8")
        (tmp_path / "other.py").write_text(OTHER_CODE, encoding="utf-8")
        (tmp_path / "plugin").mkdir()
        (tmp_path / "plugin" / "fake_host.py").write_text(HOST_CODE, encoding="utf-8")
        self.plugin = _load(self.plugin_dir / "plug.py", f"fake_plugin_{id(self)}")
        self.other = _load(tmp_path / "other.py", f"fake_other_{id(self)}")
        self.host = _load(tmp_path / "plugin" / "fake_host.py", f"plugin.fake_host_{id(self)}")
        self.permissions = PluginPermissions()
        self.permissions.apply(_snapshot(granted, enforced=enforced))
        self.sent: list[dict] = []
        self.auditor = SystemAuditor(
            plugin_dir=self.plugin_dir,
            exempt_dirs=[self.data_dir],
            project_root=tmp_path,
            permissions=self.permissions,
            sender=self.sent.append,
            home=str(tmp_path / "home"),
        )

    def from_plugin(self, event, args):
        self.plugin.call(self.auditor.hook, event, args)

    def from_host(self, event, args):
        self.host.call(self.auditor.hook, event, args)

    def records(self):
        return list(self.auditor._queue)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


# ---------------------------------------------------------------- recording


def test_open_outside_data_dir_is_a_read_or_write(env):
    path = str(env.outside / "notes.txt")
    env.from_plugin("open", (path, "r", os.O_RDONLY))
    env.from_plugin("open", (path, "w", os.O_WRONLY | os.O_CREAT | os.O_TRUNC))
    read, write = env.records()
    assert (read["permission"], write["permission"]) == (FS_READ, FS_WRITE)
    assert read["event"] == "open" and read["verdict"] == "would_deny" and read["reason"] == "undecided"
    assert read["enforcing"] is False and read["flags"] == []
    # The file name is never recorded, only its extension.
    assert read["target"].endswith("/outside/*.txt") and "notes" not in read["target"]


def test_os_open_mode_none_uses_flags(env):
    env.from_plugin("open", (str(env.outside / "a"), None, os.O_RDWR))
    (record,) = env.records()
    assert record["permission"] == FS_WRITE


@pytest.mark.parametrize(
    ("event", "args", "permission"),
    [
        ("os.listdir", ("{o}",), FS_READ),
        ("os.scandir", ("{o}",), FS_READ),
        ("os.remove", ("{o}/x.log", -1), FS_WRITE),
        ("os.mkdir", ("{o}/new", 0o777, -1), FS_WRITE),
        ("os.rmdir", ("{o}/new", -1), FS_WRITE),
        ("os.chmod", ("{o}/x.log", 0o600, -1), FS_WRITE),
        ("shutil.rmtree", ("{o}/tree", None), FS_WRITE),
    ],
)
def test_file_events(env, event, args, permission):
    args = tuple(arg.format(o=env.outside) if isinstance(arg, str) else arg for arg in args)
    env.from_plugin(event, args)
    (record,) = env.records()
    assert record["permission"] == permission and record["event"] == event


def test_rename_records_both_ends_outside(env):
    env.from_plugin("os.rename", (str(env.data_dir / "a.txt"), str(env.outside / "b.txt"), -1, -1))
    (record,) = env.records()  # the source is in the data directory
    assert record["permission"] == FS_WRITE and record["target"].endswith("outside/*.txt")


@pytest.mark.parametrize(
    ("event", "args", "program"),
    [
        ("subprocess.Popen", ("/usr/bin/git", ["git", "status"], None, None), "git"),
        ("subprocess.Popen", (None, ["ffmpeg", "-i", "secret.mp4"], None, None), "ffmpeg"),
        ("subprocess.Popen", (None, "curl https://example.invalid/?token=x", None, None), "curl"),
        ("os.system", (b"rm -rf /tmp/x",), "rm"),
        ("os.exec", ("/bin/sh", ["sh", "-c", "x"], None), "sh"),
        ("os.spawn", (os.P_NOWAIT if hasattr(os, "P_NOWAIT") else 1, "/bin/ls", ["ls"], None), "ls"),
        ("os.posix_spawn", ("/bin/true", ["true"], None), "true"),
        ("os.fork", (), "<fork>"),
        ("_winapi.CreateProcess", (None, '"C:\\Program Files\\App\\app.exe" --x', None), "app.exe"),
    ],
)
def test_spawn_records_the_program_name_only(env, event, args, program):
    env.from_plugin(event, args)
    (record,) = env.records()
    assert record["permission"] == PROCESS_SPAWN and record["target"] == program


def test_connect_uses_the_host_name_resolved_before(env):
    env.from_plugin("socket.getaddrinfo", ("example.invalid", 443, 0, 1, 0))
    sock = socket.socket()
    try:
        env.from_plugin("socket.connect", (sock, ("192.0.2.10", 443)))
        env.from_plugin("socket.connect", (sock, ("127.0.0.1", 8080)))
    finally:
        sock.close()
    named, loopback = env.records()
    assert named["permission"] == NETWORK_CONNECT and named["target"] == "example.invalid:443"
    assert loopback["target"] == "127.0.0.1:8080" and loopback["flags"] == ["loopback"]


def test_connect_from_another_thread_pairs_the_lookup_by_port(env):
    # asyncio resolves in an executor thread and connects on the loop thread.
    worker = threading.Thread(target=env.from_plugin, args=("socket.getaddrinfo", ("api.example.invalid", "https", 0, 1, 0)))
    worker.start()
    worker.join()
    env.from_plugin("socket.connect", (None, ("192.0.2.1", 443)))
    env.from_plugin("socket.connect", (None, ("192.0.2.2", 80)))
    paired, unpaired = env.records()
    assert paired["target"] == "api.example.invalid:443"
    assert unpaired["target"] == "192.0.2.2:80"


def test_unix_socket_and_ipv6(env):
    env.from_plugin("socket.connect", (None, "/run/app.sock"))
    env.from_plugin("socket.sendto", (None, ("::1", 53, 0, 0)))
    (record,) = env.records()
    assert record["target"] == "[::1]:53" and record["flags"] == ["loopback"]


def test_repeats_are_recorded_once_a_minute(env):
    for _ in range(5):
        env.from_plugin("open", (str(env.outside / "a.txt"), "r", 0))
    assert len(env.records()) == 1


# ---------------------------------------------------------------- exemptions


def test_plugin_dir_data_dir_and_interpreter_are_exempt(env):
    import json as stdlib_module

    for path in (env.plugin_dir / "config.json", env.data_dir / "state.db"):
        env.from_plugin("open", (str(path), "r", 0))
        env.from_plugin("open", (str(path), "w", os.O_WRONLY))
    for path in (Path(stdlib_module.__file__), Path(pytest.__file__)):  # stdlib, site-packages
        env.from_plugin("open", (str(path), "r", 0))
    env.from_plugin("open", (3, "r", 0))  # an fd
    assert env.records() == []
    # Writing into the interpreter's directories is recorded.
    env.from_plugin("open", (str(Path(pytest.__file__).parent / "x.pth"), "w", os.O_WRONLY))
    assert [r["permission"] for r in env.records()] == [FS_WRITE]


def test_standard_library_data_files_are_exempt_for_reading_only(env):
    import mimetypes
    import zoneinfo

    for path in (*zoneinfo.TZPATH[:1], *mimetypes.knownfiles[:1]):
        target = os.path.join(path, "Europe", "Berlin") if path in zoneinfo.TZPATH else path
        env.from_plugin("open", (target, "r", 0))
    assert env.records() == []
    env.from_plugin("open", (mimetypes.knownfiles[0], "w", os.O_WRONLY))
    assert [r["permission"] for r in env.records()] == [FS_WRITE]


def test_host_code_is_exempt(env):
    # e.g. the SDK's reply channel or model client connecting.
    env.from_host("socket.connect", (None, ("192.0.2.1", 443)))
    env.from_host("open", (str(env.outside / "a.txt"), "r", 0))
    assert env.records() == []


def test_plugin_frame_wins_over_host_frames_below_it(env):
    # Plugin code called by the SDK (every handler) is still plugin code.
    env.host.dispatch(env.from_plugin, "open", (str(env.outside / "a.txt"), "r", 0))
    assert len(env.records()) == 1


def test_library_frames_are_transparent_and_unknown_is_flagged(env):
    # other.py is neither plugin nor host code; on a fresh thread nothing else is.
    worker = threading.Thread(target=env.other.call, args=(env.auditor.hook, "open", (str(env.outside / "a"), "r", 0)))
    worker.start()
    worker.join()
    (record,) = env.records()
    assert record["flags"] == ["unattributed"]


def test_exec_code_is_judged_by_its_module_not_a_shared_file_name(env):
    # Every exec()/eval() frame is "<string>": host code compiled that way
    # (dataclass __init__ and the like) must not make plugin exec() code exempt.
    hook = env.auditor.hook
    exec(compile("hook('open', (path, 'r', 0))", "<string>", "exec"),
         {"__name__": "plugin.sdk.generated", "hook": hook, "path": str(env.outside / "host.txt")})
    assert env.records() == []
    code = compile("hook('open', (path, 'r', 0))", "<string>", "exec")
    namespace = {"__name__": "mine", "hook": hook, "path": str(env.outside / "mine.txt")}
    env.plugin.call(lambda *_: exec(code, namespace), None, None)
    assert len(env.records()) == 1


def test_host_lookups_do_not_name_plugin_connects(env):
    env.from_host("socket.getaddrinfo", ("internal.example.invalid", 443, 0, 1, 0))
    env.from_plugin("socket.connect", (None, ("192.0.2.9", 443)))
    (record,) = env.records()
    assert record["target"] == "192.0.2.9:443"


# ---------------------------------------------------------------- judging


def test_granted_permissions_are_allowed(tmp_path):
    env = Env(tmp_path, granted=(FS_READ,))
    env.from_plugin("open", (str(env.outside / "a.txt"), "r", 0))
    env.from_plugin("open", (str(env.outside / "a.txt"), "w", os.O_WRONLY))
    allowed, refused = env.records()
    assert allowed["verdict"] == "allow" and allowed["reason"] is None
    assert refused["verdict"] == "would_deny"


def test_master_switch_on_is_never_refused(tmp_path):
    env = Env(tmp_path, enforced=False)
    env.from_plugin("socket.connect", (None, ("192.0.2.1", 1)))
    assert env.records()[0]["verdict"] == "allow"


def test_no_snapshot_counts_as_denied(tmp_path):
    env = Env(tmp_path)
    env.auditor.set_permissions(PluginPermissions())
    env.from_plugin("socket.connect", (None, ("192.0.2.1", 1)))
    record = env.records()[0]
    assert (record["verdict"], record["reason"]) == ("would_deny", "denied")


def test_grants_pushed_later_are_used(env):
    env.permissions.apply({**_snapshot((NETWORK_CONNECT,)), "revision": 2})
    env.from_plugin("socket.connect", (None, ("192.0.2.1", 1)))
    assert env.records()[0]["verdict"] == "allow"


# ---------------------------------------------------------------- refusal switch


def test_refusal_switch_off_never_raises(env):
    assert system_audit.REFUSES_SYSTEM_USES is False
    env.from_plugin("open", (str(env.outside / "a.txt"), "w", os.O_WRONLY))
    assert env.records()[0]["enforcing"] is False


def test_refusal_switch_on_raises_for_unauthorised_uses_only(tmp_path, monkeypatch):
    monkeypatch.setattr(system_audit, "REFUSES_SYSTEM_USES", True)
    env = Env(tmp_path, granted=(FS_READ,))
    env.from_plugin("open", (str(env.outside / "a.txt"), "r", 0))  # granted
    with pytest.raises(SystemPermissionDeniedError) as caught:
        env.from_plugin("open", (str(env.outside / "a.txt"), "w", os.O_WRONLY))
    assert isinstance(caught.value, PermissionError)
    assert caught.value.permission == FS_WRITE and caught.value.reason == "undecided"
    assert env.records()[-1]["enforcing"] is True
    # Exempt uses are never refused.
    env.from_plugin("open", (str(env.data_dir / "a.txt"), "w", os.O_WRONLY))
    env.from_host("open", (str(env.outside / "b.txt"), "w", os.O_WRONLY))


# ---------------------------------------------------------------- hook rules


def test_unattributed_uses_are_recorded_but_never_refused(env, monkeypatch):
    monkeypatch.setattr(system_audit, "REFUSES_SYSTEM_USES", True)
    raised = []

    def use():
        try:
            env.other.call(env.auditor.hook, "open", (str(env.outside / "a"), "w", os.O_WRONLY))
        except Exception as exc:
            raised.append(exc)

    worker = threading.Thread(target=use)
    worker.start()
    worker.join()
    assert raised == []
    (record,) = env.records()
    assert record["verdict"] == "would_deny" and record["enforcing"] is False
    assert env.auditor.errors == 0


def test_hook_never_sends_and_a_thread_flushes(env):
    sender_threads = []
    env.auditor._sender = lambda message: (sender_threads.append(threading.get_ident()), env.sent.append(message))
    env.from_plugin("open", (str(env.outside / "a.txt"), "r", 0))
    assert env.sent == []  # nothing crosses the process boundary from the hook
    env.auditor.start()
    env.auditor.close(timeout=5)
    (message,) = env.sent
    assert message["type"] == "SYSTEM_AUDIT" and len(message["items"]) == 1 and message["dropped"] == 0
    assert sender_threads == [env.auditor._thread.ident] != [threading.get_ident()]


def test_hook_does_not_reenter(env):
    nested = []

    class OpeningPermissions:
        def _peek(self, permission):
            # A grant lookup that itself raises an audited event.
            nested.append(permission)
            env.from_plugin("open", (str(env.outside / "nested.txt"), "r", 0))
            return None

    env.auditor.set_permissions(OpeningPermissions())
    env.from_plugin("open", (str(env.outside / "a.txt"), "r", 0))
    assert nested == [FS_READ]
    assert [r["target"].rsplit("/", 1)[-1] for r in env.records()] == ["*.txt"]


def test_sender_thread_is_never_audited(env):
    env.auditor._internal_threads.add(threading.get_ident())
    env.from_plugin("open", (str(env.outside / "a.txt"), "r", 0))
    assert env.records() == []


def test_hook_errors_are_counted_not_raised(env):
    class Broken:
        def _peek(self, permission):
            raise RuntimeError("boom")

    env.auditor.set_permissions(Broken())
    env.from_plugin("open", (str(env.outside / "a.txt"), "r", 0))
    assert env.auditor.errors == 1


def test_queue_is_bounded(env, monkeypatch):
    monkeypatch.setattr(system_audit, "QUEUE_LIMIT", 3)
    for index in range(5):
        env.from_plugin("open", (str(env.outside / f"d{index}" / "a.txt"), "r", 0))
    assert len(env.records()) == 3 and env.auditor.dropped == 2
    env.auditor.flush()
    assert env.sent[0]["dropped"] == 2 and env.auditor.dropped == 0


def test_failed_send_counts_the_batch_as_lost(env):
    def full(_message):
        raise OSError("uplink busy")

    env.auditor._sender = full
    env.from_plugin("open", (str(env.outside / "a.txt"), "r", 0))
    env.auditor.flush()
    assert env.auditor.dropped == 1 and env.records() == []


def test_deactivated_auditor_does_nothing(env):
    env.auditor.deactivate()
    env.from_plugin("open", (str(env.outside / "a.txt"), "r", 0))
    assert env.records() == []


# ---------------------------------------------------------------- helpers


@pytest.mark.parametrize(
    ("path", "directory", "expected"),
    [
        ("{home}/Documents/diary/2026/secret plans.docx", False, "~/Documents/diary/2026/*.docx"),
        ("{home}/a/b/c/d/e.txt", False, "~/a/b/…/d/*.txt"),
        ("/home/someone/x/key", False, "/home/<user>/x/*"),
        ("/etc/ssh", True, "/etc/ssh"),
        ("/srv/data/archive.tar.verylongextension", False, "/srv/data/*"),
    ],
)
def test_redact_path(tmp_path, path, directory, expected):
    home = str(tmp_path / "me")
    assert redact_path(path.format(home=home), directory=directory, home=home) == expected


def test_redact_windows_user_directory():
    if os.name != "nt":
        pytest.skip("Windows path semantics")
    assert redact_path(r"C:\Users\someone\x.txt", home=r"D:\h") == "C:/Users/<user>/*.txt"


def test_program_name():
    assert program_name(["/usr/bin/python3", "-c", "x"]) == "python3"
    assert program_name(Path("/opt/tool")) == "tool"
    assert program_name(None) == "<unknown>"
    assert program_name("") == "<unknown>"


# ---------------------------------------------------------------- real hook

DRIVER = r'''
import json, os, socket, subprocess, sys, threading
sys.path.insert(0, {repo!r})
from plugin.core import system_audit
from plugin.sdk.shared.permissions.runtime import PluginPermissions

mode = sys.argv[1]
plugin_dir, data_dir, outside = {plugin_dir!r}, {data_dir!r}, {outside!r}
permissions = PluginPermissions()
granted = {{"system.fs:read"}} if mode == "refuse" else set()
permissions.apply({{"revision": 1, "states": {{
    p: {{"status": "granted" if p in granted else "undecided", "background": False, "enforced": True}}
    for p in system_audit.AUDITED_PERMISSIONS}}}})
sent = []
auditor = system_audit.SystemAuditor(plugin_dir=plugin_dir, exempt_dirs=[data_dir],
                                     project_root={repo!r}, permissions=permissions, sender=sent.append)
if mode == "refuse":
    system_audit.REFUSES_SYSTEM_USES = True
system_audit.install(auditor)
result = {{}}
if mode == "observe":
    open(os.path.join(outside, "w.txt"), "w").close()
    open(os.path.join(outside, "w.txt")).close()
    open(os.path.join(data_dir, "own.txt"), "w").close()
    os.listdir(outside)
    subprocess.run([sys.executable, "-c", "pass"], check=True)
    server = socket.socket(); server.bind(("127.0.0.1", 0)); server.listen()
    port = server.getsockname()[1]
    socket.create_connection(("localhost", port), timeout=5).close()
    server.close()
    # A module imported from a directory outside every exempt one: open_code
    # and the .pyc write happen inside importlib.
    sys.path.insert(0, {libdir!r})
    import helper_mod_for_audit
elif mode == "refuse":
    try:
        open(os.path.join(outside, "x.txt"), "w")
        result["write"] = "allowed"
    except system_audit.SystemPermissionDeniedError as exc:
        result["write"] = ["refused", exc.permission, isinstance(exc, PermissionError)]
    try:
        os.listdir(outside)
        result["read"] = "allowed"
    except PermissionError:
        result["read"] = "refused"
    open(os.path.join(data_dir, "own.txt"), "w").close()
    result["own"] = "allowed"
auditor.close(timeout=5)
result["records"] = [item for message in sent for item in message["items"]]
result["errors"] = auditor.errors
print("RESULT" + json.dumps(result))
'''


def _run_driver(tmp_path: Path, mode: str) -> dict:
    plugin_dir = tmp_path / "plugins" / "demo"
    data_dir = tmp_path / "data"
    outside = tmp_path / "outside"
    libdir = tmp_path / "libdir"
    for directory in (plugin_dir, data_dir, outside, libdir):
        directory.mkdir(parents=True, exist_ok=True)
    (libdir / "helper_mod_for_audit.py").write_text("X = 1\n", encoding="utf-8")
    driver = plugin_dir / "driver.py"
    driver.write_text(
        DRIVER.format(
            repo=str(REPO_ROOT), plugin_dir=str(plugin_dir), data_dir=str(data_dir),
            outside=str(outside), libdir=str(libdir),
        ),
        encoding="utf-8",
    )
    env = {**os.environ, "HOME": str(tmp_path / "home"), "NEKO_STORAGE_SELECTED_ROOT": str(tmp_path / "storage")}
    completed = subprocess.run(
        [sys.executable, str(driver), mode], capture_output=True, text=True, timeout=120, env=env, cwd=tmp_path,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    line = next(line for line in completed.stdout.splitlines() if line.startswith("RESULT"))
    return json.loads(line[len("RESULT"):])


def test_real_hook_records_each_kind(tmp_path):
    result = _run_driver(tmp_path, "observe")
    assert result["errors"] == 0
    seen = {(r["permission"], r["event"], r["target"].rsplit("/", 1)[-1]) for r in result["records"]}
    assert (FS_WRITE, "open", "*.txt") in seen
    assert (FS_READ, "open", "*.txt") in seen
    assert (FS_READ, "os.listdir", "outside") in seen
    assert (PROCESS_SPAWN, "subprocess.Popen", Path(sys.executable).name) in seen
    connects = [r for r in result["records"] if r["permission"] == NETWORK_CONNECT]
    assert connects and all(r["target"].startswith("localhost:") for r in connects)
    # Nothing about the data directory, the interpreter or the import of the SDK.
    assert all(
        "own" not in r["target"] and "site-packages" not in r["target"] and "libdir" not in r["target"]
        for r in result["records"]
    )
    assert all(r["verdict"] == "would_deny" and r["enforcing"] is False for r in result["records"])


def test_real_hook_refuses_when_switched_on(tmp_path):
    result = _run_driver(tmp_path, "refuse")
    assert result["write"] == ["refused", FS_WRITE, True]
    assert result["read"] == "allowed"  # granted
    assert result["own"] == "allowed"  # data directory
    assert not (tmp_path / "outside" / "x.txt").exists()
    refused = [r for r in result["records"] if r["verdict"] == "would_deny"]
    assert [r["enforcing"] for r in refused] == [True]


def test_source_explains_env_reads_are_not_covered():
    # system.env:read has no audit event; the module must say so, not pretend.
    assert "system.env:read" not in system_audit.AUDITED_PERMISSIONS
    assert "Not covered: ``system.env:read``" in system_audit.__doc__


def test_launch_wiring_exempts_plugin_storage_and_uses_the_message_uplink(tmp_path, monkeypatch):
    from plugin.core import host

    installed = []
    monkeypatch.setattr(system_audit, "install", lambda auditor: installed.append(auditor) or auditor)
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "storage"))

    class Sender:
        def __init__(self):
            self.messages = []

        def put_nowait(self, message):
            self.messages.append(message)

    sender = Sender()
    config = tmp_path / "plugins" / "demo" / "plugin.toml"
    config.parent.mkdir(parents=True)
    permissions = PluginPermissions()
    auditor = host._install_system_audit("demo", config, REPO_ROOT, permissions, sender, logger=None)
    assert auditor is installed[0] and auditor._permissions is permissions
    storage = os.path.normcase(str(tmp_path / "storage" / "plugins" / "demo"))
    assert storage in auditor._exempt_dirs
    assert os.path.normcase(str(config.parent)) in auditor._exempt_dirs
    auditor._sender({"type": "SYSTEM_AUDIT"})
    assert sender.messages == [{"type": "SYSTEM_AUDIT"}]
