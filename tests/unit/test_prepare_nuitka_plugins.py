from __future__ import annotations

import json
from pathlib import Path

from scripts.check_nuitka_dist import _check_plugin_stage, _check_plugin_tomls
from scripts.prepare_nuitka_plugins import install_plugins, prepare_plugins


def _write(path: Path, content: str = "runtime\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_prepare_and_install_plugins_apply_neko_build_rules(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    plugin_dir = project_root / "plugin" / "plugins" / "demo_plugin"
    _write(project_root / "launcher.py", "print('launcher')\n")
    _write(plugin_dir / "plugin.toml", '[plugin]\nid = "demo_plugin"\n')
    _write(
        plugin_dir / "pyproject.toml",
        "\n".join(
            [
                "[project]",
                'name = "demo-plugin"',
                'version = "0.1.0"',
                "dependencies = []",
                "",
                "[tool.neko.build]",
                'exclude_dirs = ["tests", "local_logs"]',
                'exclude_files = ["README.md"]',
                'exclude = ["*.tmp"]',
                "",
            ]
        ),
    )
    _write(plugin_dir / "__init__.py")
    _write(plugin_dir / "runtime.py")
    _write(plugin_dir / "data layer" / "worker.py")
    _write(plugin_dir / "tests" / "__init__.py")
    _write(plugin_dir / "tests" / "test_runtime.py")
    _write(plugin_dir / "local_logs" / "private.txt")
    _write(plugin_dir / "README.md")
    _write(plugin_dir / "scratch.tmp")
    _write(plugin_dir / "store.db")
    _write(plugin_dir / "runtime.log")

    result = prepare_plugins(
        project_root=project_root,
        plugins_root=Path("plugin/plugins"),
        stage_dir=Path("build/nuitka-plugins"),
        source_launcher=Path("launcher.py"),
        generated_launcher=Path("build_nuitka_launcher.py"),
    )

    stage_plugin = result.stage_dir / "demo_plugin"
    assert (stage_plugin / "runtime.py").is_file()
    assert (stage_plugin / "data layer" / "worker.py").is_file()
    assert not (stage_plugin / "tests").exists()
    assert not (stage_plugin / "local_logs").exists()
    assert not (stage_plugin / "README.md").exists()
    assert not (stage_plugin / "scratch.tmp").exists()
    assert not (stage_plugin / "store.db").exists()
    assert not (stage_plugin / "runtime.log").exists()

    generated = result.generated_launcher.read_text(encoding="utf-8")
    assert "--nofollow-import-to=plugin.plugins.demo_plugin.tests" in generated
    assert "plugin.plugins.demo_plugin.data layer" not in generated
    assert generated.endswith("print('launcher')\n")

    manifest = json.loads(
        (result.stage_dir.parent / "nuitka-plugin-stage.json").read_text(encoding="utf-8")
    )
    assert "demo_plugin/tests/" in manifest["excluded_paths"]
    assert "plugin.plugins.demo_plugin.tests" in manifest["excluded_modules"]

    destination = project_root / "dist" / "Xiao8" / "plugin" / "plugins"
    _write(destination / "stale_plugin" / "plugin.toml")
    install_plugins(stage_dir=result.stage_dir, destination_dir=destination)

    assert not (destination / "stale_plugin").exists()
    assert (destination / "demo_plugin" / "runtime.py").is_file()
    assert not (destination / ".nuitka-stage.json").exists()


def test_prepare_keeps_shared_plugin_runtime_directory(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    _write(project_root / "launcher.py", "pass\n")
    _write(project_root / "plugin" / "plugins" / "__init__.py")
    _write(project_root / "plugin" / "plugins" / "_shared" / "__init__.py")
    _write(project_root / "plugin" / "plugins" / "_shared" / "helper.py")

    result = prepare_plugins(
        project_root=project_root,
        plugins_root=Path("plugin/plugins"),
        stage_dir=Path("build/nuitka-plugins"),
        source_launcher=Path("launcher.py"),
        generated_launcher=Path("build_nuitka_launcher.py"),
    )

    assert result.plugin_dirs == ("_shared",)
    assert (result.stage_dir / "__init__.py").is_file()
    assert (result.stage_dir / "_shared" / "helper.py").is_file()


def test_prepare_preserves_allowed_bundled_napcat_launcher(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    _write(project_root / "launcher.py", "pass\n")
    plugin_dir = project_root / "plugin" / "plugins" / "qq_auto_reply"
    _write(plugin_dir / "plugin.toml", '[plugin]\nid = "qq_auto_reply"\n')
    _write(plugin_dir / "NapCat.Shell" / "launcher.bat", "@echo off\n")

    result = prepare_plugins(
        project_root=project_root,
        plugins_root=Path("plugin/plugins"),
        stage_dir=Path("build/nuitka-plugins"),
        source_launcher=Path("launcher.py"),
        generated_launcher=Path("build_nuitka_launcher.py"),
    )

    assert (result.stage_dir / "qq_auto_reply" / "NapCat.Shell" / "launcher.bat").is_file()


def test_dist_check_matches_stage_and_allows_shared_directory(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    dist_root = tmp_path / "dist"
    installed = dist_root / "plugin" / "plugins"
    _write(stage / "demo" / "plugin.toml")
    _write(stage / "demo" / "runtime.py")
    _write(stage / "_shared" / "helper.py")
    install_plugins(stage_dir=stage, destination_dir=installed)

    assert _check_plugin_tomls(dist_root) == []
    assert _check_plugin_stage(dist_root, stage) == []

    _write(installed / "demo" / "README.md")
    issues = _check_plugin_stage(dist_root, stage)
    assert len(issues) == 1
    assert "unstaged file" in issues[0]


def test_desktop_workflows_use_filtered_plugin_stage() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    workflow_paths = (
        repo_root / ".github" / "workflows" / "build-desktop.yml",
        repo_root / ".github" / "workflows" / "build-desktop-linux.yml",
    )

    for workflow_path in workflow_paths:
        workflow = workflow_path.read_text(encoding="utf-8")
        assert "scripts/prepare_nuitka_plugins.py prepare" in workflow
        assert "build_nuitka_launcher.py" in workflow
        assert "scripts/prepare_nuitka_plugins.py install" in workflow
        assert "--plugin-stage build/nuitka-plugins" in workflow
        assert "--include-data-dir=plugin/plugins=plugin/plugins" not in workflow
        assert 'NUITKA_OPTS="$NUITKA_OPTS --nofollow-import-to=plugin.plugins"' not in workflow
