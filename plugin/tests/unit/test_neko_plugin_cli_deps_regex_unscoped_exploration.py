"""Bug-condition exploration property test for PR #1480 review item 1.23.

Bug 1.23 — ``_update_pyproject_dependencies`` regex is not scoped to ``[project]``
==================================================================================

``_update_pyproject_dependencies`` in
``plugin/neko_plugin_cli/commands/deps_cmd.py`` rewrites the
``dependencies`` array of a plugin's ``pyproject.toml`` with a single
unscoped regular expression::

    pattern = re.compile(r"(dependencies\\s*=\\s*)\\[([^\\]]*)\\]", re.DOTALL)
    ...
    content = pattern.sub(replacement, content, count=1)

The pattern matches *any* ``dependencies = [...]`` literal in the file
and rewrites only the **first** occurrence in document order. TOML lets
the literal substring ``dependencies = [...]`` appear in tables that
have nothing to do with ``[project]``: ``[tool.uv]`` (uv's dev-dep
shorthand), ``[tool.poetry.group.dev]`` (Poetry), arbitrary
``[tool.<vendor>]`` tables, and so on. Whenever any such table appears
**before** ``[project]`` in source order, the regex clobbers the wrong
section: it overwrites the unrelated ``dependencies`` field while
``[project].dependencies`` itself stays untouched.

The expected fix (req 2.23, task 2.6.3) restricts the rewrite to the
range ``[project] … <next [section]>`` and revalidates the output by
re-parsing with ``tomllib``. See bugfix.md §1.23 / §2.23 and design.md
"Phase 6 — CLI deps 命令" for the full plan.

Property under test (Bug Condition C(X) — exploration form)
-----------------------------------------------------------

For *every* set of new dependency specifiers ``extra_deps`` (1–3 short
ASCII names), against a ``pyproject.toml`` fixture where ``[tool.uv]``
appears **before** ``[project]`` and both tables happen to declare a
``dependencies`` array, the post-fix invariants

    tomllib.load(path)["project"]["dependencies"]
        == sorted({"existing", *extra_deps}, key=str.lower)

    tomllib.load(path)["tool"]["uv"]["dependencies"]
        == ["red-herring"]                       # byte-unchanged

SHOULD both hold. On the **unfixed** code path BOTH invariants FAIL:

* ``[project].dependencies`` is still ``["existing"]`` (the regex never
  reached this section because it stopped after the first match), AND
* ``[tool.uv].dependencies`` has been silently clobbered with the new
  project-dep list.

That double failure is the observation that confirms the bug exists.

Documented counterexample
-------------------------

A concrete one-shot variant (no Hypothesis) is pinned in
``test_deps_regex_documented_counterexample`` below: the call
``_update_pyproject_dependencies(path, ["existing", "foo>=1"])`` against
the fixture above leaves ``[project].dependencies == ["existing"]``
(untouched) while ``[tool.uv].dependencies`` becomes
``["existing", "foo>=1"]`` (clobbered). That is the simplest one-line
reproducer and the assertion below shows what the **fixed** behaviour
must look like — so the test fails today and passes once req 2.23 is
implemented.

A second test, ``test_deps_regex_unfixed_state_baseline``, asserts the
**current (buggy) wrong-clobber state**. It exists as a bug-condition
baseline: it PASSES on unfixed code (proving the bug is present at the
expected location) and will FAIL once the fix lands — at which point
the ``Phase 6`` fix-checking tests in ``test_pyproject_update_scoped.py``
take over. This baseline is intentionally redundant with the
exploration property and should be removed (or inverted) by whoever
implements task 2.6.3.

**Validates: Requirements 1.23**
"""

from __future__ import annotations

import tempfile
import tomllib
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from plugin.neko_plugin_cli.commands.deps_cmd import _update_pyproject_dependencies


# Constrain extra dep names to a short ASCII alphabet so the writer's
# `'  "{d}"'` formatting always produces valid TOML, and exclude the
# literal "existing" so the assertion `sorted({"existing", *extras})`
# never collides with the seed dep.
_PACKAGE_NAME = (
    st.from_regex(r"[a-z][a-z0-9]{0,9}", fullmatch=True)
    .filter(lambda s: s != "existing")
)


def _write_fixture_tool_uv_before_project(path: Path) -> None:
    """Write a pyproject.toml where ``[tool.uv]`` precedes ``[project]``.

    ``[tool.uv].dependencies`` is a red-herring ``dependencies`` array.
    The unfixed regex matches the first ``dependencies\\s*=\\s*[...]``
    in document order — that is THIS one, not the ``[project]`` one
    further down — and clobbers it.

    Note: this fixture intentionally avoids placing the literal
    substring ``dependencies = [...]`` anywhere except under
    ``[tool.uv]`` and ``[project]``. (Putting it in a TOML comment
    would make the unscoped regex match the comment text first and
    rewrite it, producing un-parseable TOML — which is itself a
    symptom of the bug, but obscures the cleaner "wrong section gets
    clobbered" demonstration we want here.)
    """
    path.write_text(
        '[build-system]\n'
        'requires = ["setuptools"]\n'
        '\n'
        '[tool.uv]\n'
        '# Red-herring entry in an unrelated tool table.\n'
        'dependencies = ["red-herring"]\n'
        '\n'
        '[project]\n'
        'name = "test-plugin"\n'
        'version = "0.0.0"\n'
        'dependencies = ["existing"]\n',
        encoding="utf-8",
    )


@pytest.mark.plugin_unit
@settings(
    deadline=None,
    max_examples=15,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    extra_deps=st.lists(_PACKAGE_NAME, min_size=1, max_size=3, unique=True),
)
def test_update_pyproject_dependencies_only_modifies_project_section(
    extra_deps: list[str],
) -> None:
    """Property (post-fix): only ``[project].dependencies`` is rewritten.

    For every Hypothesis-generated ``extra_deps``:

    1. Materialize a fresh fixture pyproject in a TemporaryDirectory
       (where ``[tool.uv]`` precedes ``[project]`` and both tables
       declare a ``dependencies = [...]`` field).
    2. Call ``_update_pyproject_dependencies(path, expected_project)``
       with ``expected_project = sorted({"existing", *extra_deps},
       key=str.lower)``. (We pre-sort to match the writer's own
       ``sorted(deps, key=str.lower)`` formatting so equality below
       compares exactly the bytes we asked it to write.)
    3. Re-parse the file with ``tomllib`` and assert BOTH:

       * ``data["project"]["dependencies"] == expected_project``
       * ``data["tool"]["uv"]["dependencies"] == ["red-herring"]``

    On the unfixed code path BOTH assertions fail: the regex matches
    the first ``dependencies = [...]`` it sees (the ``[tool.uv]`` one),
    overwrites that, and never touches ``[project].dependencies``.
    """

    expected_project = sorted({"existing", *extra_deps}, key=str.lower)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "pyproject.toml"
        _write_fixture_tool_uv_before_project(path)

        _update_pyproject_dependencies(path, expected_project)

        with path.open("rb") as f:
            data = tomllib.load(f)

        project_deps = data["project"]["dependencies"]
        tool_uv_deps = data["tool"]["uv"]["dependencies"]

        assert project_deps == expected_project, (
            "[project].dependencies was not rewritten by "
            "_update_pyproject_dependencies. The unscoped regex matched "
            "another `dependencies = [...]` field earlier in the file and "
            "stopped before reaching [project] (count=1).\n"
            f"  expected: {expected_project!r}\n"
            f"  observed: {project_deps!r}"
        )
        assert tool_uv_deps == ["red-herring"], (
            "[tool.uv].dependencies must remain unchanged. The unscoped "
            "regex clobbered an unrelated `dependencies` field instead of "
            "the one under [project].\n"
            "  expected: ['red-herring']\n"
            f"  observed: {tool_uv_deps!r}"
        )


@pytest.mark.plugin_unit
def test_deps_regex_documented_counterexample(tmp_path: Path) -> None:
    """Anchor counterexample (post-fix invariants on a concrete input).

    With ``[tool.uv]`` preceding ``[project]``, the call

        _update_pyproject_dependencies(path, ["existing", "foo>=1"])

    must (post-fix) leave the unrelated ``[tool.uv].dependencies`` table
    byte-unchanged and rewrite only ``[project].dependencies``.

    On unfixed code, exactly the opposite happens (see the companion
    ``test_deps_regex_unfixed_state_baseline``): the regex hits
    ``[tool.uv].dependencies`` first, overwrites it with the new
    project-dep list, and ``[project].dependencies`` retains its old
    ``["existing"]`` value.
    """

    path = tmp_path / "pyproject.toml"
    _write_fixture_tool_uv_before_project(path)

    _update_pyproject_dependencies(path, ["existing", "foo>=1"])

    with path.open("rb") as f:
        data = tomllib.load(f)

    assert data["tool"]["uv"]["dependencies"] == ["red-herring"], (
        "[tool.uv].dependencies must NOT be rewritten — the regex must be "
        "scoped to [project] (req 2.23). Observed clobber: "
        f"{data['tool']['uv']['dependencies']!r}"
    )
    assert data["project"]["dependencies"] == ["existing", "foo>=1"], (
        "[project].dependencies must be rewritten to include the new "
        f"package, but observed: {data['project']['dependencies']!r}"
    )


@pytest.mark.plugin_unit
def test_deps_regex_unfixed_state_baseline(tmp_path: Path) -> None:
    """Bug-condition baseline: pin the **wrong** clobber pattern.

    This test PASSES on unfixed code and FAILS once req 2.23 lands.
    It exists purely to document the precise misbehaviour we observe
    today (and so the orchestrator can record the counterexample). Once
    task 2.6.3 ships the scoped rewrite, this test should be deleted —
    its fix-checking equivalent lives in
    ``plugin/neko_plugin_cli/tests/test_pyproject_update_scoped.py``
    (per tasks.md §3.6).

    Documented counterexample (unfixed):

    * ``[tool.uv].dependencies``     becomes ``["existing", "foo>=1"]``
      (wrongly clobbered with the new project-dep list)
    * ``[project].dependencies``     remains ``["existing"]``
      (regex never reached this section because ``count=1``)
    """

    path = tmp_path / "pyproject.toml"
    _write_fixture_tool_uv_before_project(path)

    _update_pyproject_dependencies(path, ["existing", "foo>=1"])

    with path.open("rb") as f:
        data = tomllib.load(f)

    # Buggy state: the new deps were written into [tool.uv], not [project].
    assert data["tool"]["uv"]["dependencies"] == ["existing", "foo>=1"], (
        "Buggy-state baseline expected [tool.uv].dependencies to have been "
        "wrongly clobbered with the new project-dep list. If this assertion "
        "fails, the scoping fix (req 2.23) has likely landed — delete this "
        "baseline and rely on the Phase 6 fix-checking tests instead.\n"
        f"  observed: {data['tool']['uv']['dependencies']!r}"
    )
    assert data["project"]["dependencies"] == ["existing"], (
        "Buggy-state baseline expected [project].dependencies to be left "
        "untouched at ['existing']. If this assertion fails, the scoping "
        "fix (req 2.23) has likely landed — delete this baseline.\n"
        f"  observed: {data['project']['dependencies']!r}"
    )
