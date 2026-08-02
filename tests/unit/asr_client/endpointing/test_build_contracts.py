from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[4]


def test_pyinstaller_bundles_voice_turn_assets():
    spec = (ROOT / "specs" / "launcher.spec").read_text(encoding="utf-8")
    assert spec.count("'main_logic/asr_client/endpointing/models'") == 2
    assert "voice_turn_assets_present" in spec
    assert re.search(
        r"pkg == ['\"]onnxruntime['\"] and voice_turn_assets_present",
        spec,
    )


def test_nuitka_workflows_prepare_bundle_and_verify_voice_turn_assets():
    desktop_workflow = (ROOT / ".github/workflows/build-desktop.yml").read_text(
        encoding="utf-8"
    )
    linux_workflow = (ROOT / ".github/workflows/build-desktop-linux.yml").read_text(
        encoding="utf-8"
    )

    for workflow in (desktop_workflow, linux_workflow):
        assert "scripts/prepare_voice_turn_assets.py" in workflow
        assert (
            "--include-data-dir=main_logic/asr_client/endpointing/models="
            "main_logic/asr_client/endpointing/models"
        ) in workflow
        assert (
            "hashFiles('main_logic/asr_client/endpointing/models/manifest.json')"
            in workflow
        )

    assert (
        'endpointing_assets="$NEKO_NUITKA_RUNTIME_DIR/main_logic/asr_client/'
        'endpointing/models"'
        in desktop_workflow
    )
    assert (
        'endpointing_assets="dist/Xiao8/main_logic/asr_client/endpointing/models"'
        in linux_workflow
    )
    for workflow in (desktop_workflow, linux_workflow):
        assert '--asset-dir "$endpointing_assets" --offline' in workflow
        assert "THIRD_PARTY_NOTICES.md" in workflow
        assert "endpointing weights must be packaged exactly once" in workflow


def test_docker_workflow_prepares_and_caches_voice_turn_assets():
    workflow = (ROOT / ".github/workflows/docker-multi-arch.yml").read_text(
        encoding="utf-8"
    )

    # Both build jobs (standard + full) must pre-fetch the weights on the
    # native runner so they ride into the build context for every platform.
    assert (
        workflow.count("run: python3 scripts/prepare_voice_turn_assets.py")
        == 2
    )
    assert (
        workflow.count(
            "path: main_logic/asr_client/endpointing/models/*.onnx"
        )
        == 2
    )
    assert (
        workflow.count(
            "key: voice-turn-models-v2-${{ runner.os }}"
            "-${{ hashFiles('main_logic/asr_client/endpointing/models/manifest.json') }}"
        )
        == 2
    )


def test_dockerfiles_verify_voice_turn_assets_in_image():
    for name in ("Dockerfile", "Dockerfile.full"):
        dockerfile = (ROOT / "docker" / name).read_text(encoding="utf-8")
        assert "python3 scripts/prepare_voice_turn_assets.py" in dockerfile, name
        assert (
            "test -s /app/main_logic/asr_client/endpointing/models/"
            "THIRD_PARTY_NOTICES.md"
        ) in dockerfile, name
        assert "test ! -e /app/data/vad_models" in dockerfile, name
        # The verify step must run after the project lands in the image but
        # before ownership is fixed up for the runtime user.
        copy_index = dockerfile.index("COPY --chown=neko:neko . /app")
        prepare_index = dockerfile.index(
            "python3 scripts/prepare_voice_turn_assets.py"
        )
        chown_index = dockerfile.index("chown -R neko:neko /app", copy_index)
        assert copy_index < prepare_index < chown_index, name


def test_build_context_does_not_exclude_voice_turn_assets():
    # Negative contract: the host-prepared weights must actually reach the
    # Docker build context, so .dockerignore must not filter them out.
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    patterns = [
        line.strip().rstrip("/")
        for line in dockerignore.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert "/data/vad_models" in patterns
    assert not any(
        "main_logic/asr_client/endpointing/models" in pattern
        for pattern in patterns
    )
    assert "data" not in patterns
    assert "data/*" not in patterns

    # The runtime never downloads: .gitignore must keep the manifest (and the
    # notices file) reviewable in git while the weights stay untracked.
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "/data/" in gitignore.splitlines()
    assert (
        "!/main_logic/asr_client/endpointing/models/manifest.json"
        in gitignore
    )
    assert (
        "!/main_logic/asr_client/endpointing/models/THIRD_PARTY_NOTICES.md"
        in gitignore
    )
