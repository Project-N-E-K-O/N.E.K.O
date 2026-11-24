# /// script
# requires-python = ">=3.10"
# dependencies = ["psutil>=5.9", "playwright>=1.45", "matplotlib>=3.7"]
# ///
"""N.E.K.O plugin-system performance baseline (requirement #326). Not for merging.

Cross-platform (Windows / Linux / macOS) entry point. Run from the repository root:

    uv run perf-baseline/bench.py <command> [options]

Commands (see README.md for the full workflow):
    setup       install Playwright's Chromium for this script's environment (once)
    env         record machine / toolchain / commit information
    boot        plugin server start-up (standalone), cold / warm, per-phase timing
    importtime  python -X importtime of the plugin server
    stack       full backend (launcher.py) start-up, browser-mode stack
    panel       plugin manager panel: open / switch / fps (browser or desktop shell via CDP)
    mem         memory scenario: server + plugin processes + panel page over phases
    soak        repeated install/uninstall, server RSS after each cycle
    tracemalloc Python allocation growth across install cycles and API rounds
    analyze     aggregate everything under --out into summary.json, results.md and charts
    all         the default suite (everything above with README defaults)

Every process started here is stopped by PID (psutil), never by name matching.
All N.E.K.O processes run with an isolated data root (see isolation/sitecustomize.py).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import psutil

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import linux_only  # noqa: E402

IS_WIN = sys.platform == "win32"
PLUGIN_PORT = 48916
MAIN_PORT = 48911
PLUGIN_URL = f"http://127.0.0.1:{PLUGIN_PORT}"
MAIN_URL = f"http://127.0.0.1:{MAIN_PORT}"
TIER_EXPECTED_RUNNING = {0: 0, 1: 1, 2: 11}


# ----------------------------------------------------------------------------- utilities
def log(msg: str) -> None:
    line = f"[{_dt.datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if CTX.out:
        with open(CTX.out / "bench.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


class Ctx:
    repo: Path = HERE.parent
    out: Path | None = None
    work: Path = Path(tempfile.gettempdir()) / "neko-perf"
    uv: str = "uv"
    uv_env: dict = {}


CTX = Ctx()


def wjson(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def http(method: str, url: str, *, body: dict | None = None, headers: dict | None = None, timeout: float = 10.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, OSError, TimeoutError):
        return None, b""


def running_plugins() -> int | None:
    status, raw = http("GET", f"{PLUGIN_URL}/plugins", timeout=3)
    if status != 200:
        return None
    try:
        return sum(1 for p in json.loads(raw)["plugins"] if p.get("status") == "running")
    except (ValueError, KeyError, TypeError):
        return None


def port_busy(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def require_free_ports(*ports: int) -> None:
    busy = [p for p in ports if port_busy(p)]
    if busy:
        raise SystemExit(f"Ports {busy} are in use. Quit N.E.K.O (and any previous benchmark run) first.")


def kill_tree(pid: int, timeout: float = 5.0) -> None:
    """Stop a process and all its descendants, by PID only."""
    try:
        root = psutil.Process(pid)
    except psutil.Error:
        return
    procs = [root]
    try:
        procs += root.children(recursive=True)
    except psutil.Error:
        pass
    for p in reversed(procs):
        try:
            p.terminate()
        except psutil.Error:
            pass
    alive = _wait_gone(procs, timeout)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass
    _wait_gone(alive, timeout)


def _is_alive(p) -> bool:
    try:
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def _wait_gone(procs, timeout: float):
    try:
        return psutil.wait_procs(procs, timeout=timeout)[1]
    except OSError:  # psutil's pidfd wait can fail for already-reaped PIDs; fall back to polling
        end = time.time() + timeout
        alive = list(procs)
        while alive and time.time() < end:
            alive = [p for p in alive if _is_alive(p)]
            time.sleep(0.1)
        return alive


def wait_ports_free(*ports: int, timeout: float = 15.0) -> None:
    end = time.time() + timeout
    while time.time() < end and any(port_busy(p) for p in ports):
        time.sleep(0.1)


def uv_dirs() -> dict:
    """Pin uv's own cache / interpreter dirs, so the isolated HOME/APPDATA does not make uv re-download."""
    env = {}
    for key, args in (("UV_CACHE_DIR", ["cache", "dir"]), ("UV_PYTHON_INSTALL_DIR", ["python", "dir"])):
        if os.environ.get(key):
            env[key] = os.environ[key]
            continue
        try:
            env[key] = subprocess.run([CTX.uv, *args], capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            pass
    return env


def isolated_env(root: Path, *, extra: dict | None = None) -> dict:
    """Environment for N.E.K.O child processes: every user-data location points into `root`."""
    root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    home = root / "home"
    for sub in ("home", "appdata/Roaming", "appdata/Local", "xdg/data", "xdg/config", "Documents"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    env.update({
        "HOME": str(home),
        "USERPROFILE": str(home),
        "APPDATA": str(root / "appdata" / "Roaming"),
        "LOCALAPPDATA": str(root / "appdata" / "Local"),
        "XDG_DATA_HOME": str(root / "xdg" / "data"),
        "XDG_CONFIG_HOME": str(root / "xdg" / "config"),
        "XDG_DOCUMENTS_DIR": str(root / "Documents"),
        "NEKO_PERF_ISOLATED_ROOT": str(root),
        "PYTHONPATH": os.pathsep.join([str(HERE / "isolation")] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])),
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    env.update(CTX.uv_env)
    env.pop("ELECTRON_RUN_AS_NODE", None)
    env.pop("VIRTUAL_ENV", None)  # the orchestrator's own uv environment; children use the project .venv
    if extra:
        env.update({k: v for k, v in extra.items() if v is not None})
    return env


def app_dir(root: Path) -> Path:
    """N.E.K.O data root inside the isolated root (ConfigManager candidate + app name)."""
    return root / "appdata" / "N.E.K.O"


def user_plugin_root(root: Path) -> Path:
    return app_dir(root) / ".neko-plugin-installations" / "plugins"


def popen(cmd: list[str], *, cwd: Path, env: dict, log_path: Path, stderr_path: Path | None = None) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    out = open(log_path, "ab")
    err = open(stderr_path, "ab") if stderr_path else subprocess.STDOUT
    kwargs = {}
    if IS_WIN:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(cmd, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err, **kwargs)


def server_cmd(*, wrapper: bool = True) -> list[str]:
    entry = ["plugin/user_plugin_server.py"]
    if wrapper:
        entry = [str(HERE / "perf_boot.py")] + entry
    return [CTX.uv, "run", "--no-sync", "python", *entry]


def make_fixture(root: Path, n: int, *, keep_meta: bool = False) -> None:
    """Install n renamed copies of the bundled memo_reminder plugin (auto_start = true) into the isolated user root."""
    src = CTX.repo / "plugin" / "plugins" / "memo_reminder"
    dst_root = user_plugin_root(root)
    dst_root.mkdir(parents=True, exist_ok=True)
    for i in range(1, n + 1):
        install_copy(src, dst_root, f"perf_memo_{i}", auto_start=True, keep_meta=keep_meta, display=f"PerfMemo {i}")


def install_copy(src: Path, dst_root: Path, plugin_id: str, *, auto_start: bool, keep_meta: bool = False, display: str | None = None) -> Path:
    d = dst_root / plugin_id
    shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(src, d, ignore=shutil.ignore_patterns("__pycache__"))
    if not keep_meta:
        (d / "plugin.meta.json").unlink(missing_ok=True)
    toml = d / "plugin.toml"
    text = toml.read_text(encoding="utf-8")
    src_id = re.search(r'^id = "([^"]+)"', text, re.M).group(1)
    text = re.sub(r'^id = "[^"]+"', f'id = "{plugin_id}"', text, count=1, flags=re.M)
    text = text.replace(f"plugin.plugins.{src_id}:", f"plugin.plugins.{plugin_id}:")
    text = re.sub(r"^auto_start = (true|false)", f"auto_start = {'true' if auto_start else 'false'}", text, flags=re.M)
    if display:
        text = re.sub(r'^name = "[^"]*"', f'name = "{display}"', text, count=1, flags=re.M)
    toml.write_text(text, encoding="utf-8")
    return d


def clear_pycache() -> int:
    n = 0
    for sub in ("plugin", "utils", "config", "app", "main_logic", "main_routers", "memory"):
        base = CTX.repo / sub
        if not base.exists():
            continue
        for d in list(base.rglob("__pycache__")):
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    return n


def make_cold(args) -> str:
    """Best-effort OS-level cold state. Returns a description of what was done."""
    done = [f"cleared {clear_pycache()} __pycache__ dirs", "fresh data root"]
    if args.cold_cmd:
        rc = subprocess.run(args.cold_cmd, shell=True).returncode
        done.append(f"ran --cold-cmd (rc={rc})")
    elif linux_only.can_drop_caches():
        linux_only.drop_caches()
        done.append("dropped Linux page cache")
    else:
        done.append("OS file cache NOT dropped (see README: reboot or --cold-cmd)")
    return "; ".join(done)


def tree_memory(pid: int) -> list[dict]:
    rows = []
    try:
        procs = [psutil.Process(pid)] + psutil.Process(pid).children(recursive=True)
    except psutil.Error:
        return rows
    for p in procs:
        try:
            mi = p.memory_full_info()
            rows.append({"pid": p.pid, "name": p.name(), "rss_mb": round(mi.rss / 2**20, 1), "uss_mb": round(getattr(mi, "uss", 0) / 2**20, 1),
                         "pss_mb": round(getattr(mi, "pss", 0) / 2**20, 1), "cmd": " ".join(p.cmdline())[:120]})
        except psutil.Error:
            pass
    return rows


# ----------------------------------------------------------------------------- boot
def boot_once(tier: int, mode: str, out: Path, args, *, pyspy: bool = False, extra_env: dict | None = None, fixture_kw: dict | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    require_free_ports(PLUGIN_PORT)
    root = CTX.work / f"boot-t{tier}{args.variant_tag}"
    cold_note = None
    if mode == "cold" or not root.exists():
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        if tier == 2:
            make_fixture(root, 10, **(fixture_kw or {}))
    if mode == "cold":
        cold_note = make_cold(args)
    phases = out / "phases.jsonl"
    phases.unlink(missing_ok=True)
    env = isolated_env(root, extra={"PERF_PHASE_LOG": str(phases), "NEKO_PERF_NO_BUILTIN": "1" if tier == 0 else None, **(extra_env or {})})
    cmd = server_cmd()
    if pyspy:
        exe = shutil.which("py-spy")
        if not exe:
            raise SystemExit("py-spy not found on PATH (uv tool install py-spy)")
        cmd = [exe, "record", "--subprocesses", "-r", "200", "-f", "raw", "-o", str(out / "pyspy.raw"), "--", *cmd]
    t0 = time.time_ns()
    env["PERF_T0"] = str(t0)
    proc = popen(cmd, cwd=CTX.repo, env=env, log_path=out / "server.log")
    ms = lambda: (time.time_ns() - t0) / 1e6
    health = all_running = None
    expect = TIER_EXPECTED_RUNNING[tier]
    deadline = time.time() + args.timeout
    while time.time() < deadline and proc.poll() is None:
        if health is None:
            st, _ = http("GET", f"{PLUGIN_URL}/health", timeout=1)
            if st == 200:
                health = ms()
        if health is not None:
            n = running_plugins()
            if n is not None and n >= expect:
                all_running = ms()
                break
        time.sleep(0.05)
    time.sleep(1.0)
    mem = tree_memory(proc.pid)
    # wait for background capability analysis to drain (no new analysis records for 1.5 s, max 30 s)
    last, stable, end = -1, 0, time.time() + 30
    while time.time() < end:
        c = phases.read_text(encoding="utf-8").count('"analysis') if phases.exists() else 0
        stable = stable + 1 if c == last else 0
        last = c
        if stable >= 3:
            break
        time.sleep(0.5)
    summary = {"tier": tier, "mode": mode, "health_ms": health, "all_running_ms": all_running, "cold": cold_note,
               "platform": sys.platform, "start_method": (extra_env or {}).get("PERF_MP_START", "default"), "tree_memory": mem}
    wjson(out / "summary.json", summary)
    if pyspy:
        # stop the profiled tree, then let py-spy write its output
        for child in psutil.Process(proc.pid).children():
            kill_tree(child.pid)
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            kill_tree(proc.pid)
    else:
        kill_tree(proc.pid)
    wait_ports_free(PLUGIN_PORT)
    log(f"boot tier{tier} {mode}{args.variant_tag}: health={health and round(health)}ms all_running={all_running and round(all_running)}ms")
    return summary


def cmd_boot(args) -> None:
    base = CTX.out / ("boot" + args.variant_tag)
    extra = {"PERF_MP_START": args.start_method} if args.start_method else None
    fixture_kw = {"keep_meta": args.keep_meta}
    for tier in args.tiers:
        for i in range(1, args.cold + 1):
            boot_once(tier, "cold", base / f"t{tier}-cold-{i}", args, extra_env=extra, fixture_kw=fixture_kw)
        for i in range(1, args.warm + 1):
            boot_once(tier, "warm", base / f"t{tier}-warm-{i}", args, extra_env=extra, fixture_kw=fixture_kw)
        if args.pyspy:
            for mode in ("cold", "warm"):
                boot_once(tier, mode, CTX.out / "pyspy" / f"t{tier}-{mode}{args.variant_tag}", args, pyspy=True, extra_env=extra, fixture_kw=fixture_kw)


def cmd_importtime(args) -> None:
    require_free_ports(PLUGIN_PORT)
    root = CTX.work / "importtime"
    shutil.rmtree(root, ignore_errors=True)
    make_fixture(root, 10)
    out = CTX.out / "importtime"
    out.mkdir(parents=True, exist_ok=True)
    for warmup in (True, False):  # first run warms caches, second one is recorded
        target = out / ("importtime-warmup.txt" if warmup else "importtime.txt")
        target.unlink(missing_ok=True)
        env = isolated_env(root, extra={"PYTHONPROFILEIMPORTTIME": "1", "PERF_T0": str(time.time_ns()), "PERF_PHASE_LOG": str(out / "phases.jsonl")})
        proc = popen(server_cmd(), cwd=CTX.repo, env=env, log_path=out / "server.log", stderr_path=target)
        end = time.time() + args.timeout
        while time.time() < end and http("GET", f"{PLUGIN_URL}/health", timeout=1)[0] != 200:
            time.sleep(0.1)
        time.sleep(3)
        kill_tree(proc.pid)
        wait_ports_free(PLUGIN_PORT)
    log("importtime done")


# ----------------------------------------------------------------------------- full stack (launcher.py)
class Stack:
    """launcher.py (main 48911 + memory + agent with the embedded plugin server 48916), optionally the desktop shell."""

    def __init__(self, tier: int, out: Path, args, *, electron: bool = False):
        self.tier, self.out, self.args, self.electron = tier, out, args, electron
        self.root = CTX.work / f"stack-t{tier}"
        self.procs: list[subprocess.Popen] = []
        self.xvfb = None
        self.timing: dict = {}

    def __enter__(self):
        require_free_ports(MAIN_PORT, PLUGIN_PORT)
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        if self.tier == 2:
            make_fixture(self.root, 10)
        env = isolated_env(self.root, extra={"NEKO_PERF_NO_BUILTIN": "1" if self.tier == 0 else None})
        t0 = time.time()
        self.procs.append(popen([CTX.uv, "run", "--no-sync", "python", "launcher.py"], cwd=CTX.repo, env=env, log_path=self.out / "backend.log"))
        end = time.time() + self.args.timeout
        main_ok = plug_ok = None
        while time.time() < end:
            if main_ok is None and http("GET", f"{MAIN_URL}/", timeout=1)[0] == 200:
                main_ok = time.time() - t0
            if plug_ok is None and http("GET", f"{PLUGIN_URL}/health", timeout=1)[0] == 200:
                plug_ok = time.time() - t0
            if main_ok and plug_ok:
                break
            if self.procs[0].poll() is not None:
                raise SystemExit(f"launcher.py exited early, see {self.out / 'backend.log'}")
            time.sleep(0.1)
        self.timing = {"main_ready_ms": main_ok and round(main_ok * 1000), "plugin_ready_ms": plug_ok and round(plug_ok * 1000),
                       "backend_ready_ms": round(max(main_ok or 0, plug_ok or 0) * 1000)}
        # A brand-new data root starts in "storage location selection required" mode; the panel entry answers 409 until
        # a location is chosen. Choose the current (isolated) one, as a first-run user would.
        st, raw = http("POST", f"{MAIN_URL}/api/storage/location/select", body={"selected_root": str(app_dir(self.root)), "selection_source": "current"},
                       headers={"Origin": MAIN_URL}, timeout=30)
        self.timing["storage_select_status"] = st
        if self.electron:
            self._start_shell(t0)
        wjson(self.out / "stack-timing.json", self.timing)
        return self

    def _start_shell(self, t0: float) -> None:
        pc = Path(self.args.pc_repo).resolve()
        npx = shutil.which("npx")
        if not npx:
            raise SystemExit("npx not found")
        flags = [f"--remote-debugging-port={self.args.cdp_port}"]
        env = isolated_env(self.root, extra={"NEKO_USER_DATA_DIR": str(self.root / "electron-ud")})
        if sys.platform.startswith("linux"):
            # Linux-only: the shell relaunches itself once to add X11 flags; pass them up front so CDP stays reachable.
            flags += linux_only.ELECTRON_X11_FLAGS
            if self.args.xvfb:
                self.xvfb = linux_only.start_xvfb(self.out / "xvfb.log")
                env["DISPLAY"] = linux_only.XVFB_DISPLAY
        self.procs.append(popen([npx, "electron-forge", "start", "--", *flags], cwd=pc, env=env, log_path=self.out / "electron.log"))
        end = time.time() + 180
        while time.time() < end:
            st, raw = http("GET", f"http://127.0.0.1:{self.args.cdp_port}/json/list", timeout=1)
            if st == 200 and f":{MAIN_PORT}/" in raw.decode(errors="replace"):
                self.timing["shell_pet_target_ms"] = round((time.time() - t0) * 1000)
                break
            time.sleep(0.5)
        time.sleep(8)

    def __exit__(self, *exc):
        for p in reversed(self.procs):
            kill_tree(p.pid)
        if self.xvfb:
            kill_tree(self.xvfb.pid)
        wait_ports_free(MAIN_PORT, PLUGIN_PORT)


def cmd_stack(args) -> None:
    for tier in args.tiers:
        res = []
        for i in range(1, args.reps + 1):
            out = CTX.out / "stack" / f"t{tier}-{i}"
            if args.drop_caches_stack:
                make_cold(args)
            with Stack(tier, out, args) as s:
                res.append(s.timing)
            log(f"stack tier{tier} #{i}: {s.timing}")
            time.sleep(2)
        wjson(CTX.out / "stack" / f"t{tier}-summary.json", res)


# ----------------------------------------------------------------------------- standalone server for panel / memory runs
class Standalone:
    def __init__(self, tier: int, out: Path, args, *, extra_env: dict | None = None, name: str = "serve"):
        self.tier, self.out, self.args, self.extra_env = tier, out, args, extra_env or {}
        self.root = CTX.work / f"{name}-t{tier}"
        self.proc = None

    def __enter__(self):
        require_free_ports(PLUGIN_PORT)
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        if self.tier == 2:
            make_fixture(self.root, 10)
        self.out.mkdir(parents=True, exist_ok=True)
        env = isolated_env(self.root, extra={"NEKO_PERF_NO_BUILTIN": "1" if self.tier == 0 else None, "PERF_T0": str(time.time_ns()),
                                             "PERF_PHASE_LOG": str(self.out / "server-phases.jsonl"), **self.extra_env})
        self.proc = popen(server_cmd(), cwd=CTX.repo, env=env, log_path=self.out / "server.log")
        end = time.time() + self.args.timeout
        while time.time() < end:
            n = running_plugins()
            if n is not None and n >= TIER_EXPECTED_RUNNING[self.tier]:
                break
            if self.proc.poll() is not None:
                raise SystemExit(f"plugin server exited early, see {self.out / 'server.log'}")
            time.sleep(0.1)
        time.sleep(2)
        return self

    def server_pid(self) -> int | None:
        return find_server_pid(self.proc.pid)

    def __exit__(self, *exc):
        kill_tree(self.proc.pid)
        wait_ports_free(PLUGIN_PORT)


def find_server_pid(root_pid: int, *, listen_only: bool = False) -> int | None:
    """The python process that listens on 48916 (falls back to the top-most python running user_plugin_server.py)."""
    try:
        procs = [psutil.Process(root_pid)] + psutil.Process(root_pid).children(recursive=True)
    except psutil.Error:
        return None
    for p in procs:
        try:
            if any(c.laddr and c.laddr.port == PLUGIN_PORT and c.status == psutil.CONN_LISTEN for c in p.net_connections("tcp")):
                return p.pid
        except (psutil.Error, AttributeError):
            try:
                if any(c.laddr and c.laddr.port == PLUGIN_PORT and c.status == psutil.CONN_LISTEN for c in p.connections("tcp")):
                    return p.pid
            except psutil.Error:
                pass
    if listen_only:
        return None
    for p in procs:
        try:
            if ("python" in p.name().lower() and "user_plugin_server.py" in " ".join(p.cmdline())
                    and "user_plugin_server.py" not in " ".join(p.parent().cmdline())):
                return p.pid
        except psutil.Error:
            pass
    return None


# ----------------------------------------------------------------------------- install / uninstall cycle (shared by mem, soak, tracemalloc)
def install_cycle(root: Path, i: int, *, hold_s: float, events=None) -> None:
    src = user_plugin_root(root) / "perf_memo_1"
    pid = f"perf_dyn_{i}"
    if events:
        events(f"install:{pid}")
    install_copy(src, user_plugin_root(root), pid, auto_start=False)
    http("POST", f"{PLUGIN_URL}/plugins/refresh", timeout=60)
    http("POST", f"{PLUGIN_URL}/plugin/{pid}/start", timeout=60)
    time.sleep(hold_s)
    if events:
        events(f"uninstall:{pid}")
    http("POST", f"{PLUGIN_URL}/plugin/{pid}/stop", timeout=60)
    shutil.rmtree(user_plugin_root(root) / pid, ignore_errors=True)
    http("POST", f"{PLUGIN_URL}/plugins/refresh", timeout=60)


PANEL_GET_ENDPOINTS = [
    "/plugins?locale=en-US", "/plugin/metrics", "/plugin/status", "/console/board", "/console/switches", "/console/timeline/start",
    "/console/commissions?days=7", "/console/space/projects", "/console/space/rules", "/console/mcp", "/console/runtime",
    "/market/knowledge/status", "/market/knowledge/packs", "/market/knowledge/packs/jobs", "/plugins/web_search?locale=en-US",
    "/plugin/web_search/config", "/plugin/web_search/surfaces?locale=en-US", "/plugin-consent/notices",
]


def cmd_soak(args) -> None:
    out = CTX.out / "soak"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    with Standalone(2, out, args, name="soak") as s:
        spid = s.server_pid()

        def sample(c):
            p = psutil.Process(spid)
            mi = p.memory_full_info()
            try:
                handles = p.num_handles() if IS_WIN else p.num_fds()
            except psutil.Error:
                handles = None
            rows.append({"cycle": c, "rss_mb": round(mi.rss / 2**20, 2), "uss_mb": round(mi.uss / 2**20, 2), "pss_mb": round(getattr(mi, "pss", 0) / 2**20, 2),
                         "threads": p.num_threads(), "handles_or_fds": handles})

        sample(0)
        for c in range(1, args.cycles + 1):
            install_cycle(s.root, c, hold_s=1.0)
            time.sleep(0.5)
            sample(c)
            if c % 10 == 0:
                log(f"soak cycle {c}: rss={rows[-1]['rss_mb']} MB")
    import csv
    with open(out / "soak.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def cmd_tracemalloc(args) -> None:
    out = CTX.out / "tracemalloc"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    extra = {"PERF_TRACEMALLOC": "25", "PERF_TM_DIR": str(out)}
    with Standalone(2, out, args, extra_env=extra, name="tm") as s:
        spid = s.server_pid()
        (out / "request-start").touch()  # tracing starts after start-up, so start-up is not slowed down
        time.sleep(2)
        rss = {}

        def snap(n):
            rss[n] = round(psutil.Process(spid).memory_info().rss / 2**20, 1)
            (out / f"request-{n}").touch()
            end = time.time() + 300
            while time.time() < end and not (out / f"tm-{n}.pickle").exists():
                time.sleep(0.5)

        def api_round():
            for e in PANEL_GET_ENDPOINTS:
                http("GET", PLUGIN_URL + e, timeout=10)

        install_cycle(s.root, 0, hold_s=1.0)
        api_round()
        snap(1)
        for c in range(1, args.cycles + 1):
            install_cycle(s.root, c, hold_s=1.0)
        snap(2)
        for _ in range(args.api_rounds):
            api_round()
        snap(3)
        wjson(out / "rss.json", rss)
    # compare inside the project interpreter (pickles reference the server's Python version)
    subprocess.run([CTX.uv, "run", "--no-sync", "python", str(HERE / "tm_compare.py"), str(out)], cwd=CTX.repo,
                   env={k: v for k, v in {**os.environ, **CTX.uv_env}.items() if k != "VIRTUAL_ENV"})
    log("tracemalloc done")


# ----------------------------------------------------------------------------- setup / env
def cmd_setup(args) -> None:
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    if not shutil.which("py-spy"):
        log("py-spy not on PATH: install it with `uv tool install py-spy` (needed for boot --pyspy)")
    log("setup done")


def cmd_env(args) -> None:
    def run(cmd, cwd=None):
        try:
            return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    vm = psutil.virtual_memory()
    info = {
        "platform": platform.platform(), "system": platform.system(), "release": platform.release(), "machine": platform.machine(),
        "cpu": platform.processor() or None, "logical_cpus": psutil.cpu_count(), "physical_cpus": psutil.cpu_count(logical=False),
        "memory_gb": round(vm.total / 2**30, 1), "python_orchestrator": sys.version.split()[0],
        "python_project": run([CTX.uv, "run", "--no-sync", "python", "--version"], cwd=CTX.repo),
        "uv": run([CTX.uv, "--version"]), "node": run([shutil.which("node") or "node", "--version"]),
        "py_spy": run([shutil.which("py-spy") or "py-spy", "--version"]),
        "repo_commit": run(["git", "rev-parse", "HEAD"], cwd=CTX.repo), "repo_branch": run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=CTX.repo),
        "main_commit_measured_on_linux": "f47f73729615f828da14be2d7e47016e38d57939",
    }
    info.update(linux_only.machine_details() if sys.platform.startswith("linux") else {})
    if IS_WIN:
        info["cpu_name"] = run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"])
        info["gpu"] = run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name) -join '; '"])
        info["os_caption"] = run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_OperatingSystem).Caption + ' ' + (Get-CimInstance Win32_OperatingSystem).BuildNumber"])
        info["disk"] = run(["powershell", "-NoProfile", "-Command", "(Get-PhysicalDisk | Select-Object -ExpandProperty MediaType) -join '; '"])
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = launch_browser(p, args)
            info["browser"] = f"{args.browser} {b.version}"
            b.close()
    except Exception as exc:  # noqa: BLE001
        info["browser"] = f"unavailable: {type(exc).__name__}: {exc}"[:200]
    wjson(CTX.out / "env.json", info)
    log("env: " + json.dumps(info, ensure_ascii=False))


def launch_browser(p, args):
    kw = {"args": ["--enable-precise-memory-info", "--js-flags=--expose-gc"], "headless": not args.headed}
    if args.browser in ("chrome", "msedge"):
        kw["channel"] = args.browser
    return p.chromium.launch(**kw)


# ----------------------------------------------------------------------------- panel / mem (implemented in panel.py)
def cmd_panel(args) -> None:
    import panel
    for tier in args.tiers:
        out = CTX.out / ("electron" if args.shell else "browser") / f"t{tier}"
        if args.shell:
            with Stack(tier, out, args, electron=True):
                panel.run(args, out, cdp=f"http://127.0.0.1:{args.cdp_port}")
        elif args.server == "stack" and tier != 0:
            with Stack(tier, out / "stack", args):
                panel.run(args, out, cdp=None)
        else:
            with Standalone(tier, out, args):
                panel.run(args, out, cdp=None)
        log(f"panel tier{tier} done")


def cmd_mem(args) -> None:
    import panel
    out = CTX.out / "mem"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    events_path = out / "events.jsonl"

    def event(name):
        with open(events_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": time.time(), "name": name}) + "\n")

    sampler_stop = threading.Event()
    event("server:spawn")
    root = CTX.work / "mem-t2"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    make_fixture(root, 10)
    require_free_ports(PLUGIN_PORT)
    env = isolated_env(root, extra={"PERF_T0": str(time.time_ns()), "PERF_PHASE_LOG": str(out / "server-phases.jsonl")})
    proc = popen(server_cmd(), cwd=CTX.repo, env=env, log_path=out / "server.log")
    th = threading.Thread(target=process_sampler, args=(proc.pid, out / "proc-mem.csv", out / "server.log", sampler_stop), daemon=True)
    th.start()
    try:
        end = time.time() + args.timeout
        while time.time() < end and (running_plugins() or 0) < 11:
            time.sleep(0.2)
        event("server:all-running")
        time.sleep(10)
        event("server:idle-before-panel")
        panel.run_mem(args, out, event, lambda i: install_cycle(root, i, hold_s=4.0, events=event))
        event("server:idle-after")
        time.sleep(20)
    finally:
        sampler_stop.set()
        th.join(timeout=5)
        kill_tree(proc.pid)
        event("server:stopped")
        wait_ports_free(PLUGIN_PORT)
    log("mem done")


def process_sampler(root_pid: int, csv_path: Path, server_log: Path, stop: threading.Event, interval: float = 0.5) -> None:
    import csv
    pat = re.compile(r"Plugin (\S+) process started \(pid: (\d+)\)")
    known: dict[int, psutil.Process] = {}
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["t", "pid", "role", "rss_mb", "uss_mb", "pss_mb", "threads", "cpu_pct"])
        spid = None
        while not stop.is_set():
            t = time.time()
            roles = {}
            try:
                for m in pat.finditer(server_log.read_text(encoding="utf-8", errors="replace")):
                    roles[int(m.group(2))] = "plugin:" + m.group(1)
            except OSError:
                pass
            if spid is None:
                spid = find_server_pid(root_pid, listen_only=True)
            try:
                tree = [psutil.Process(root_pid)] + psutil.Process(root_pid).children(recursive=True)
            except psutil.Error:
                break
            for p in tree:
                if p.pid not in known:
                    known[p.pid] = p
                    try:
                        p.cpu_percent(None)
                    except psutil.Error:
                        pass
                try:
                    mi = p.memory_full_info()
                    role = "server" if p.pid == spid else roles.get(p.pid, "other")
                    w.writerow([f"{t:.2f}", p.pid, role, f"{mi.rss / 2**20:.1f}", f"{getattr(mi, 'uss', 0) / 2**20:.1f}",
                                f"{getattr(mi, 'pss', 0) / 2**20:.1f}", p.num_threads(), f"{p.cpu_percent(None):.1f}"])
                except psutil.Error:
                    pass
            fh.flush()
            stop.wait(max(0.0, interval - (time.time() - t)))


# ----------------------------------------------------------------------------- analyze / all
def cmd_analyze(args) -> None:
    import analyze
    analyze.run(CTX.out, CTX.repo)


def cmd_all(args) -> None:
    steps = [
        ("env", cmd_env, {}),
        ("boot", cmd_boot, {"tiers": [0, 1, 2], "cold": args.cold, "warm": args.warm, "pyspy": True}),
        ("boot-keepmeta", cmd_boot, {"tiers": [2], "cold": 3, "warm": args.warm, "pyspy": False, "keep_meta": True, "variant_tag": "-keepmeta"}),
        ("importtime", cmd_importtime, {}),
        ("stack", cmd_stack, {"tiers": [1, 2], "reps": 5}),
        ("panel", cmd_panel, {"tiers": [0, 1, 2], "modes": ["open", "switch"], "reps": args.reps}),
        ("panel-fps", cmd_panel, {"tiers": [2], "modes": ["fps"], "reps": args.reps}),
        ("mem", cmd_mem, {}),
        ("soak", cmd_soak, {"cycles": 60}),
        ("tracemalloc", cmd_tracemalloc, {"cycles": 20, "api_rounds": 30}),
        ("analyze", cmd_analyze, {}),
    ]
    if sys.platform.startswith("linux") and args.with_spawn:
        steps.insert(3, ("boot-spawn", cmd_boot, {"tiers": [2], "cold": 3, "warm": args.warm, "pyspy": True, "start_method": "spawn", "variant_tag": "-spawn"}))
    for name, fn, overrides in steps:
        if name in args.skip:
            log(f"skip {name}")
            continue
        ns = argparse.Namespace(**{**vars(args), "keep_meta": False, "start_method": None, "variant_tag": "", **overrides})
        t = time.time()
        log(f"=== {name}")
        try:
            fn(ns)
        except SystemExit as exc:
            log(f"{name} FAILED: {exc}")
        except Exception as exc:  # noqa: BLE001 - keep the suite going, the failure is in bench.log
            log(f"{name} FAILED: {type(exc).__name__}: {exc}")
        log(f"=== {name} took {time.time() - t:.0f}s")


# ----------------------------------------------------------------------------- CLI
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["setup", "env", "boot", "importtime", "stack", "panel", "mem", "soak", "tracemalloc", "analyze", "all"])
    ap.add_argument("--repo", default=str(HERE.parent), help="N.E.K.O checkout (default: parent of perf-baseline/)")
    ap.add_argument("--out", default=str(HERE / "results"), help="output directory (default: perf-baseline/results)")
    ap.add_argument("--work", default=str(Path(tempfile.gettempdir()) / "neko-perf"), help="scratch dir for isolated data roots")
    ap.add_argument("--tiers", default="0,1,2", help="plugin tiers: 0 = none, 1 = bundled, 2 = bundled + 10 copies")
    ap.add_argument("--cold", type=int, default=5)
    ap.add_argument("--warm", type=int, default=10)
    ap.add_argument("--reps", type=int, default=10, help="repetitions for stack / panel")
    ap.add_argument("--pyspy", action="store_true", help="boot: also record one py-spy profile per tier and mode")
    ap.add_argument("--keep-meta", action="store_true", help="boot: keep plugin.meta.json in the tier-2 copies")
    ap.add_argument("--start-method", choices=["spawn", "fork", "forkserver"], help="boot: force multiprocessing start method (Linux: spawn emulates Windows)")
    ap.add_argument("--with-spawn", action="store_true", help="all: add the spawn variant (Linux only)")
    ap.add_argument("--cold-cmd", help="shell command run before each cold start to flush the OS file cache (e.g. Sysinternals RAMMap: \"RAMMap64.exe -Ew\")")
    ap.add_argument("--drop-caches-stack", action="store_true", help="stack: make every repetition cold")
    ap.add_argument("--timeout", type=float, default=180)
    ap.add_argument("--modes", default="open,switch,fps", help="panel: open,switch,fps")
    ap.add_argument("--server", choices=["stack", "standalone"], default="stack", help="panel: serve the panel from launcher.py (default) or the standalone server")
    ap.add_argument("--browser", choices=["chromium", "chrome", "msedge"], default="chromium", help="chromium = Playwright's bundled build")
    ap.add_argument("--headed", action="store_true", help="show the browser window (real GPU / vsync on a desktop)")
    ap.add_argument("--shell", action="store_true", help="panel: measure inside the N.E.K.O.-PC desktop shell (needs --pc-repo)")
    ap.add_argument("--pc-repo", default=str(HERE.parent.parent / "N.E.K.O.-PC"))
    ap.add_argument("--cdp-port", type=int, default=9222)
    ap.add_argument("--xvfb", action="store_true", help="Linux only: start Xvfb for the desktop shell")
    ap.add_argument("--cycles", type=int, default=60, help="soak / tracemalloc install-uninstall cycles")
    ap.add_argument("--api-rounds", type=int, default=30)
    ap.add_argument("--idle", type=int, default=60, help="mem: idle seconds after opening the panel")
    ap.add_argument("--switch-rounds", type=int, default=30, help="mem: rounds over all sidebar pages")
    ap.add_argument("--mem-cycles", type=int, default=10, help="mem: install/uninstall cycles")
    ap.add_argument("--skip", default="", help="all: comma-separated step names to skip")
    args = ap.parse_args()
    args.tiers = [int(x) for x in str(args.tiers).split(",") if x != ""] if isinstance(args.tiers, str) else args.tiers
    args.modes = [m for m in args.modes.split(",") if m] if isinstance(args.modes, str) else args.modes
    args.skip = [s for s in args.skip.split(",") if s]
    args.variant_tag = ("-keepmeta" if args.keep_meta else "") + (f"-{args.start_method}" if args.start_method else "")

    CTX.repo = Path(args.repo).resolve()
    CTX.out = Path(args.out).resolve()
    CTX.out.mkdir(parents=True, exist_ok=True)
    CTX.work = Path(args.work).resolve()
    CTX.work.mkdir(parents=True, exist_ok=True)
    CTX.uv = shutil.which("uv") or "uv"
    CTX.uv_env = uv_dirs()
    if not (CTX.repo / "plugin" / "user_plugin_server.py").exists():
        raise SystemExit(f"{CTX.repo} is not a N.E.K.O checkout")
    {"setup": cmd_setup, "env": cmd_env, "boot": cmd_boot, "importtime": cmd_importtime, "stack": cmd_stack, "panel": cmd_panel, "mem": cmd_mem,
     "soak": cmd_soak, "tracemalloc": cmd_tracemalloc, "analyze": cmd_analyze, "all": cmd_all}[args.command](args)


if __name__ == "__main__":
    main()
