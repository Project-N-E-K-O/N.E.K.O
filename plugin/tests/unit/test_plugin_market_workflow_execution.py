"""Execute reusable-workflow Python blocks and the real SDK packaging checks.

GitHub upload/release actions are intentionally not invoked by these local tests.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import zipfile
from pathlib import Path
from string import Template

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.plugin_unit


def workflow(mode):
    return yaml.safe_load((ROOT / f".github/workflows/plugin-market-{mode}.yml").read_text(encoding="utf-8"))["jobs"][mode]


def python_block(mode, name):
    script = next(s["run"] for s in workflow(mode)["steps"] if s["name"] == name)
    return script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


def execute(mode, step, workspace, env):
    return subprocess.run([sys.executable, "-c", python_block(mode, step)], cwd=workspace, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=120)


def lint(mode, workspace, env):
    step = next(s for s in workflow(mode)["steps"] if s["name"] == "Ruff check")
    command = [Template(token).substitute(env) for token in shlex.split(step["run"])]
    return subprocess.run(command, cwd=workspace / step.get("working-directory", "."), env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=120)


def environment(tmp_path, mode, directory="", name=""):
    return {**os.environ, "PLUGIN_ID": "voice_bridge", "PLUGIN_DIRECTORY": directory, "PACKAGE_NAME": name,
            "MARKET_MODE": mode, "GITHUB_ENV": str(tmp_path / "github-env"),
            "GITHUB_REPOSITORY": "alice/arbitrary-suite", "GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v0.7.1",
            "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "321", "GITHUB_RUN_ATTEMPT": "2",
            "NEKO_REPOSITORY": "Project-N-E-K-O/N.E.K.O", "NEKO_REF": "main", "NEKO_COMMIT": "b" * 40,
            "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1"}


def cli(*args, cwd, env):
    result = subprocess.run([sys.executable, "-m", "plugin.neko_plugin_cli.cli", *args],
                            cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.mark.parametrize("mode", ["verify", "release"])
@pytest.mark.parametrize("directory,name,tooling,expected_directory", [
    ("", "", "none", ""), ("./", "", "none", ""),
    ("plugins/bridge", "neko_voice_bridge-0.7.1.neko-plugin", "none", "plugins/bridge"),
    ("./plugins/bridge/", "neko_voice_bridge-0.7.1.neko-plugin", "none", "plugins/bridge"),
    ("plugins/bridge", "neko_voice_bridge-0.7.1.neko-plugin", "github", "plugins/bridge"),
    ("plugins/bridge", "neko_voice_bridge-0.7.1.neko-plugin", "vscode", "plugins/bridge"),
    ("plugins/voice bridge", "neko_voice_bridge-0.7.1.neko-plugin", "both", "plugins/voice bridge"),
])
def test_workflow_mount_build_and_evidence_use_actual_selected_plugin(tmp_path, mode, directory, name, tooling, expected_directory):
    env = environment(tmp_path, mode, directory, name)
    repo = tmp_path / "plugin-repo"
    source = repo / directory
    cli("init", "voice_bridge", "--output", str(source), cwd=ROOT, env=env)
    config = source / "plugin.toml"
    config.write_text(config.read_text(encoding="utf-8").replace('version = "0.1.0"', 'version = "0.7.1"'), encoding="utf-8")
    # A monorepo normally keeps its caller workflows/editor tooling at its root.
    if source != repo:
        for child in (".github", ".vscode", ".gitignore"):
            (source / child).rename(repo / child)
    release = repo / ".github/workflows/release.yml"
    release.write_text("name: fixture release\n", encoding="utf-8")
    plugin_owned = {}
    if tooling in {"github", "both"}:
        plugin_owned[".github/workflows/custom.yml"] = "name: plugin-owned custom workflow\n"
    if tooling in {"vscode", "both"}:
        plugin_owned[".vscode/launch.json"] = '{"version":"0.2.0","configurations":[]}'
    if tooling == "both":
        plugin_owned[".github/workflows/verify.yml"] = "name: plugin-owned verifier\n"
        settings = json.loads((repo / ".vscode/settings.json").read_text(encoding="utf-8"))
        settings["pluginOwnedMarker"] = "preserve me"
        plugin_owned[".vscode/settings.json"] = json.dumps(settings)
    for relative, content in plugin_owned.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (repo / "external-program.txt").write_text("outside selected plugin", encoding="utf-8")
    if expected_directory:
        external = repo / "external-program/main.py"
        external.parent.mkdir()
        external.write_text("import os\n", encoding="utf-8")
    neko = tmp_path / "neko"
    neko.mkdir()
    mounted = execute(mode, "Mount plugin into N.E.K.O tree", tmp_path, env)
    assert mounted.returncode == 0, mounted.stderr
    env.update(dict(line.split("=", 1) for line in (tmp_path / "github-env").read_text(encoding="utf-8").splitlines()))
    assert env["MARKET_PLUGIN_DIRECTORY"] == expected_directory
    plugins = neko / "plugin/plugins"
    if expected_directory:
        assert not (plugins / "voice_bridge/external-program.txt").exists()
    for relative, content in plugin_owned.items():
        assert (plugins / "voice_bridge" / relative).read_text(encoding="utf-8") == content
    for relative in (".github/workflows/verify.yml", ".vscode/settings.json", ".vscode/tasks.json", ".gitignore"):
        assert (plugins / "voice_bridge" / relative).is_file()
    linted = lint(mode, tmp_path, env)
    assert linted.returncode == 0, linted.stdout + linted.stderr
    target = neko / "plugin/neko_plugin_cli/target"
    cli("sync", str(plugins / "voice_bridge"), "--clean", cwd=neko, env=env)
    checked = cli("check", "-r", "voice_bridge", "--plugins-root", str(plugins), "-t", str(target), cwd=neko, env=env)
    assert "payload_hash_verified=True" in checked.stdout
    evidence_result = execute(mode, "Write Market evidence", neko, env)
    assert evidence_result.returncode == 0, evidence_result.stderr
    evidence = json.loads((target / "market-evidence.json").read_text(encoding="utf-8"))
    package = target / (name or "voice_bridge.neko-plugin")
    assert evidence["plugin_directory"] == expected_directory
    assert evidence["package"]["name"] == package.name
    assert evidence["package"]["sha256"] == hashlib.sha256(package.read_bytes()).hexdigest()
    assert evidence["package"]["size"] == package.stat().st_size
    assert evidence["workflow_run_attempt"] == 2
    with zipfile.ZipFile(package) as archive:
        assert "manifest.toml" in archive.namelist()
        assert "payload/plugins/voice_bridge/plugin.toml" in archive.namelist()
    if mode == "release":
        assert evidence["version"] == "0.7.1"
        assert evidence["ref_name"] == "v0.7.1"
    steps = workflow(mode)["steps"]
    upload = steps[-1]["with"]["files" if mode == "release" else "path"]
    assert "${{ env.MARKET_PACKAGE_NAME }}" in upload
    assert "${{ env.PLUGIN_ID }}-market-evidence" in str(steps)


@pytest.mark.parametrize("mode", ["verify", "release"])
def test_selected_directory_is_validated_before_lint_and_dependency_execution(mode):
    steps = [step["name"] for step in workflow(mode)["steps"]]
    assert steps.index("Mount plugin into N.E.K.O tree") < steps.index("Ruff check")
    assert steps.index("Ruff check") < steps.index("Sync plugin dependencies")


@pytest.mark.parametrize("mode", ["verify", "release"])
@pytest.mark.parametrize("directory", ["", "plugins/bridge", "./plugins/voice bridge/"])
@pytest.mark.parametrize("code,rule", [
    ("import os\n", "F401"),
    ("import os  # noqa: F401\n", "F401"),
    ("def broken(:\n", "SyntaxError"),
])
def test_selected_plugin_lint_violation_blocks_even_when_gitignore_hides_it(tmp_path, mode, directory, code, rule):
    env = environment(tmp_path, mode, directory)
    repo = tmp_path / "plugin-repo"
    source = repo / directory
    source.mkdir(parents=True)
    (source / "plugin.toml").write_text('[plugin]\nid="voice_bridge"\nversion="0.7.1"\n', encoding="utf-8")
    (source / "main.py").write_text(code, encoding="utf-8")
    (repo / ".github/workflows").mkdir(parents=True)
    (repo / ".github/workflows/release.yml").write_text("name: fixture release\n", encoding="utf-8")
    neko = tmp_path / "neko"
    neko.mkdir()
    # A repository cannot exempt its own checked-out Python files from lint.
    (repo / ".git").mkdir()
    (repo / ".gitignore").write_text("*.py\n", encoding="utf-8")
    mounted = execute(mode, "Mount plugin into N.E.K.O tree", tmp_path, env)
    assert mounted.returncode == 0, mounted.stderr
    env.update(dict(line.split("=", 1) for line in (tmp_path / "github-env").read_text(encoding="utf-8").splitlines()))
    linted = lint(mode, tmp_path, env)
    assert linted.returncode != 0
    assert rule in linted.stdout, linted.stdout + linted.stderr
    assert not (neko / "plugin/neko_plugin_cli/target/voice_bridge.neko-plugin").exists()
    assert not (neko / "plugin/neko_plugin_cli/target/market-evidence.json").exists()


@pytest.mark.parametrize("mode", ["verify", "release"])
@pytest.mark.parametrize("directory,name", [
    ("../escape", ""), ("/absolute", ""), ("plugins/../bridge", ""), ("plugins\\bridge", ""),
    ("plugins/%2e%2e", ""), ("plugins/bridge\nEVIL=1", ""), ("", "../outside.neko-plugin"),
    ("", "voice*.neko-plugin"), ("", "voice.neko-plugin\nEVIL=1"),
])
def test_unsafe_paths_and_asset_names_fail_before_mount(tmp_path, mode, directory, name):
    (tmp_path / "plugin-repo").mkdir()
    (tmp_path / "neko").mkdir()
    result = execute(mode, "Mount plugin into N.E.K.O tree", tmp_path, environment(tmp_path, mode, directory, name))
    assert result.returncode != 0
    expected = "Invalid package-name" if name else "Invalid plugin-directory"
    assert expected in result.stderr, result.stderr
    assert not (tmp_path / "github-env").exists()
    assert not (tmp_path / "neko/plugin").exists()


@pytest.mark.parametrize("mode", ["verify", "release"])
@pytest.mark.parametrize("change", ["identity", "version"])
def test_identity_or_tag_mismatch_cannot_produce_workflow_evidence(tmp_path, mode, change):
    repo = tmp_path / "plugin-repo"
    repo.mkdir()
    (tmp_path / "neko").mkdir()
    plugin_id = "other" if change == "identity" else "voice_bridge"
    version = "9.9.9" if change == "version" else "0.7.1"
    (repo / "plugin.toml").write_text(f'[plugin]\nid="{plugin_id}"\nversion="{version}"\n', encoding="utf-8")
    result = execute(mode, "Mount plugin into N.E.K.O tree", tmp_path, environment(tmp_path, mode))
    assert result.returncode != 0
    assert not (tmp_path / "github-env").exists()


@pytest.mark.parametrize("mode", ["verify", "release"])
@pytest.mark.parametrize("directory", [" plugins/bridge", "./ plugins/bridge", "plugins/ bridge", "plugins/bridge "])
def test_ambiguous_directory_cannot_claim_a_different_repository_target(tmp_path, mode, directory):
    repo = tmp_path / "plugin-repo"
    # Ordinary Win32 paths cannot create a literal trailing-space component.
    # This spelling must be rejected by policy before any filesystem lookup.
    actual = repo / (directory.rstrip() if directory.endswith(" ") else directory)
    actual.mkdir(parents=True)
    (actual / "plugin.toml").write_text('[plugin]\nid="voice_bridge"\nversion="0.7.1"\n', encoding="utf-8")
    canonical = repo / "plugins/bridge"
    canonical.mkdir(parents=True, exist_ok=True)
    (canonical / "plugin.toml").write_text('[plugin]\nid="voice_bridge"\nversion="0.7.1"\n', encoding="utf-8")
    (repo / ".github/workflows").mkdir(parents=True)
    (repo / ".github/workflows/release.yml").write_text("name: fixture release\n", encoding="utf-8")
    (tmp_path / "neko").mkdir()
    result = execute(mode, "Mount plugin into N.E.K.O tree", tmp_path, environment(tmp_path, mode, directory))
    assert result.returncode != 0
    assert "Invalid plugin-directory" in result.stderr
    assert not (tmp_path / "github-env").exists()
    assert not (tmp_path / "neko/plugin").exists()


@pytest.mark.parametrize("mode", ["verify", "release"])
def test_plugin_owned_invalid_tooling_is_preserved_and_still_blocks_sdk_check(tmp_path, mode):
    env = environment(tmp_path, mode, "plugins/bridge")
    repo = tmp_path / "plugin-repo"
    source = repo / "plugins/bridge"
    cli("init", "voice_bridge", "--output", str(source), cwd=ROOT, env=env)
    config = source / "plugin.toml"
    config.write_text(config.read_text(encoding="utf-8").replace('version = "0.1.0"', 'version = "0.7.1"'), encoding="utf-8")
    for child in (".github", ".vscode", ".gitignore"):
        (source / child).rename(repo / child)
    (repo / ".github/workflows/release.yml").write_text("name: fixture release\n", encoding="utf-8")
    (source / ".vscode").mkdir()
    (source / ".vscode/settings.json").write_text("{ invalid JSON", encoding="utf-8")
    neko = tmp_path / "neko"
    neko.mkdir()
    mounted = execute(mode, "Mount plugin into N.E.K.O tree", tmp_path, env)
    assert mounted.returncode == 0, mounted.stderr
    plugins = neko / "plugin/plugins"
    settings = plugins / "voice_bridge/.vscode/settings.json"
    assert settings.read_text(encoding="utf-8") == "{ invalid JSON"
    target = neko / "plugin/neko_plugin_cli/target"
    checked = subprocess.run(
        [sys.executable, "-m", "plugin.neko_plugin_cli.cli", "check", "-r", "voice_bridge",
         "--plugins-root", str(plugins), "-t", str(target)], cwd=neko, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert checked.returncode != 0
    assert ".vscode/settings.json" in checked.stderr
    assert not (target / "voice_bridge.neko-plugin").exists()
    assert not (target / "market-evidence.json").exists()
