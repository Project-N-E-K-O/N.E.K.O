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
"""Shared launcher for the generated node simulation harnesses.

Several static-contract suites drive real frontend modules through a node
script built at test time.  Two ways of handing that script to node have both
bitten this repo, and both failures look like anything but what they are:

Command-line length
    ``node -e <script>`` puts the whole script on the command line.  Past 32767
    characters Windows' ``CreateProcess`` refuses it and ``subprocess`` raises
    ``WinError 206`` before node starts, so not one assertion runs.  A suite
    crossed that line at 34067 characters and stayed red unnoticed.

Locale encoding
    ``subprocess.run(..., text=True)`` without an explicit ``encoding`` encodes
    stdin and decodes stdout with ``locale.getpreferredencoding()``.  On a
    machine with the Windows UTF-8 option enabled that is cp65001 and CJK in a
    harness script sails through; on a stock English Windows (every GitHub
    runner) it is cp1252 and the same script dies with ``UnicodeEncodeError``.
    Five tests passed locally and failed in CI on exactly this.

Both runners here take the script off the command line and pin UTF-8 in both
directions.  Node lookup and the node-missing policy (skip vs. hard failure)
stay with each caller, since the suites deliberately differ there.

A third failure mode showed up once the suite ran under ``pytest -n auto`` on
the Windows runner: ``subprocess.TimeoutExpired`` with nothing else to go on.
A bare ceiling around ``subprocess.run`` cannot say which of two very different
things happened, and the two want opposite responses:

The script never settled
    A ``setInterval`` the harness forgot to clear, or an ``await`` on a promise
    nothing resolves, keeps node's event loop alive forever.  That is a real
    defect in the harness and must stay red however often it is retried.

The node process never got going
    Process creation, the read of the script off disk, or V8 bootstrap stalled
    on the runner.  Nothing in the script is reached, so no ceiling the caller
    picks can help and no assertion is being tested; retrying is the only
    sensible answer.

They are told apart by giving the script its own deadline *inside* node.  The
appended watchdog fires only if something is still holding the event loop open,
prints what that something is, and exits non-zero -- so the first case fails
deterministically, with the leaked handle named, and is never retried.  The
subprocess ceiling then sits a few seconds above that deadline, so a
surviving ``TimeoutExpired`` means the watchdog never got to run.

The deadline has to be enforced two ways, because ``unref()`` cuts both.  A
script still holding the loop open gets the timer.  A script that merely ran
long and then finished would sail past it -- node exits before an unref'd timer
on an otherwise empty loop -- so the overdue case is checked once, synchronously,
at the moment the watchdog arms.  Without that second half, moving the caller's
timeout off ``subprocess.run`` and into the script would have quietly stopped
the timeout from meaning anything for every script that overruns and completes.

One in-script hang escapes the watchdog: a synchronous block.  ``while (true)
{}`` never yields, and a timer cannot interrupt the thread it is queued on --
no amount of retrying will make that script finish.  What separates it from a
genuine spawn stall is evidence: measured on Windows, whatever the script wrote
before it blocked still reaches the parent, while a node that never reached the
script emits nothing at all.  So a stalled attempt that produced output is
reported straight away, and only a silent one is retried.  A synchronous block
that also printed nothing stays ambiguous, and the error says so rather than
picking a side -- as does the case of a caller that never captured output at
all, where the silence is nobody watching rather than nothing said.
"""

import os
import subprocess
import tempfile


# Script deadline for callers that pass no ``timeout`` of their own.  Their
# ceiling today is the 25-minute job cap, so anything finite is an improvement;
# the slowest harness in the suite measures 5.1s.
_DEFAULT_WATCHDOG_SECONDS = 120.0
# Head-room between the script's own deadline and the subprocess ceiling.  The
# gap is what the watchdog needs to fire, write its diagnosis and exit, and it
# is the only thing that makes a surviving ``TimeoutExpired`` mean "node never
# ran the script".  Callers keep exactly the script budget they asked for.
_SPAWN_SLACK_SECONDS = 5.0
# Distinctive exit code so a watchdog kill is never mistaken for an assertion
# failure or an uncaught exception, both of which leave node with 1.
_WATCHDOG_EXIT_CODE = 87

_WATCHDOG_TEMPLATE = r"""
;(function () {
  // The timer comes from node:timers, not from a name in scope.  Harness
  // scripts routinely install a fake clock, and they reach the watchdog through
  // two different doors: `const setTimeout = (cb) => cb()` shadows the bare name
  // for the whole module (temporal dead zone included, so merely reading it
  // throws), and a harness that has done `global.window = global` overwrites the
  // real thing when it sets `window.setTimeout`.  The module export is behind
  // both.  Everything else goes through globalThis for the same reason.
  var g = globalThis;
  var timer;
  try {
    timer = require('node:timers').setTimeout;
  } catch (err) {
    timer = g.setTimeout;
  }

  function report(prefix) {
    var held;
    try {
      held = typeof g.process.getActiveResourcesInfo === 'function'
        ? g.JSON.stringify(g.process.getActiveResourcesInfo())
        : '<getActiveResourcesInfo unavailable on this node>';
    } catch (err) {
      held = '<unavailable: ' + err + '>';
    }
    var message = '\n[node_harness] ' + prefix + ' __SECONDS__s after node '
      + 'started.\n'
      + '[node_harness] event loop is held by: ' + held + '\n'
      + '[node_harness] a harness that never settles has usually left a timer '
      + 'armed (clearInterval/clearTimeout) or is awaiting a promise that '
      + 'nothing resolves.\n';
    try {
      // writeSync, because process.exit() does not flush a pending async pipe
      // write and stderr to a pipe is async on Windows -- the diagnosis is the
      // whole point, so it must not be the part that gets dropped.
      require('node:fs').writeSync(2, message);
    } catch (err) {
      g.process.stderr.write(message);
    }
    g.process.exit(__EXIT_CODE__);
  }

  // Charge node startup and the script's synchronous top level against the
  // budget, because the outer ceiling is charged for them too.  Arming for the
  // full deadline here would put the watchdog at (startup + top level +
  // deadline) while the ceiling sits at (deadline + slack): any top level
  // heavier than the slack and the ceiling wins, losing the diagnosis.
  var spent = Math.round((g.process.uptime ? g.process.uptime() : 0) * 1000);
  var budget = __MILLIS__ - spent;

  if (budget <= 0) {
    // Already over budget by the time the top level finished.  Scheduling a
    // timer here would be worse than useless: it is unref'd, so with the loop
    // otherwise empty node exits 0 before the callback ever runs, and the
    // caller's timeout silently stops meaning anything.  Fail now instead.
    report('the script was still running');
  }

  var deadline = timer(function () {
    report('the script still had pending work');
  }, budget);
  // unref() so the watchdog cannot itself be the reason node stays up: with no
  // other handle open node exits first and the watchdog never fires, which is
  // exactly the healthy case.
  if (deadline && typeof deadline.unref === 'function') deadline.unref();
})();
"""


def _excerpt(blob, limit: int = 400) -> str:
    """Readable, bounded view of whatever a stalled attempt had emitted."""
    if blob is None:
        return "<none>"
    if isinstance(blob, bytes):
        blob = blob.decode("utf-8", "replace")
    if len(blob) > limit:
        blob = blob[:limit] + "..."
    return repr(blob)


class NodeHarnessSpawnTimeout(subprocess.TimeoutExpired):
    """The run hit the ceiling without node exiting.

    Subclasses ``TimeoutExpired`` so existing ``except`` clauses keep working,
    and carries what each attempt managed to emit.  One attempt means the stall
    came with output and was not worth repeating; two means both were silent.
    """

    def __init__(self, cmd, timeout, attempts):
        # Carry the last attempt's output on the exception the way
        # ``subprocess.run`` would have: a caller that catches
        # ``TimeoutExpired`` and reads ``.stdout``/``.stderr`` (or ``.output``)
        # must not get None just because the launcher wrapped the error.
        last = attempts[-1] if attempts else None
        super().__init__(
            cmd,
            timeout,
            output=getattr(last, "stdout", None),
            stderr=getattr(last, "stderr", None),
        )
        self.attempts = list(attempts)

    def __str__(self) -> str:
        diagnosis = (
            "Stalled before node exited, and the in-script watchdog never "
            "fired. Either node never reached the script (process creation, "
            "reading it off disk, V8 bootstrap), or the script blocked the "
            "event loop synchronously -- a timer cannot interrupt that. Each "
            "attempt's output below is the evidence, where there was a pipe "
            "to collect it: anything at all means the script did run."
        )
        lines = [super().__str__(), "", diagnosis]
        if not any(_observed_output(attempt) for attempt in self.attempts):
            lines.append(
                "  note: this caller does not capture output, so the silence "
                "below is unobserved rather than empty -- pass "
                "capture_output=True to tell the two apart."
            )
        for index, attempt in enumerate(self.attempts, 1):
            lines.append(
                f"  attempt {index}: stdout={_excerpt(attempt.stdout)} "
                f"stderr={_excerpt(attempt.stderr)}"
            )
        return "\n".join(lines)


def _emitted_anything(exc: subprocess.TimeoutExpired) -> bool:
    """Did this stalled attempt prove the script ran?

    ``subprocess.run`` kills the child on timeout and then collects whatever it
    had already written, so this is real evidence rather than a guess.  It is
    one-directional: output means the script ran, silence does not prove it did
    not, which is why silence is what gets the retry.
    """
    return bool(exc.stdout) or bool(exc.stderr)


def _observed_output(exc: subprocess.TimeoutExpired) -> bool:
    """Was there a pipe to observe in the first place?

    A caller that does not capture leaves the child on the inherited handles,
    and ``communicate()`` then hands back ``None`` rather than ``""``.  Those
    two look identical to :func:`_emitted_anything` and mean opposite things --
    "the script printed nothing" versus "nobody was watching" -- so the
    distinction has to survive as far as the error message.
    """
    return exc.stdout is not None or exc.stderr is not None


def _utf8(kwargs: dict) -> dict:
    """Force UTF-8 for stdin/stdout so the host locale cannot decide."""
    merged = dict(kwargs)
    merged.setdefault("text", True)
    merged["encoding"] = "utf-8"
    return merged


def _budgeted(kwargs: dict) -> tuple[dict, float]:
    """Split the caller's ceiling into a script deadline and a spawn ceiling.

    The caller's ``timeout`` stays the budget the *script* gets; the ceiling
    handed to ``subprocess.run`` is raised by the slack, so a script that
    overruns is killed from inside node with a diagnosis rather than from
    outside with none.  A caller that passes no timeout gets the default
    deadline and a ceiling to match: it used to get neither, which left a
    synchronously blocked script running until the job cap.
    """
    merged = dict(kwargs)
    timeout = merged.get("timeout")
    watchdog = _DEFAULT_WATCHDOG_SECONDS if timeout is None else float(timeout)
    merged["timeout"] = watchdog + _SPAWN_SLACK_SECONDS
    return merged, watchdog


def _with_watchdog(script: str, seconds: float) -> str:
    """Append the self-deadline that turns a hung script into a named failure.

    Appended rather than prepended so line numbers in the caller's own stack
    traces keep pointing at the caller's own code.  It runs once the script's
    synchronous top level is done, which is precisely when the event loop takes
    over and a leaked handle starts to matter.
    """
    watchdog = (
        _WATCHDOG_TEMPLATE
        .replace("__SECONDS__", f"{seconds:g}")
        .replace("__MILLIS__", str(int(seconds * 1000)))
        .replace("__EXIT_CODE__", str(_WATCHDOG_EXIT_CODE))
    )
    return script + "\n" + watchdog


def _run_retrying_spawn_stalls(next_attempt, cmd_for_error):
    """Run once, and once more if node stalled without ever reaching the script.

    ``next_attempt`` is called per attempt so a caller that stages a temp file
    gets a fresh one, rather than a second attempt inheriting whatever state
    the killed first attempt left behind.
    """
    attempts = []
    for attempt in (1, 2):
        argv, run_kwargs = next_attempt()
        try:
            return subprocess.run(argv, **run_kwargs)
        except subprocess.TimeoutExpired as exc:
            attempts.append(exc)
            if attempt == 2 or _emitted_anything(exc):
                raise NodeHarnessSpawnTimeout(
                    cmd_for_error, exc.timeout, attempts
                ) from exc
    raise AssertionError("unreachable")  # pragma: no cover


def run_node_script(node_path: str, script: str, **kwargs) -> subprocess.CompletedProcess[str]:
    """Run ``script`` from a temp file under ``node_path``.

    Use this when the script is large or grows with the behaviour it simulates.
    Extra keyword arguments go straight to ``subprocess.run``.
    """
    merged, watchdog_seconds = _budgeted(_utf8(kwargs))
    guarded = _with_watchdog(script, watchdog_seconds)
    staged: list[str] = []

    def _attempt():
        with tempfile.NamedTemporaryFile(
            "w", suffix=".js", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(guarded)
            staged.append(handle.name)
        return [node_path, staged[-1]], merged

    try:
        return _run_retrying_spawn_stalls(_attempt, [node_path, "<temp script>"])
    finally:
        for path in staged:
            try:
                os.unlink(path)
            except OSError:
                # Best effort on purpose: a killed node on Windows can still
                # hold the file for a moment, and a leaked temp file must not
                # replace the real error with a cleanup one.
                pass


def run_node_stdin(node_path: str, script: str, **kwargs) -> subprocess.CompletedProcess[str]:
    """Pipe ``script`` into ``node -`` over stdin.

    Equivalent to ``run_node_script`` for callers already written against the
    stdin form; stdin has no length ceiling, so only the encoding pin matters
    here. Extra keyword arguments go straight to ``subprocess.run``.
    """
    merged, watchdog_seconds = _budgeted(_utf8(kwargs))
    guarded = _with_watchdog(script, watchdog_seconds)
    return _run_retrying_spawn_stalls(
        lambda: ([node_path, "-"], dict(merged, input=guarded)),
        [node_path, "-"],
    )
