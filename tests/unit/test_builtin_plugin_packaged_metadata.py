"""Guard: a built-in plugin's packaged metadata must still describe its source.

``plugin.meta.json`` is the only place a plugin's ``@plugin_entry`` list is
written down for the host -- ``plugin.toml`` carries no entries table, because
entries are derived from decorators at build time. The host verifies that file
against the source tree before trusting it (see
``plugin/server/infrastructure/packaged_metadata.py``); on a mismatch it drops
the whole thing and falls back to the manifest, which leaves the plugin
advertising *zero* entries. Anything that resolves an entry from registry
metadata rather than from the live plugin process then breaks -- most visibly
hosted UI actions, which fail with "UI action '<id>' is not a plugin entry".

Not hypothetical: #3081 edited ``game_agent_minecraft``'s source without
rebuilding its metadata, and that plugin's own status button was dead on every
install until the metadata was rebuilt. Nothing caught it, which is why this
test exists.

To fix a failure here::

    neko-plugin build <plugin> --keep-staging
    # then copy the staged plugin.meta.json back over plugin/plugins/<plugin>/

A plugin whose directory holds untracked files (a plugin writing its own runtime
state next to its source) cannot be checked from a developer working tree: those
files count toward the fingerprint but are not part of the commit. Those are
skipped here and still fully checked in CI, whose checkout has none of them.

The line-ending guard below is the other half, and it is what makes this test
useful on Windows. ``source_bytes`` counts raw bytes, so a metadata file built
against a CRLF working tree records a byte total that no real checkout has:
``.gitattributes`` pins these files to LF, so the committed content, CI, and
every shipped build see the LF total. Comparing metadata against the working
tree alone would agree with itself on such a machine and only fail elsewhere --
which is exactly what happened here, with metadata rebuilt on a tree an editor
had rewritten with CRLF.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGINS_DIR = REPO_ROOT / "plugin" / "plugins"


def _tracked_plugin_paths() -> set[str] | None:
    """Repo-relative posix paths git tracks under ``plugin/plugins``.

    ``None`` when git cannot answer (no git binary, not a checkout), which makes
    the test skip rather than guess which files are source.
    """
    try:
        completed = subprocess.run(
            ["git", "ls-files", "plugin/plugins"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return {line for line in completed.stdout.splitlines() if line}


def _worktree_crlf_paths() -> list[str] | None:
    """Tracked plugin paths whose working-tree copy has CRLF line endings.

    Asks git rather than scanning bytes, because "contains a CR-LF pair" is not
    the same question: a PNG holds those two bytes as pixel data. ``ls-files
    --eol`` reports git's own verdict per path -- ``w/crlf`` or ``w/mixed`` for
    text, ``w/-text`` for anything it treats as binary -- so binary files drop
    out on their own instead of needing an extension list to keep current.

    ``None`` when git cannot answer, which makes the test skip rather than pass
    on no evidence.
    """
    try:
        completed = subprocess.run(
            ["git", "ls-files", "--eol", "plugin/plugins"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    found: list[str] = []
    for line in completed.stdout.splitlines():
        fields = line.split(maxsplit=3)
        if len(fields) < 4:
            continue
        worktree_eol = fields[1]
        if worktree_eol in ("w/crlf", "w/mixed"):
            found.append(fields[3].strip())
    return sorted(found)


def test_builtin_plugin_sources_use_lf_endings():
    """Source under ``plugin/plugins`` must be LF, so a fingerprint is portable.

    ``.gitattributes`` declares these files ``eol=lf``, so git normalizes them
    on commit and hands them back as LF everywhere. A working tree that carries
    CRLF anyway (an editor or a script that wrote with the platform default)
    still fingerprints as CRLF, and ``neko-plugin build`` run there bakes a
    ``source_bytes`` total that is off by one byte per line. The plugin then
    fails validation on the committed tree, in CI, and in every packaged build,
    while looking fine on the machine that produced it.
    """
    crlf = _worktree_crlf_paths()
    if crlf is None:
        pytest.skip("git unavailable; cannot tell text files from binary ones")
    assert not crlf, (
        "these tracked plugin sources have CRLF in the working tree: "
        + ", ".join(crlf)
        + ". .gitattributes pins them to LF, so any plugin.meta.json built from "
        "this tree records a source_bytes total no checkout will ever match. "
        "Rewrite them with LF endings, then rebuild the affected plugin's "
        "plugin.meta.json."
    )


def test_builtin_plugin_packaged_metadata_matches_source_tree():
    from plugin.server.infrastructure.packaged_metadata import (
        read_packaged_metadata,
        source_stat_summary,
    )

    tracked = _tracked_plugin_paths()
    if tracked is None:
        pytest.skip("git unavailable; cannot separate source from runtime artifacts")

    stale: list[str] = []
    skipped: list[str] = []
    checked: list[str] = []

    for plugin_dir in sorted(p for p in PLUGINS_DIR.iterdir() if p.is_dir()):
        if not (plugin_dir / "plugin.meta.json").is_file():
            # No packaged metadata at all is a supported state: the host just
            # rescans. Only a metadata file that has gone stale is a defect.
            continue
        present = {
            (plugin_dir / name).relative_to(REPO_ROOT).as_posix()
            for name in source_stat_summary(plugin_dir).names
        }
        untracked = sorted(present - tracked)
        if untracked:
            skipped.append(f"{plugin_dir.name} ({untracked[0]}, +{len(untracked) - 1})")
            continue
        checked.append(plugin_dir.name)
        if read_packaged_metadata(plugin_dir) is None:
            stale.append(plugin_dir.name)

    assert checked, (
        "no plugin could be checked -- every packaged plugin had untracked files "
        f"in its directory, so the guard verified nothing. Skipped: {skipped}"
    )
    assert not stale, (
        "packaged metadata no longer describes the source tree for: "
        + ", ".join(stale)
        + ". The host will discard that metadata and advertise ZERO entries for "
        "these plugins, so their hosted UI actions fail with 'is not a plugin "
        "entry'. Rebuild each one with `neko-plugin build <plugin> "
        "--keep-staging` and copy the staged plugin.meta.json back into "
        "plugin/plugins/<plugin>/."
    )
