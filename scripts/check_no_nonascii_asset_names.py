#!/usr/bin/env python3
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

"""Ratchet: no NEW non-ASCII filenames in anything bundled into the desktop app.

Why this exists
---------------
Nuitka ``--mode=app`` puts the whole payload under
``projectneko_server.app/Contents/MacOS/``. codesign's default bundle rules
(``rules2``) classify everything under that directory as *nested code*::

    ^(Frameworks|SharedFrameworks|PlugIns|Plug-ins|XPCServices|Helpers|MacOS|
      Library/(Automator|Spotlight|LoginItems))/   =>   nested

So every .mp3 / .png / .json we ship is signed as its own code object, and for
each one codesign writes a designated requirement into
``Contents/_CodeSignature/CodeResources``::

    identifier <name> and anchor apple generic and certificate ...

When the filename is non-ASCII, codesign emits that identifier as a **hex
literal** instead of a quoted string::

    identifier 0xe4b883e5a4a9e5898defbc8ce68891e4bbace8bf98e58faae698afe7a...

That is not valid requirement syntax. Reading the seal back fails, and the
whole bundle then verifies as::

    <app>: the sealed resource directory is invalid

…which blocks signing, notarization, and Steam upload. One such file poisons
the entire bundle; the error names no path, so it is genuinely painful to
diagnose from scratch.

Why CI can never catch it on its own
------------------------------------
``.github/workflows/build-desktop.yml`` signs ad-hoc::

    codesign --force --deep --sign - dist/Xiao8/projectneko_server.app

Ad-hoc signatures use ``cdhash H"..."`` requirements, which carry no
identifier at all — so the hex-literal bug simply does not occur there and
the workflow is always green. The breakage only appears on the local
Developer ID signing path (``build_mac.sh``). This lint is the substitute for
a signal CI structurally cannot produce.

What it scans
-------------
Only what actually ships:

- files git knows about under the bundled roots (``BUNDLED_ROOTS``) —
  tracked plus untracked-but-not-ignored, so the check fires before
  ``git add`` rather than only after commit;
- ``filename`` values in the manifests that drive build-time downloads
  (``main_logic/asr_client/*/models/manifest.json``): the ``.onnx`` itself is
  gitignored and only exists after a build, but the name it will be written
  under is committed, so a plain CI run can still catch it;
- member names inside the archives that get unpacked into those roots at
  build time (``assets/*.tar.gz`` -> ``static/<model>/`` via
  ``build_frontend.sh``; the PNGTuber packs named by
  ``frontend/pngtuber-packs/manifest.json`` -> ``static/pngtuber/<folder>/``
  via ``scripts/unpack_builtin_pngtuber.py``). Reading member names needs no
  build and no extraction, so the check gives the same answer in a fresh
  checkout as on a build machine. Hard-link and symlink members count too:
  ``tar -xzm`` materializes them as entries under ``static/`` like any file.

Asking git (rather than walking the working tree) keeps the result
deterministic: gitignored build outputs, downloaded model weights, and local
runtime files never make the answer wander between a fresh checkout and a
built one. Pass ``--include-untracked`` to additionally walk the on-disk tree
— useful right after a build, when you want to sanity-check generated payload
too.

Only **basenames** are checked, not whole paths. codesign derives the nested
identifier from the filename alone, so a non-ASCII *directory* holding
ASCII-named files signs and verifies cleanly (checked against a real
Developer ID certificate: ``MacOS/<cjk-dir>/plain_name.png`` seals as
``identifier "plain_name"`` and passes ``--verify --deep --strict``).
Flagging those too would only produce failures nobody can act on.

``tests/`` is deliberately out of scope: it carries a few CJK fixture paths
and never reaches the .app.

Baseline
--------
338 tracked files plus 1 archive member are already non-ASCII when this check
was written, across three unrelated subsystems:

- ``static/assets/tutorial/guide-audio/{zh,ja,ko,ru,en}/*.mp3`` — filenames
  are truncated line transcripts, referenced from the ``audioFilesByKey``
  manifests in ``static/tutorial/yui-guide/days/*.js``;
- ``static/vrm/motion/**``, ``static/vrm/animation/*``,
  ``static/mmd/animation/*`` — ``static/vrm/motion/manifest.json`` states the
  convention outright (``"fileNaming": "descriptive Chinese filename with
  stable id"``, ``"authoritativeLanguage": "zh-CN"``), because motion lookup
  runs through Chinese action-card retrieval;
- ``static/game/games/soccer/audio/*.mp3``, plus one Live2D expression file
  under ``static/yui-origin/expressions/`` that ships inside
  ``assets/yui-origin.tar.gz`` and is named from the ``.model3.json``.

Renaming those is a product decision, not a mechanical sweep — the VRM naming
scheme in particular is load-bearing for motion retrieval. So this is a
ratchet, not a clean-room ban: everything listed in
``scripts/nonascii_asset_baseline.txt`` is grandfathered, anything new fails.

TODO: shrink the baseline. Each family needs its own follow-up — rename the
files to ASCII (stable slug or id) and update the manifest that names them.
An empty baseline is the goal; when it gets there, delete the file and make
this a plain ban.

Usage
-----
    python scripts/check_no_nonascii_asset_names.py
    python scripts/check_no_nonascii_asset_names.py --list
    python scripts/check_no_nonascii_asset_names.py --include-untracked
    python scripts/check_no_nonascii_asset_names.py --update-baseline
    python scripts/check_no_nonascii_asset_names.py --base origin/main

The baseline is a ratchet in both directions of attack: ``--update-baseline``
only ever drops entries that no longer exist, and ``--base`` fails when the
committed list gained a line relative to that ref. Without the second one the
first is only a convention — adding the asset and its baseline entry in the
same PR leaves nothing for the in-tree comparison to see.
"""
from __future__ import annotations

import argparse
from fnmatch import fnmatchcase
import json
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

try:  # 3.11+ stdlib; the repo pins ==3.11.*, this is belt-and-braces
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - only on an older interpreter
    # Without it we simply do not know each plugin's [tool.neko.build] rules,
    # which means scanning more than ships — false positives, never a hole.
    tomllib = None

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "scripts" / "nonascii_asset_baseline.txt"

CODE = "NONASCII_ASSET_NAME"

# Directories whose contents end up inside the .app payload. Mirrors the
# --include-data-dir / --include-package-data set in build_nuitka.bat,
# build_mac.sh and .github/workflows/build-desktop.yml; keep in sync when a
# new payload directory is added there.
BUNDLED_ROOTS: tuple[str, ...] = (
    "static",
    "templates",
    "assets",
    # config/ and data/ ship a named subset, not the whole root:
    # --include-package=config compiles Python modules but copies no data, so
    # everything that actually lands is listed explicitly in the build scripts.
    # Scanning the roots wholesale would fail a PR over e.g. config/prompts/*.md,
    # which never reaches the payload.
    "config/__init__.py",
    "config/api_providers.json",
    "config/characters.json",
    "config/core_config.json",
    "config/user_preferences.json",
    "config/characters",
    "config/changelog",
    "config/surveys",
    "data/browser_use_prompts",
    "data/tiktoken_cache",
    "data/embedding_models",
    # Not all of docs/ — the build packs exactly one subtree
    # (--include-data-dir=docs/zh-CN/guide). Scanning the whole root would fail
    # documentation PRs over files that never enter the payload.
    "docs/zh-CN/guide",
    # Only the vite output ships (--include-data-dir=frontend/plugin-manager/dist,
    # identically in build_nuitka.bat, build_mac.sh and build-desktop.yml).
    # Frontend *sources* never enter the payload — React's output goes to
    # static/ — so scanning all of frontend/ would fail a PR over a file that
    # cannot possibly break signing.
    "frontend/plugin-manager/dist",
    # Vite copies public/ verbatim into the build output, keeping the filename.
    # Those outputs are gitignored (plugin-manager/dist, static/react/neko-chat),
    # so without these two roots a tracked public/ asset is invisible here yet
    # still lands in the payload.
    "frontend/plugin-manager/public",
    "frontend/react-neko-chat/public",
    # Whole source trees, not just src/assets: Vite decides by *import*, not by
    # directory. An imported asset above the inline limit is emitted as
    # `assets/[name]-[hash][extname]` wherever it lives, and a dynamically
    # imported module donates its basename to the chunk name the same way. Both
    # outputs are gitignored, so the source tree is the only place CI can see
    # these names without building. An ASCII-only rule for frontend sources
    # costs a rename; missing one costs the whole mac release.
    "frontend/plugin-manager/src",
    "frontend/react-neko-chat/src",
    "plugin/plugins",
    # --include-package=steamworks pulls every native lib in this directory in
    # as package data; the workflow's cleanup only removes the fixed
    # wrong-platform filenames, so anything else here lands in the payload.
    "steamworks",
    # Voice-turn + speaker models, both --include-data-dir'd. The .onnx weights
    # are downloaded at build time and gitignored, so the git listing cannot see
    # them; these roots cover whatever *is* tracked here, and make
    # --include-untracked reach the downloaded payload after a build. The names
    # the downloader will write are checked from the manifests below, so a plain
    # CI run catches them without building.
    "main_logic/asr_client/endpointing/models",
    "main_logic/asr_client/speaker_shadow/models",
)

# Manifests naming files the build downloads into a bundled root. The weights are
# gitignored, so no git listing can see them before a build — but the name is
# committed right here, so it can be checked in a plain run. ``filename`` sits
# either at the top level or inside an ``assets`` list, depending on the schema.
MODEL_MANIFESTS: tuple[str, ...] = (
    "main_logic/asr_client/endpointing/models/manifest.json",
    "main_logic/asr_client/speaker_shadow/models/manifest.json",
)

# Archives that are expanded into a bundled root at build time. Value is the
# bundled path prefix the members land under, so a violation reports where the
# file will actually sit inside the .app rather than where it hides today.
TAR_ARCHIVE_DESTS: tuple[tuple[str, str], ...] = (
    # build_frontend.sh: unpack_live2d_model <name>  ->  static/<name>/
    ("assets", "static"),
)
# PNGTuber packs are not globbed: scripts/unpack_builtin_pngtuber.py unpacks
# exactly the archives listed in this manifest, and each one lands under its
# own `folder`. Both halves matter — globbing would flag a zip the build never
# opens, and a shared prefix would report `static/pngtuber/layers/x.png` for a
# member that really lands at `static/pngtuber/yui-origin/layers/x.png`, while
# collapsing same-named members from different packs into one entry.
ZIP_MANIFEST_REL = "frontend/pngtuber-packs/manifest.json"
ZIP_MANIFEST_DEST_ROOT = "static/pngtuber"

# Never walked under --include-untracked. Vendored/derived trees whose names
# we do not author; a hit in here is a bug in the upstream package, not in
# this repo, and the .app build has its own gates for those.
WALK_EXCLUDE_DIRS = frozenset(
    {
        "node_modules",
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
    }
)


def _is_ascii(text: str) -> bool:
    return all(ord(ch) < 128 for ch in text)


def _under_bundled_root(rel_posix: str) -> bool:
    return any(
        rel_posix == root or rel_posix.startswith(root + "/") for root in BUNDLED_ROOTS
    )


# Mirror of the staging filter in scripts/prepare_nuitka_plugins.py, which runs
# each plugin's ``[tool.neko.build]`` rules (plus hard defaults) before the
# payload is installed into the bundle. Without it this check rejects files that
# never reach Contents/MacOS — a plugin's own tests/, its .db/.log runtime
# leftovers, editor directories.
#
# It is a mirror rather than an import on purpose: the real rules live behind
# pydantic (plugin/neko_plugin_cli/core/build_rules.py) and the analyze job runs
# these scripts on a bare interpreter with no dependencies installed.
#
# tests/unit/test_check_no_nonascii_asset_names.py imports the real
# ``should_skip_path`` and demands the same verdict in both directions, so this
# copy cannot drift silently. The two directions are not equally bad, which is
# worth knowing when one does slip: listing something the real staging keeps
# stops us scanning a file that ships (a hole), while missing something it drops
# only leaves a false positive. Both fail the test; only the first is dangerous.
_PLUGIN_SKIP_DIR_NAMES = frozenset(
    {
        "__pycache__",
        ".github",
        ".vscode",
        ".idea",
        ".pytest_cache",
        ".mypy_cache",
        ".venv",
        ".git",
    }
)
_PLUGIN_SKIP_ROOT_DIR_NAMES = frozenset({"dist", "build"})
_PLUGIN_SKIP_FILE_NAMES = frozenset({".DS_Store"})
# Two different comparisons upstream, and the difference matters: the build
# rules test `Path.suffix` case-SENSITIVELY against lowercase .pyc/.pyo, while
# _remove_private_runtime_artifacts lowercases before testing .db/.log. So a
# file named `x.PYC` really is staged and shipped — folding case here would
# drop it from the scan and hide it.
_PLUGIN_SKIP_SUFFIXES_EXACT = frozenset({".pyc", ".pyo"})
_PLUGIN_SKIP_SUFFIXES_FOLDED = frozenset({".db", ".log"})

PLUGINS_ROOT = "plugin/plugins"


def _match_build_pattern(path_str: str, pattern: str) -> bool:
    if fnmatchcase(path_str, pattern):
        return True
    return "/" not in pattern and fnmatchcase(PurePosixPath(path_str).name, pattern)


def _plugin_rules(repo_root: Path, plugin_dir: str) -> dict[str, list[str]]:
    pyproject = repo_root / PLUGINS_ROOT / plugin_dir / "pyproject.toml"
    if tomllib is None or not pyproject.is_file():
        return {}
    try:
        table = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        # A broken pyproject is the plugin CLI's problem to report; here it just
        # means "no extra rules", which only ever widens what we scan.
        return {}
    build = table.get("tool", {}).get("neko", {}).get("build", {})
    if not isinstance(build, dict):
        return {}
    # BuildRuleSet._normalize_pattern_list strips each entry and drops blanks
    # and duplicates. Skipping that here is not cosmetic: ` assets/* ` would
    # fail to match, the include allow-list would then reject everything, and
    # the checker would stop scanning files that really do ship.
    def _patterns(key: str) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in build.get(key, []):
            if not isinstance(item, str):
                continue
            pattern = item.strip()
            if not pattern or pattern in seen:
                continue
            seen.add(pattern)
            out.append(pattern)
        return out

    return {
        key: _patterns(key)
        for key in ("include", "exclude", "exclude_dirs", "exclude_files")
    }


def _plugin_stage_filter(repo_root: Path):
    """Return ``keep(path)`` — False for repo paths the plugin stage drops."""
    cache: dict[str, dict[str, list[str]]] = {}

    def keep(path: str) -> bool:
        prefix = PLUGINS_ROOT + "/"
        if not path.startswith(prefix):
            return True
        parts = PurePosixPath(path[len(prefix):]).parts
        if len(parts) < 2:
            # A loose file directly under plugin/plugins/. No plugin rules apply,
            # but _remove_private_runtime_artifacts sweeps the whole stage, so
            # .db/.log still go.
            return PurePosixPath(parts[-1]).suffix.lower() not in _PLUGIN_SKIP_SUFFIXES_FOLDED
        plugin_dir, relative = parts[0], PurePosixPath(*parts[1:])
        dir_parts = relative.parts[:-1]
        if dir_parts and dir_parts[0] in _PLUGIN_SKIP_ROOT_DIR_NAMES:
            return False
        if any(part in _PLUGIN_SKIP_DIR_NAMES for part in dir_parts):
            return False
        if relative.name in _PLUGIN_SKIP_FILE_NAMES:
            return False
        if relative.suffix in _PLUGIN_SKIP_SUFFIXES_EXACT:
            return False
        if relative.suffix.lower() in _PLUGIN_SKIP_SUFFIXES_FOLDED:
            return False

        rules = cache.setdefault(plugin_dir, _plugin_rules(repo_root, plugin_dir))
        if not rules:
            return True
        path_str = relative.as_posix()
        if any(_match_build_pattern(path_str, p) for p in rules.get("exclude", [])):
            return False
        # Both lists are also tested against every ancestor directory: the real
        # walk asks should_skip_path(is_dir=True) for each directory and prunes
        # the whole subtree, so `exclude = ["cache"]` drops cache/** even though
        # no file path equals "cache".
        for index in range(len(dir_parts)):
            candidate = "/".join(dir_parts[: index + 1])
            if any(
                _match_build_pattern(candidate, p)
                for p in rules.get("exclude_dirs", []) + rules.get("exclude", [])
            ):
                return False
        exclude_files = rules.get("exclude_files", [])
        if relative.name in exclude_files:
            return False
        if any(_match_build_pattern(path_str, p) for p in exclude_files):
            return False
        # `include` is an allow-list applied after every exclude has run: with
        # it present, anything unmatched is dropped from the stage.
        include = rules.get("include", [])
        if not include:
            return True
        return any(_match_build_pattern(path_str, p) for p in include)

    return keep


def _git_listed_offenders(repo_root: Path) -> set[str]:
    """Non-ASCII git-visible paths under the bundled roots.

    ``--cached --others --exclude-standard`` = tracked files plus untracked
    ones that are not gitignored. The ``--others`` half is what makes the
    check fire before ``git add``, while ``--exclude-standard`` keeps build
    outputs and downloaded weights out so the answer does not depend on
    whether the tree has been built.

    ``-z`` matters: without it git quote-escapes non-ASCII paths
    (``"static/\\344\\270\\203....mp3"``), which is exactly the set we are
    looking for.
    """
    try:
        raw = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=repo_root,
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8", errors="surrogateescape")
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"error: cannot list git files: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    staged = _plugin_stage_filter(repo_root)
    return {
        path
        for path in raw.split("\0")
        if path
        and _under_bundled_root(path)
        and staged(path)
        and not _is_ascii(PurePosixPath(path).name)
    }


def _archive_offenders(repo_root: Path) -> dict[str, str]:
    """Non-ASCII member names inside archives that get unpacked into the app.

    Keyed by the path the member will occupy once unpacked, so both the
    baseline and the violation message point at the bundled location; the
    value is the archive to edit.
    """
    offenders: dict[str, str] = {}

    def _record(members: list[str], dest_prefix: str, archive_rel: str) -> None:
        for member in members:
            # A ZIP written on Windows can carry backslash separators; the
            # unpacker normalizes them (`_safe_relative_path`), so a member like
            # `中文目录\plain.png` really lands as an ASCII file inside a CJK
            # directory — allowed. Without this the whole string reads as one
            # basename and the check rejects a file that signs fine.
            normalized = member.replace("\\", "/")
            if _is_ascii(PurePosixPath(normalized).name):
                continue
            offenders[f"{dest_prefix}/{normalized}"] = archive_rel

    for source_dir, dest_prefix in TAR_ARCHIVE_DESTS:
        directory = repo_root / source_dir
        if not directory.is_dir():
            continue
        for archive in sorted(directory.glob("*.tar.gz")):
            with tarfile.open(archive) as handle:
                # islnk/issym as well as isfile: `tar -xzm` materializes hard
                # links and symlinks as entries in static/ too, so a non-ASCII
                # link name reaches the payload exactly like a regular file.
                names = [
                    m.name
                    for m in handle.getmembers()
                    if m.isfile() or m.islnk() or m.issym()
                ]
            _record(names, dest_prefix, archive.relative_to(repo_root).as_posix())

    for archive_rel, dest_prefix in _pngtuber_archive_dests(repo_root):
        archive = repo_root / archive_rel
        if not archive.is_file():
            continue
        with zipfile.ZipFile(archive) as handle:
            names = [i.filename for i in handle.infolist() if not i.is_dir()]
        _record(names, dest_prefix, archive_rel)

    return offenders


def _load_json(path: Path, rel: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"error: cannot read {rel}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def _model_manifest_offenders(repo_root: Path) -> dict[str, str]:
    """Non-ASCII ``filename`` values in the downloaded-model manifests.

    Keyed by where the download lands, valued by the manifest to edit. This is
    what makes the two model roots useful in CI: the payload itself only exists
    after a build, but the name that will be written is committed.
    """
    offenders: dict[str, str] = {}
    for rel in MODEL_MANIFESTS:
        path = repo_root / rel
        if not path.is_file():
            continue
        manifest = _load_json(path, rel)
        if not isinstance(manifest, dict):
            print(f"error: invalid {rel}: top level must be an object", file=sys.stderr)
            raise SystemExit(2)

        entries: list[dict] = [manifest]
        assets = manifest.get("assets")
        if assets is not None:
            if not isinstance(assets, list):
                print(f"error: invalid {rel}: 'assets' must be a list", file=sys.stderr)
                raise SystemExit(2)
            entries.extend(item for item in assets if isinstance(item, dict))

        dest_dir = PurePosixPath(rel).parent.as_posix()
        for entry in entries:
            name = entry.get("filename")
            if isinstance(name, str) and name and not _is_ascii(PurePosixPath(name).name):
                offenders[f"{dest_dir}/{name}"] = rel
    return offenders


def _pngtuber_archive_dests(repo_root: Path) -> list[tuple[str, str]]:
    """(archive path, destination prefix) for each manifest-listed PNGTuber pack.

    Mirrors ``unpack_model``: the archive named by ``archive`` is expanded into
    ``static/pngtuber/<folder>/``. Entries missing either field are skipped —
    the unpacker rejects them too, so they never reach the payload.
    """
    manifest_path = repo_root / ZIP_MANIFEST_REL
    if not manifest_path.is_file():
        return []
    manifest = _load_json(manifest_path, ZIP_MANIFEST_REL)
    # A malformed manifest must be a loud error, not a silently empty scan —
    # "no packs found" and "the file is a list" would otherwise look identical.
    if not isinstance(manifest, dict):
        print(
            f"error: invalid {ZIP_MANIFEST_REL}: top level must be an object",
            file=sys.stderr,
        )
        raise SystemExit(2)
    models = manifest.get("models", [])
    if not isinstance(models, list):
        print(
            f"error: invalid {ZIP_MANIFEST_REL}: 'models' must be a list",
            file=sys.stderr,
        )
        raise SystemExit(2)

    packs_dir = PurePosixPath(ZIP_MANIFEST_REL).parent
    dests: list[tuple[str, str]] = []
    for model in models:
        if not isinstance(model, dict):
            continue
        folder = model.get("folder")
        archive = model.get("archive")
        if not isinstance(folder, str) or not isinstance(archive, str):
            continue
        dests.append(
            (
                (packs_dir / archive).as_posix(),
                f"{ZIP_MANIFEST_DEST_ROOT}/{folder}",
            )
        )
    return sorted(dests)


def _untracked_offenders(repo_root: Path) -> set[str]:
    """Non-ASCII paths found by walking the on-disk bundled roots.

    Only used with ``--include-untracked``: after a build these roots also
    hold generated payload (unpacked models, vite bundles, downloaded model
    weights) that no git listing can see.
    """
    offenders: set[str] = set()
    staged = _plugin_stage_filter(repo_root)
    for root in BUNDLED_ROOTS:
        base = repo_root / root
        if not base.is_dir():
            continue
        for current, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in WALK_EXCLUDE_DIRS]
            current_path = Path(current)
            for name in files:
                if _is_ascii(name):
                    continue
                relative = (current_path / name).relative_to(repo_root).as_posix()
                if staged(relative):
                    offenders.add(relative)
    return offenders


def collect_offenders(
    repo_root: Path, include_untracked: bool = False
) -> tuple[set[str], dict[str, str]]:
    """Return (bundled paths with non-ASCII names, path -> owning archive/manifest)."""
    from_archives = _archive_offenders(repo_root)
    from_archives.update(_model_manifest_offenders(repo_root))
    offenders = _git_listed_offenders(repo_root) | set(from_archives)
    if include_untracked:
        offenders |= _untracked_offenders(repo_root)
    return offenders, from_archives


def load_baseline(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    entries: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entries.add(stripped)
    return entries


def write_baseline(path: Path, offenders: set[str]) -> None:
    header = (
        "# Grandfathered non-ASCII filenames in bundled assets.\n"
        "#\n"
        "# See scripts/check_no_nonascii_asset_names.py for why these break macOS\n"
        "# Developer ID signing (codesign emits a hex-literal `identifier` in the\n"
        "# nested-code requirement, and the sealed resource directory then fails to\n"
        "# parse). Entries here are tolerated; anything NOT here fails the check.\n"
        "#\n"
        "# This list should only ever shrink. Regenerate after renaming or deleting\n"
        "# files with:  python scripts/check_no_nonascii_asset_names.py --update-baseline\n"
        "#\n"
        "# Paths are where the file lands inside the .app payload. A few of them do\n"
        "# not exist in the source tree because they ship inside an archive that is\n"
        "# unpacked at build time (assets/*.tar.gz, frontend/pngtuber-packs/*.zip).\n"
    )
    body = "".join(f"{entry}\n" for entry in sorted(offenders))
    path.write_text(header + body, encoding="utf-8")


def _baseline_growth(repo_root: Path, base_ref: str) -> list[str]:
    """Baseline entries present now but absent at ``base_ref``.

    The in-tree comparison alone cannot enforce "only shrinks": a PR that adds
    a non-ASCII asset *and* the matching baseline line has an empty
    ``offenders - baseline`` and sails through. Only a diff against the merge
    base sees that the list grew.
    """
    # Resolve the ref first. Without this, an unreachable ref (shallow clone
    # that never fetched origin/main, a typo, a renamed default branch) is
    # indistinguishable from "the baseline did not exist yet" — and the ratchet
    # would silently pass exactly when it is needed. Fail loudly instead.
    if subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}"],
        cwd=repo_root,
        capture_output=True,
    ).returncode != 0:
        print(
            f"error: --base {base_ref} does not resolve to a commit "
            "(shallow clone, or the ref was never fetched)",
            file=sys.stderr,
        )
        raise SystemExit(2)

    # The merge base, not the tip of base_ref. If main drops grandfathered
    # entries after this branch was cut, the branch still carries them and a
    # tip comparison would report those as newly added — a red build for
    # somebody else's cleanup.
    merge_base = subprocess.run(
        ["git", "merge-base", base_ref, "HEAD"],
        cwd=repo_root,
        capture_output=True,
    )
    if merge_base.returncode != 0:
        # No common ancestor: unrelated histories, or a clone shallow enough
        # that the ancestor was never fetched. Falling back to the tip would
        # quietly restore the bug this function exists to avoid, so say so.
        print(
            f"error: no merge base between {base_ref} and HEAD "
            "(unrelated histories, or the shared history was not fetched)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    reference = merge_base.stdout.decode().strip()

    rel = BASELINE_PATH.relative_to(repo_root).as_posix()
    completed = subprocess.run(
        ["git", "show", f"{reference}:{rel}"],
        cwd=repo_root,
        capture_output=True,
    )
    if completed.returncode != 0:
        # The ref is good but carries no baseline: first landing of this check.
        # Nothing to compare; the in-tree check still runs.
        return []

    before = {
        line.strip()
        for line in completed.stdout.decode("utf-8", errors="surrogateescape").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    return sorted(load_baseline(BASELINE_PATH) - before)


def _explain(count: int) -> str:
    return (
        f"\n{count} new non-ASCII bundled filename(s) found.\n"
        "\n"
        "Why this fails the build: Nuitka --mode=app puts the payload under\n"
        "projectneko_server.app/Contents/MacOS/, and codesign treats everything\n"
        "there as nested code. For each file it writes a requirement of the form\n"
        "`identifier <name> and anchor apple generic and ...` into CodeResources.\n"
        "A non-ASCII name becomes a hex literal (`identifier 0xe4b883...`), which\n"
        "is not valid requirement syntax, so the whole bundle then fails with\n"
        "`the sealed resource directory is invalid` — blocking signing,\n"
        "notarization and Steam upload. The error names no file, so this costs\n"
        "hours to trace after the fact.\n"
        "\n"
        "CI cannot catch this: build-desktop.yml signs ad-hoc (`--sign -`), whose\n"
        "requirements are `cdhash H\"...\"` and carry no identifier at all. Only the\n"
        "local Developer ID path (build_mac.sh) hits it.\n"
        "\n"
        "Fix: give the file an ASCII name (stable slug or id) and update whatever\n"
        "manifest references it. Do NOT add it to\n"
        "scripts/nonascii_asset_baseline.txt — that list is a ratchet for assets\n"
        "that predate this check and is only allowed to shrink.\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fail on new non-ASCII filenames in assets bundled into the desktop app."
        )
    )
    parser.add_argument(
        "--include-untracked",
        action="store_true",
        help=(
            "also walk the on-disk bundled roots (build outputs, unpacked models); "
            "off by default so the result does not depend on build state"
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="print every current offender (baselined included) and exit 0",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help=(
            "drop baseline entries that no longer exist; never adds (the list "
            "is a ratchet)"
        ),
    )
    parser.add_argument(
        "--base",
        metavar="REF",
        help=(
            "also fail if the baseline gained entries relative to REF "
            "(e.g. origin/main); PR-only, there is nothing to diff on a push"
        ),
    )
    args = parser.parse_args(argv)

    offenders, from_archives = collect_offenders(
        REPO_ROOT, include_untracked=args.include_untracked
    )

    if args.list:
        for entry in sorted(offenders):
            origin = from_archives.get(entry)
            print(f"{entry}" + (f"  (in {origin})" if origin else ""))
        print(f"\n{len(offenders)} non-ASCII bundled path(s).")
        return 0

    if args.update_baseline:
        if args.include_untracked:
            # Untracked payload is build-state dependent; baking it into the
            # baseline would make the file differ per machine.
            print(
                "error: --update-baseline refuses --include-untracked "
                "(the result would depend on local build state)",
                file=sys.stderr,
            )
            return 2
        # Shrink-only. Writing `offenders` wholesale would let the documented
        # workflow grandfather a brand-new violation: add the file, run this,
        # and `offenders - baseline` comes back empty. Intersecting instead
        # means the command can only ever drop entries that no longer exist.
        existing = load_baseline(BASELINE_PATH)
        kept = offenders & existing
        rejected = sorted(offenders - existing)
        write_baseline(BASELINE_PATH, kept)
        rel = BASELINE_PATH.relative_to(REPO_ROOT).as_posix()
        print(f"wrote {len(kept)} entr(ies) to {rel} ({len(existing) - len(kept)} dropped)")
        if rejected:
            print(
                f"\nerror: refusing to add {len(rejected)} new entr(ies) — the "
                "baseline may only shrink:",
                file=sys.stderr,
            )
            for entry in rejected:
                print(f"  - {entry}", file=sys.stderr)
            print(_explain(len(rejected)), file=sys.stderr)
            return 1
        return 0

    if args.base:
        grown = _baseline_growth(REPO_ROOT, args.base)
        if grown:
            rel = BASELINE_PATH.relative_to(REPO_ROOT).as_posix()
            print(
                f"{rel}  {CODE}  {len(grown)} entr(ies) added to the baseline "
                f"since {args.base}:",
                file=sys.stderr,
            )
            for entry in grown:
                print(f"  + {entry}", file=sys.stderr)
            print(_explain(len(grown)), file=sys.stderr)
            return 1

    baseline = load_baseline(BASELINE_PATH)
    new = sorted(offenders - baseline)
    stale = sorted(baseline - offenders)

    for entry in new:
        origin = from_archives.get(entry)
        where = f" (ships inside {origin})" if origin else ""
        print(f"{entry}  {CODE}  non-ASCII filename in bundled asset{where}")

    if stale and not new:
        # Renamed or deleted — progress, never a failure. Nudge only, so a PR
        # that legitimately removes an asset does not go red for it.
        rel = BASELINE_PATH.relative_to(REPO_ROOT).as_posix()
        print(
            f"note: {len(stale)} baseline entr(ies) no longer exist — "
            f"run `python scripts/check_no_nonascii_asset_names.py "
            f"--update-baseline` to shrink {rel}:",
            file=sys.stderr,
        )
        for entry in stale[:10]:
            print(f"  - {entry}", file=sys.stderr)
        if len(stale) > 10:
            print(f"  … and {len(stale) - 10} more", file=sys.stderr)

    if new:
        print(_explain(len(new)), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
