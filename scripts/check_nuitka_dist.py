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

"""Sanity-check a Nuitka standalone dist directory.

Run after ``build_nuitka.bat`` (local) or the ``build-desktop.yml`` Nuitka step
(CI). Exits non-zero on the first missing critical asset, before signing or
packaging into Electron.

## What it catches

- ``--include-data-dir=`` silently dropped files that Nuitka treats as code
  (e.g., the historical ``plugin/neko-plugin-cli/`` bug where ``--include-data-dir``
  filtered out ``.py`` and only ``docs/*.md`` reached dist).
- Whole top-level directories missing because of file-lock collisions in
  ``rmdir /s /q dist\\Xiao8`` followed by ``move dist\\launcher.dist dist\\Xiao8``
  nesting on top of the leftover (each such collision turns into a partial,
  half-broken bundle that boots but lacks config/static/templates).
- Built-in plugins that lost their ``plugin.toml`` (means the plugin scanner
  will produce zero plugins at runtime).

## What it does NOT do

- Does not launch the exe. That's expensive, platform-specific, and would
  drag in a network of subprocesses.
- Does not check compiled-into-exe Python modules (no way to enumerate them
  from outside the binary). Coverage of that surface lives in:
  - ``tests/unit/test_no_hyphen_python_packages.py`` — the source-level lint
  - the L3 doc rule in CLAUDE.md
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import tomllib
from pathlib import Path


# Critical asset manifest: relative paths that **must** exist under the dist root. The second item is a filename pattern list;
# None means "the directory only needs to be non-empty"; otherwise it must contain at least one file matching the pattern.
_REQUIRED_ASSETS: tuple[tuple[str, str | None], ...] = (
    ("projectneko_server.exe", None),  # Main entry-point executable (platform must be renamed on macOS/Linux)
    ("config", "core_config.json"),
    ("config", "characters.json"),
    ("config", "api_providers.json"),
    # changelog/.md and survey/.json are data files; --include-package=config bundles only .py files and omits them;
    # verify they exist in dist or /api/changelog and /api/survey (Steam-only) will read nothing after packaging.
    ("config/changelog", None),
    ("config/surveys", None),
    # Localized character seed directory config/characters/<locale>.json (PR #1282). --include-package=config
    # bundles only .py files and omits this directory; verify at least one locale JSON is present or character seeds fall back to the wrong language for non-default users.
    ("config/characters", "*.json"),
    ("static", None),
    # Vendored MMD libraries must ship with their upstream license notices.
    ("static/libs", "three-mmd.module.js"),
    ("static/libs", "three-mmd-physics-ammo.module.js"),
    ("static/libs", "THIRD_PARTY_NOTICES.md"),
    ("static/libs/licenses", "THREE-MMD-LICENSE.txt"),
    ("static/libs/licenses", "BABYLON-MMD-LICENSE.txt"),
    # Vendor SDKs for the peer-to-peer transfer page are loaded lazily by transport (pinned versions, bundled with the app, never fetched from a CDN) along with their licenses.
    ("static/libs", "trtc.js"),
    ("static/libs", "livekit-client.umd.js"),
    ("static/libs/licenses", "TRTC.LICENSE"),
    ("static/libs/licenses", "LIVEKIT.LICENSE"),
    # Built-in Live2D models: source archives are assets/<name>.tar.gz, and build_frontend extracts them to static/<name>/.
    # The default character uses yui-lolita, which is also the fallback and tutorial model if loading fails; yui-origin is bundled too.
    # Checking only model3.json does not catch a partial extraction — moc3 and textures are required for loading, so assert all of them.
    ("static/yui-lolita", "yui-lolita.model3.json"),
    ("static/yui-lolita", "yui-lolita.moc3"),
    ("static/yui-lolita/yui-lolita.4096", "texture_00.png"),
    ("static/yui-origin", "yui-origin.model3.json"),
    ("static/yui-origin", "yui-origin.moc3"),
    ("static/yui-origin/yui-origin.4096", "texture_00.png"),
    ("static/pngtuber/yui-lolita", "model.json"),
    ("static/pngtuber/yui-origin", "model.json"),
    ("static/pngtuber/yui-sister", "model.json"),
    # React chat-window build artifacts (in a gitignored directory); if missing, chat panels in index.html/chat.html return 404.
    # CSS is generated separately by sync-css.mjs and can be missing independently of iife.js, so assert both.
    ("static/react/neko-chat", "neko-chat-window.iife.js"),
    ("static/react/neko-chat", "neko-chat-window.css"),
    ("templates", None),
    ("assets", None),
    ("data/browser_use_prompts", None),
    # Offline model assets: prepare_*.py scripts download these before Nuitka; weights are not checked in, and frozen builds have no runtime download path;
    # if omitted, local endpoint detection, speaker recognition, and vector memory silently degrade. CI also checks every file ("Verify bundled
    # offline assets"); this check catches silent drift such as a missing directory or weights. For embeddings, assert the actual file:
    # an interrupted download can leave only an empty profile subdirectory in data/embedding_models, which passes the non-empty-directory check.
    # Check only int8: CI prepares --variant both, while local Steam builds include int8 only (fp32 weights are 810MB,
    # adding ~370MB to the final compressed package, which is not worthwhile for a fallback used by very few CPUs). This is a
    # shared gate, so require only the asset both environments guarantee; CI separately enforces fp32 in the build-desktop.yml
    # "Verify bundled offline assets" step.
    ("data/embedding_models/local-text-retrieval-v1", "tokenizer.json"),
    ("data/embedding_models/local-text-retrieval-v1/onnx", "model_quantized.onnx"),
    ("data/embedding_models/local-text-retrieval-v1/onnx", "model_quantized.onnx_data"),
    ("main_logic/asr_client/endpointing/models", "silero_vad.onnx"),
    ("main_logic/asr_client/endpointing/models", "smart_turn_v3.onnx"),
    ("main_logic/asr_client/speaker_shadow/models", "campplus-zh-en-advanced.onnx"),
    # bilibili-api-dev imports PyCryptodomeX through the ``Cryptodome`` namespace.
    # Its native cipher modules cannot be compiled into the main executable, so
    # their absence here proves the QR-login dependency closure is incomplete.
    ("Cryptodome/Cipher", "_raw_aes.*"),
    ("frontend/plugin-manager/dist", "index.html"),
    ("plugin/plugins", None),
    # In-app OpenClaw guide and images, served by agent_router through /api/agent/openclaw/guide/*; data-only directory.
    ("docs/zh-CN/guide", None),
)

# Every subdirectory in the bundled plugins directory must contain plugin.toml; catches cases where the entire ``plugin/plugins``
# directory is included via ``--include-data-dir`` as an empty shell.
_PLUGIN_TOML_REQUIRED_PARENT = "plugin/plugins"

# These plugins are distributed exclusively through the plugin marketplace.
# Shipping one here would recreate a read-only built-in copy that conflicts
# with market installation and upgrades using the same plugin ID.
_MARKETPLACE_ONLY_PLUGIN_IDS = frozenset(
    {"neko_warthunder", "study_companion", "galgame_plugin"}
)


def _plugin_manifest_id(manifest_path: Path) -> str | None:
    try:
        with manifest_path.open("rb") as file_obj:
            manifest = tomllib.load(file_obj)
    except (OSError, tomllib.TOMLDecodeError):
        return None

    plugin = manifest.get("plugin")
    if not isinstance(plugin, dict):
        return None
    plugin_id = plugin.get("id")
    if not isinstance(plugin_id, str):
        return None
    normalized = plugin_id.strip()
    return normalized or None


def _check_asset(dist_root: Path, rel: str, must_contain: str | None) -> str | None:
    p = dist_root / rel
    if rel.endswith(".exe") or rel.endswith(".bin"):
        # Platform-specific: .exe on Windows, no extension on Linux/macOS. Allow a fallback.
        if not p.exists():
            stem = p.with_suffix("")
            if stem.exists():
                p = stem
        if not p.is_file() or p.stat().st_size == 0:
            return f"missing or empty: {rel} (also tried {p.with_suffix('').name})"
        return None
    if not p.exists():
        return f"missing: {rel}"
    if must_contain is None:
        if p.is_dir() and not any(p.iterdir()):
            return f"empty directory: {rel}"
        return None
    target = p / must_contain
    if "*" in must_contain:
        matches = list(p.glob(must_contain))
        if not matches:
            return f"no file matching {must_contain} in {rel}/"
        return None
    if not target.is_file():
        return f"missing: {rel}/{must_contain}"
    return None


def _check_plugin_tomls(dist_root: Path) -> list[str]:
    plugins_dir = dist_root / _PLUGIN_TOML_REQUIRED_PARENT
    if not plugins_dir.is_dir():
        # _check_asset above already reports this; do not duplicate the check.
        return []
    issues: list[str] = []
    plugin_subdirs = [p for p in plugins_dir.iterdir() if p.is_dir()]
    if not plugin_subdirs:
        issues.append(f"no plugin subdirectories under {_PLUGIN_TOML_REQUIRED_PARENT}/")
        return issues
    for sub in plugin_subdirs:
        if sub.name.startswith("_"):
            continue
        manifest_path = sub / "plugin.toml"
        manifest_id = _plugin_manifest_id(manifest_path)
        if (
            sub.name in _MARKETPLACE_ONLY_PLUGIN_IDS
            or manifest_id in _MARKETPLACE_ONLY_PLUGIN_IDS
        ):
            issues.append(
                f"marketplace-only plugin bundled: {sub.relative_to(dist_root).as_posix()}"
            )
            continue
        if not manifest_path.is_file():
            issues.append(f"plugin missing plugin.toml: {sub.relative_to(dist_root).as_posix()}")
    return issues


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_plugin_stage(dist_root: Path, stage_root: Path) -> list[str]:
    """Require the installed plugin payload to exactly match the filtered stage."""

    installed_root = dist_root / _PLUGIN_TOML_REQUIRED_PARENT
    if not stage_root.is_dir():
        return [f"plugin stage does not exist: {stage_root}"]
    if not installed_root.is_dir():
        return []  # The regular required-asset check reports this more clearly.

    staged_files = {
        path.relative_to(stage_root).as_posix(): path
        for path in stage_root.rglob("*")
        if path.is_file()
    }
    installed_files = {
        path.relative_to(installed_root).as_posix(): path
        for path in installed_root.rglob("*")
        if path.is_file()
    }

    issues: list[str] = []
    missing = sorted(staged_files.keys() - installed_files.keys())
    unexpected = sorted(installed_files.keys() - staged_files.keys())
    if missing:
        issues.append(f"plugin payload missing {len(missing)} staged file(s): {missing[:5]}")
    if unexpected:
        issues.append(f"plugin payload contains {len(unexpected)} unstaged file(s): {unexpected[:5]}")

    mismatched = [
        relative
        for relative in sorted(staged_files.keys() & installed_files.keys())
        if _file_digest(staged_files[relative]) != _file_digest(installed_files[relative])
    ]
    if mismatched:
        issues.append(f"plugin payload changed after staging ({len(mismatched)} file(s)): {mismatched[:5]}")
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "dist_root",
        nargs="?",
        default="dist/Xiao8",
        help="Path to Nuitka standalone dist root (default: dist/Xiao8)",
    )
    parser.add_argument(
        "--plugin-stage",
        type=Path,
        help=(
            "Filtered built-in plugin stage produced by prepare_nuitka_plugins.py; "
            "when provided, the installed plugin payload must match it exactly"
        ),
    )
    args = parser.parse_args(argv)

    dist_root = Path(args.dist_root).resolve()
    if not dist_root.is_dir():
        print(f"[FAIL] dist root does not exist or is not a directory: {dist_root}", file=sys.stderr)
        return 1

    issues: list[str] = []
    for rel, contains in _REQUIRED_ASSETS:
        problem = _check_asset(dist_root, rel, contains)
        if problem:
            issues.append(problem)

    issues.extend(_check_plugin_tomls(dist_root))
    if args.plugin_stage is not None:
        issues.extend(_check_plugin_stage(dist_root, args.plugin_stage.resolve()))

    if issues:
        print(f"[FAIL] Nuitka dist verification failed for {dist_root}:", file=sys.stderr)
        for it in issues:
            print(f"  - {it}", file=sys.stderr)
        print(
            "\nHints:\n"
            "  - 'missing config/static/templates' often means rmdir on dist\\Xiao8 "
            "failed (file lock from a previous run); kill all neko/projectneko "
            "processes and rebuild.\n"
            "  - 'plugin missing plugin.toml' means --include-data-dir=plugin/plugins "
            "ran but the source dir is empty/wrong.\n"
            "  - For Python packages that were missing on import (e.g., the historical "
            "neko-plugin-cli case), see tests/unit/test_no_hyphen_python_packages.py "
            "and the 'Nuitka packaging caveats' section in CLAUDE.md.",
            file=sys.stderr,
        )
        return 1

    print(f"[OK] Nuitka dist looks healthy: {dist_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
