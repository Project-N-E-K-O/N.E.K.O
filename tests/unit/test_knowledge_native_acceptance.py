"""Opt-in native acceptance with fresh processes and read-only model assets.

No saved local session or developer-specific path is needed. Existing assets
are mandatory once the caller opts in; an incorrect supplied root must fail
instead of silently skipping. JUnit properties retain the scenario receipts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from tests.process_isolation import child_process_env

pytestmark = pytest.mark.integration_serial
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def native_model_root():
    """Require the existing int8 profile; never install or download assets."""
    value = os.environ.get("NEKO_REAL_EMBEDDING_ROOT")
    if not value:
        pytest.skip("set NEKO_REAL_EMBEDDING_ROOT to existing read-only native assets")
    root = Path(value).expanduser().resolve(strict=True)
    files = [root / "local-text-retrieval-v1" / name for name in (
        "tokenizer.json", "onnx/model_quantized.onnx", "onnx/model_quantized.onnx_data",
    )]
    assert all(path.is_file() and path.stat().st_size > 0 for path in files), root

    def identities():
        return [(stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                for stat in (path.stat() for path in files)]

    before = identities()
    yield root
    assert identities() == before, "native probes modified read-only model assets"


def _run_stage(root: Path, model_root: Path, stage: str) -> dict:
    """A successful exit means native owners and worker threads have also closed."""
    env = child_process_env(root / "process-runtime")
    env.update(NEKO_REAL_EMBEDDING_ROOT=str(model_root), PYTHONIOENCODING="utf-8")
    env.pop("PYTHONOPTIMIZE", None)  # Child assertions run outside pytest rewriting.
    if stage == "market":
        # Run the existing collected contract under its own conftest instead
        # of importing a test function or weakening its streaming assertions.
        env["NEKO_UNIFIED_PACKAGE_FIXTURES"] = str(root / "market-fixture")
        arguments = ["-m", "pytest", "plugin/tests/unit/server/test_unified_knowledge_native_roundtrip.py"
                     "::test_real_builder_native_worker_installs_seeded_market_pack",
                     "-v", "-rfE", "-o", "junit_family=xunit1",
                     "--basetemp", str(root / "plugin-tmp"), "--junitxml", str(root / "market.xml")]
    else:
        arguments = ["-m", "tests.knowledge_native_probe", stage, str(root)]
    result = subprocess.run(
        [sys.executable, *arguments],
        cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8", timeout=90,
    )
    (root / f"{stage}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    if stage == "market":
        cases = list(ET.parse(root / "market.xml").iter("testcase"))
        assert len(cases) == 1 and all(cases[0].find(tag) is None for tag in ("skipped", "failure", "error")), (
            "native market contract must pass without skips"
        )
        return {"passed_cases": 1, "remote_transport": "controlled fixture",
                "oauth": "synthetic", "receiver_encoder_identity": "synthetic"}
    return json.loads((root / f"{stage}.json").read_text(encoding="utf-8"))


def test_native_archive_cross_process_recovery(tmp_path, native_model_root, record_property):
    """Import, install and recover with no object or index shared across processes."""
    root = tmp_path / "native"
    root.mkdir()
    (root / "run.json").write_text(json.dumps({"kind": "knowledge-native-probe-v1"}), encoding="utf-8")
    receipts = {stage: _run_stage(root, native_model_root, stage)
                for stage in ("author", "install", "recover")}
    assert len({row["pid"] for row in receipts.values()} | {os.getpid()}) == 4
    assert receipts["author"]["export_extra_native_calls"] == 0
    assert receipts["install"]["install_native_calls"] == 0
    assert receipts["recover"]["ready_chunks"] == receipts["author"]["chunks"]
    assert receipts["recover"]["protected_inputs_unchanged"]
    receipts["market"] = _run_stage(root, native_model_root, "market")
    record_property("knowledge_native_acceptance", json.dumps(receipts, sort_keys=True))


@pytest.mark.performance
@pytest.mark.skipif(os.environ.get("RUN_PERF_TESTS") != "true", reason="set RUN_PERF_TESTS=true to measure native performance")
@pytest.mark.parametrize("entries", [10, 100])
def test_native_scale_measurements(tmp_path, native_model_root, record_property, entries):
    """Report timings with unchanged scheduling; assert correctness, not speed."""
    root = tmp_path / "scale"
    root.mkdir()
    (root / "run.json").write_text(
        json.dumps({"kind": "knowledge-native-probe-v1", "entries": entries}), encoding="utf-8",
    )
    build = _run_stage(root, native_model_root, "measure-build")
    read = _run_stage(root, native_model_root, "measure-read")
    assert build["pid"] != read["pid"] and os.getpid() not in {build["pid"], read["pid"]}
    assert build["entries"] == read["entries"] == build["chunks"] == read["chunks"] == entries
    assert build["spec"] == read["spec"]
    assert len(read["hybrid_samples_ms"]) == 20 and read["query_native_calls"] == 21
    record_property("knowledge_native_performance", json.dumps({"build": build, "read": read}, sort_keys=True))
