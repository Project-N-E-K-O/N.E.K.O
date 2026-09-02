# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Every node-driving test must go through tests/node_harness.

Hand-rolled ``subprocess.run`` calls to node have broken this suite twice, both
times in a way that hides what actually went wrong:

* ``node -e <script>`` blows past Windows' 32767-character command line and
  raises ``WinError 206`` before node starts, so no assertion in the test runs.
* ``text=True`` without ``encoding`` encodes stdin with the host locale, so a
  harness carrying CJK passes on a UTF-8-configured machine and dies with
  ``UnicodeEncodeError`` on a stock English Windows — i.e. on every CI runner.

Both are invisible locally to whoever writes the harness.  The shared launcher
pins the temp-file form and UTF-8, so this test keeps new harnesses on it
rather than re-deriving the raw call.

Discovered by walking the AST, not from a hand-maintained file list: a list is
exactly what a new harness file would slip past.
"""

import ast
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from tests import node_harness
from tests.node_harness import (
    NodeHarnessSpawnTimeout,
    run_node_script,
    run_node_stdin,
)
from tests.repo_ast_cache import parse_source_file

TESTS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = TESTS_ROOT.parent
# The launcher itself is the one place allowed to call subprocess.run on node.
EXEMPT = {TESTS_ROOT / "node_harness.py"}


def _mentions_node(call: ast.Call) -> bool:
    """True when this subprocess.run call is driving node."""
    for node in ast.walk(call):
        if isinstance(node, ast.Name) and "node" in node.id.lower():
            return True
        if isinstance(node, ast.Attribute) and "node" in node.attr.lower():
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.lower() in {"node", "node.exe"} or node.value.lower().endswith("/node"):
                return True
    return False


# 全部 subprocess 入口，不只是 run：漏一个（比如 check_call）就等于给新
# harness 留了一条绕过这条契约、退回 node -e 的合法路径（Codex P2）。
_ENTRY_POINTS = frozenset({"run", "Popen", "check_output", "check_call", "call"})


def _subprocess_bindings(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Which names in this file resolve to subprocess.

    The module is not always spelled ``subprocess``: ``import subprocess as sp``
    and ``from subprocess import run`` are both ordinary, and matching only the
    literal ``subprocess.`` prefix leaves either one as a way around this
    contract.
    """
    module_aliases = {"subprocess"}
    direct_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    module_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                if alias.name in _ENTRY_POINTS:
                    direct_names.add(alias.asname or alias.name)
    return module_aliases, direct_names


def _subprocess_run_calls(tree: ast.AST):
    module_aliases, direct_names = _subprocess_bindings(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if (
                func.attr in _ENTRY_POINTS
                and getattr(func.value, "id", None) in module_aliases
            ):
                yield node
        elif isinstance(func, ast.Name) and func.id in direct_names:
            yield node


def test_node_harnesses_go_through_the_shared_launcher():
    offenders = []
    scanned = 0
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        if path in EXEMPT:
            continue
        try:
            tree = parse_source_file(path)
        except SyntaxError:  # pragma: no cover - a broken test file fails elsewhere
            continue
        scanned += 1
        for call in _subprocess_run_calls(tree):
            if _mentions_node(call):
                offenders.append(f"{path.relative_to(TESTS_ROOT).as_posix()}:{call.lineno}")

    assert scanned > 50, f"扫描面太小，断言已失效（只扫到 {scanned} 个文件）"
    assert not offenders, (
        "这些地方直接用 subprocess 跑 node，绕开了 tests/node_harness 的"
        f"命令行长度与 UTF-8 兜底：{offenders}"
    )


def test_unit_tests_workflow_pins_locked_pyclipper():
    """The workflow's standalone pyclipper install must track uv.lock.

    It is installed outside ``uv sync`` because the group carrying it also
    carries opencv, so nothing else keeps the two in step: an index update
    could otherwise hand an unchanged commit a different release and turn the
    workflow red. Pinning without this check just moves the drift somewhere
    nobody looks.
    """
    workflow = (REPO_ROOT / ".github" / "workflows" / "unit-tests.yml").read_text(
        encoding="utf-8"
    )
    pinned = re.search(r"uv pip install pyclipper==([\w.]+)", workflow)
    assert pinned, "unit-tests.yml 里的 pyclipper 安装必须钉版本"

    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = [p["version"] for p in lock["package"] if p["name"] == "pyclipper"]
    assert locked, "uv.lock 里找不到 pyclipper，断言已失效"
    assert pinned.group(1) == locked[0], (
        f"workflow 钉的是 {pinned.group(1)}，uv.lock 解析的是 {locked[0]}"
    )


@pytest.mark.parametrize("runner", ["run_node_script", "run_node_stdin"])
def test_shared_launcher_pins_utf8(runner):
    """Both runners must pin the encoding rather than inherit the locale."""
    source = (TESTS_ROOT / "node_harness.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == runner
    )
    body = ast.get_source_segment(source, func) or ""
    assert "_utf8(kwargs)" in body, f"{runner} 必须把 kwargs 过一遍 _utf8()"

    helper = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_utf8"
    )
    helper_body = ast.get_source_segment(source, helper) or ""
    assert '"encoding"] = "utf-8"' in helper_body, "_utf8 必须强制 encoding，而不是 setdefault"


def _node_or_skip() -> str:
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node is required for the launcher's own liveness tests")
    return node_path


# A script that finishes its work but leaves a timer armed. Node's event loop
# stays alive forever on it, which is the shape every "harness hangs" report in
# this repo has taken.
_LEAKS_A_TIMER = "setInterval(function () {}, 1000);\nprocess.stdout.write('started');\n"


@pytest.mark.parametrize("runner", [run_node_script, run_node_stdin])
def test_a_harness_that_leaks_a_timer_fails_with_the_leak_named(runner):
    """A never-settling script must die from inside node, saying what held it.

    Before the watchdog this ran out the caller's ``subprocess.run`` ceiling and
    surfaced as a bare ``TimeoutExpired`` naming only ``node.EXE`` and a temp
    file - indistinguishable from a runner that never started node at all, and
    with nothing in it to act on.
    """
    node_path = _node_or_skip()

    # 3s, not the caller-typical 10-60: this test deliberately waits out the
    # deadline, so the budget is CI time spent on purpose.
    result = runner(
        node_path, _LEAKS_A_TIMER, capture_output=True, check=False, timeout=3
    )

    assert result.returncode == node_harness._WATCHDOG_EXIT_CODE, (
        "泄漏 handle 的脚本必须由 watchdog 结束，而不是被外层 ceiling 杀掉："
        f"rc={result.returncode} stderr={result.stderr!r}"
    )
    assert "[node_harness]" in result.stderr
    assert "Timeout" in result.stderr, (
        f"诊断必须点名还占着事件循环的 handle：{result.stderr!r}"
    )
    # The script's own output is still there: the watchdog reports, it does not
    # replace what the harness was saying.
    assert result.stdout == "started"


@pytest.mark.parametrize("runner", [run_node_script, run_node_stdin])
def test_a_healthy_harness_is_untouched_by_the_watchdog(runner):
    """The dual: a script that settles must see no trace of the guard.

    Without this the watchdog could "pass" the test above by firing on
    everything, and every caller asserting ``result.stdout == "ok"`` or
    ``result.stderr == ""`` would go red.
    """
    node_path = _node_or_skip()

    result = runner(
        node_path,
        "process.stdout.write('ok');\n",
        capture_output=True,
        check=False,
        timeout=6,
    )

    assert result.returncode == 0
    assert result.stdout == "ok"
    assert result.stderr == ""


def test_the_spawn_ceiling_sits_above_the_script_deadline(monkeypatch):
    """The caller's timeout is the script's budget; the spawn gets slack on top.

    This ordering is the whole basis for telling the two failures apart. If the
    two deadlines were equal, the subprocess kill would race the watchdog and a
    hung script could still surface as an undiagnosed ``TimeoutExpired`` - and
    then get retried, which is exactly what must not happen to a real defect.
    """
    assert node_harness._SPAWN_SLACK_SECONDS > 0, "没有间隙就没有先后，分类失效"

    seen = {}

    def _fake_run(argv, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        seen["script"] = kwargs.get("input") or Path(argv[1]).read_text(encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(node_harness.subprocess, "run", _fake_run)
    run_node_script("node", "process.stdout.write('ok');", timeout=12)

    assert seen["timeout"] == 12 + node_harness._SPAWN_SLACK_SECONDS
    assert "}, 12000);" in seen["script"], (
        f"watchdog 必须按调用方的 timeout 武装，而不是别的值：{seen['script'][-400:]!r}"
    )


def test_a_caller_without_a_timeout_still_gets_a_finite_script_deadline(monkeypatch):
    """No ceiling at all used to mean "hang until the 25-minute job cap"."""
    seen = {}

    def _fake_run(argv, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        seen["script"] = Path(argv[1]).read_text(encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(node_harness.subprocess, "run", _fake_run)
    run_node_script("node", "process.stdout.write('ok');")

    assert seen["timeout"] == (
        node_harness._DEFAULT_WATCHDOG_SECONDS + node_harness._SPAWN_SLACK_SECONDS
    ), "没给 timeout 的调用方以前连外层 ceiling 都没有，同步卡死能一路跑到 job cap"
    millis = int(node_harness._DEFAULT_WATCHDOG_SECONDS * 1000)
    assert f"}}, {millis});" in seen["script"]


def test_a_spawn_that_stalls_once_is_retried():
    """A stall with the script never reached is the runner's fault, not ours.

    ``node.EXE`` has come back from a Windows runner having burned 30s without
    reaching the first line of a 55ms script. Nothing in the harness can make
    that attempt succeed, and no assertion was under test, so the run is
    repeated rather than reported as a contract violation.
    """
    calls = []
    ok = subprocess.CompletedProcess(["node"], 0, "ok", "")

    def _fake_run(argv, **kwargs):
        calls.append(argv)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
        return ok

    original = node_harness.subprocess.run
    node_harness.subprocess.run = _fake_run
    try:
        result = run_node_script("node", "process.stdout.write('ok');", timeout=3)
    finally:
        node_harness.subprocess.run = original

    assert result is ok
    assert len(calls) == 2, "第一次 spawn 卡死后必须再试一次"
    assert calls[0][1] != calls[1][1], "重试要用新的临时脚本，别继承上一次被 kill 时的残留"


def test_a_spawn_that_keeps_stalling_reports_both_attempts():
    """The dual: retrying must not become a way to hide a reproducible stall.

    A stall only earns a retry when it was completely silent, so a two-attempt
    run is by construction two silent attempts - and the error has to say that
    twice over rather than collapsing it into one line.
    """
    calls = []

    def _fake_run(argv, **kwargs):
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"), output="", stderr="")

    original = node_harness.subprocess.run
    node_harness.subprocess.run = _fake_run
    try:
        with pytest.raises(subprocess.TimeoutExpired) as excinfo:
            run_node_script("node", "process.stdout.write('ok');", timeout=3)
    finally:
        node_harness.subprocess.run = original

    assert len(calls) == 2, "重试次数必须有界"
    assert isinstance(excinfo.value, NodeHarnessSpawnTimeout)
    message = str(excinfo.value)
    assert "attempt 1:" in message and "attempt 2:" in message, (
        f"两次尝试都得各自列出来：{message}"
    )
    assert message.count("stdout=") == 2 and message.count("stderr=") == 2, (
        f"每次尝试各自吐了什么必须跟着错误一起报出来：{message}"
    )


# Three suites drive fake clocks by shadowing the timer globals at module scope.
# `const` makes that shadow cover the whole module, temporal dead zone included,
# so an appended guard reading the bare name gets the fake or throws outright.
_SHADOWS_THE_TIMERS = (
    "const setTimeout = (callback) => { callback(); return 0; };\n"
    "const clearTimeout = () => {};\n"
    "const setInterval = (callback) => { callback(); return 0; };\n"
    "setTimeout(function () {});\n"
    "process.stdout.write('ok');\n"
)


# The other door to the same problem: a harness that aliases window onto the
# global object (four files do) turns `window.setTimeout = fake` into an
# overwrite of the real global, which a `globalThis.setTimeout` watchdog would
# happily pick up.
_OVERWRITES_THE_GLOBAL_TIMER = (
    "globalThis.window = globalThis;\n"
    "window.setTimeout = (callback) => { callback(); return 0; };\n"
    "window.setInterval = (callback) => { callback(); return 0; };\n"
    "setTimeout(function () {});\n"
    "process.stdout.write('ok');\n"
)


@pytest.mark.parametrize("runner", [run_node_script, run_node_stdin])
def test_a_harness_that_overwrites_the_global_timer_still_runs_clean(runner):
    """`global.window = global` makes `window.setTimeout = fake` a real overwrite.

    Reading the timer off ``globalThis`` survives a module-scope ``const``
    shadow but not this, so the watchdog takes it from ``node:timers`` instead -
    behind both doors.
    """
    node_path = _node_or_skip()

    result = runner(
        node_path,
        _OVERWRITES_THE_GLOBAL_TIMER,
        capture_output=True,
        check=False,
        timeout=6,
    )

    assert result.returncode == 0, (
        f"被改写掉的全局 setTimeout 不能牵动 watchdog：stderr={result.stderr!r}"
    )
    assert result.stdout == "ok"
    assert result.stderr == ""


@pytest.mark.parametrize("runner", [run_node_script, run_node_stdin])
def test_a_harness_that_shadows_the_timers_still_runs_clean(runner):
    """The guard must not be steerable by the script it is guarding.

    Found the hard way: the first version called the bare ``setTimeout`` and
    ``tests/unit/test_avatar_annotation_frontend.py`` -- whose harness replaces
    it with ``(callback) => callback()`` -- ran the watchdog's own callback on
    the spot and died at exit 87 with nothing wrong with it.
    """
    node_path = _node_or_skip()

    result = runner(
        node_path, _SHADOWS_THE_TIMERS, capture_output=True, check=False, timeout=6
    )

    assert result.returncode == 0, (
        f"被 harness 影子化的 setTimeout 不能牵动 watchdog：stderr={result.stderr!r}"
    )
    assert result.stdout == "ok"
    assert result.stderr == ""


# A script the watchdog cannot save: a synchronous block never yields, so the
# timer it is queued behind can never run. What separates it from a node that
# never started is that anything written first still reaches the parent.
_BLOCKS_THE_EVENT_LOOP = (
    "process.stdout.write('started');\n"
    "while (true) {}\n"
)


def test_a_synchronously_blocked_script_is_reported_not_retried():
    """The one in-script hang the watchdog cannot catch must still not be retried.

    ``while (true) {}`` never yields, so the watchdog's own timer never runs and
    the stall reaches the outer ceiling looking exactly like a spawn stall. It
    is not one, and a second spawn cannot help. Measured on Windows: what the
    script wrote before blocking still arrives, so the output is the tell.
    """
    node_path = _node_or_skip()

    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        # 1s, so the run costs one ceiling (1 + slack) rather than two.
        run_node_script(
            node_path, _BLOCKS_THE_EVENT_LOOP, capture_output=True, timeout=1
        )

    error = excinfo.value
    assert isinstance(error, NodeHarnessSpawnTimeout)
    assert len(error.attempts) == 1, (
        f"卡住但吐了东西的脚本证明它跑起来了，重试没有意义：{error.attempts}"
    )
    assert error.attempts[0].stdout == "started", (
        "同步卡死之前写出去的东西必须还能拿到——这是区分它和「node 没跑起来」的唯一证据"
    )
    assert "blocked the event loop synchronously" in str(error), (
        f"报错不能一口咬定是 node 没跑起来：{error}"
    )


def test_a_silent_stall_is_still_retried():
    """The dual: with no output there is nothing to conclude, so retry stands."""
    calls = []
    ok = subprocess.CompletedProcess(["node"], 0, "ok", "")

    def _fake_run(argv, **kwargs):
        calls.append(argv)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(
                argv, kwargs.get("timeout"), output="", stderr=""
            )
        return ok

    original = node_harness.subprocess.run
    node_harness.subprocess.run = _fake_run
    try:
        assert run_node_script("node", "process.stdout.write('ok');", timeout=3) is ok
    finally:
        node_harness.subprocess.run = original

    assert len(calls) == 2


def test_a_stall_that_only_wrote_to_stderr_is_not_retried():
    """Evidence is evidence whichever stream it came out of.

    A harness that reports through ``console.error`` leaves its trace on stderr
    alone; counting only stdout would send that straight back into a pointless
    retry. Found by mutation - nothing else in this file covered it.
    """
    calls = []

    def _fake_run(argv, **kwargs):
        calls.append(argv)
        raise subprocess.TimeoutExpired(
            argv, kwargs.get("timeout"), output="", stderr="started"
        )

    original = node_harness.subprocess.run
    node_harness.subprocess.run = _fake_run
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            run_node_script("node", "process.stdout.write('ok');", timeout=1)
    finally:
        node_harness.subprocess.run = original

    assert len(calls) == 1, "只往 stderr 写的脚本同样证明它跑起来了，不该重试"


def test_the_wrapped_error_keeps_the_last_attempt_output_where_callers_look():
    """``except TimeoutExpired`` reading ``.stdout`` must not regress to None.

    ``subprocess.run`` fills those in after killing the child; wrapping the
    error in a subclass and forgetting to pass them through would quietly take
    that away from every caller.
    """

    def _fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(
            argv, kwargs.get("timeout"), output="partial-out", stderr="partial-err"
        )

    original = node_harness.subprocess.run
    node_harness.subprocess.run = _fake_run
    try:
        with pytest.raises(subprocess.TimeoutExpired) as excinfo:
            run_node_script("node", "process.stdout.write('ok');", timeout=1)
    finally:
        node_harness.subprocess.run = original

    assert excinfo.value.stdout == "partial-out"
    assert excinfo.value.output == "partial-out"
    assert excinfo.value.stderr == "partial-err"
