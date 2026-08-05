from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from knowledge.api import (
    CollectionSpec,
    MAX_PACK_BYTES,
    PACK_SCHEMA_VERSION,
    canonical_pack_bytes,
    load_canonical_pack_artifact,
    load_pack,
    validate_knowledge_identifier,
    validate_pack,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "knowledge" / "schemas" / "knowledge-pack-v1.schema.json"
EXAMPLE_PATH = (
    REPO_ROOT / "examples" / "knowledge-packs" / "minimal.neko-knowledge.json"
)
CLI_PATH = REPO_ROOT / "scripts" / "validate_knowledge_pack.py"
GOLDEN_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "knowledge-pack-v1.golden.neko-knowledge.json"
)
GOLDEN_SHA256 = "7acfed9562006b5404e5e668ca373f72f720b6ea47fe21c1484c00323a2bead6"


def _example_payload() -> dict:
    return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


def _run_cli(path: Path, *, strict: bool = False) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(CLI_PATH)]
    if strict:
        command.append("--strict")
    command.append(str(path))
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_schema_is_draft_2020_12_and_accepts_example() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    payload = _example_payload()

    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(payload, schema, cls=jsonschema.Draft202012Validator)
    assert load_pack(EXAMPLE_PATH).collection_id == "example-colors"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({"entrypoint": "unsafe.module:main"}),
        lambda payload: payload["entries"][0].update({"content": ""}),
        lambda payload: payload["entries"][0].update({"tags": ["source:forged"]}),
        lambda payload: payload["source"].update({"homepage": "javascript:alert(1)"}),
        lambda payload: payload.update({"pack_id": "nul"}),
        lambda payload: payload.update({"collection_id": "com1.fixture"}),
        lambda payload: payload["entries"][0]["terms"].update({"alias": ["x" * 301]}),
        lambda payload: payload["entries"][0].update({"tags": ["x" * 301]}),
    ],
)
def test_schema_and_runtime_reject_shared_invalid_fixtures(mutate) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    payload = _example_payload()
    mutate(payload)

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, schema, cls=jsonschema.Draft202012Validator)
    with pytest.raises(ValueError):
        validate_pack(payload)


def test_normal_validation_accepts_formatted_json() -> None:
    result = _run_cli(EXAMPLE_PATH)

    assert result.returncode == 0
    assert "[PASS] pack_id: example-colors" in result.stdout
    assert "[PASS] collection_id: example-colors" in result.stdout
    assert "[PASS] entries: 1" in result.stdout


def test_strict_validation_requires_canonical_bytes(tmp_path: Path) -> None:
    payload = _example_payload()
    formatted_path = tmp_path / "formatted.neko-knowledge.json"
    formatted_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    formatted = _run_cli(formatted_path, strict=True)
    assert formatted.returncode == 2
    assert "[WARN] canonical_json" in formatted.stderr

    canonical_path = tmp_path / "canonical.neko-knowledge.json"
    canonical_bytes = canonical_pack_bytes(payload)
    assert canonical_bytes.endswith(b"\n")
    assert b"\r" not in canonical_bytes
    canonical_path.write_bytes(canonical_bytes)
    canonical = _run_cli(canonical_path, strict=True)

    assert canonical.returncode == 0
    assert "[PASS] canonical_json" in canonical.stdout


def test_canonical_artifact_requires_exactly_one_final_lf() -> None:
    canonical = canonical_pack_bytes(_example_payload())

    assert canonical.endswith(b"\n")
    assert not canonical.endswith(b"\n\n")
    assert load_canonical_pack_artifact(canonical)["pack_id"] == "example-colors"
    for invalid in (canonical[:-1], canonical + b"\n", canonical[:-1] + b"\r\n"):
        with pytest.raises(ValueError, match="canonical JSON"):
            load_canonical_pack_artifact(invalid)


def test_market_golden_artifact_matches_sdk_contract() -> None:
    raw = GOLDEN_PATH.read_bytes()
    payload = load_canonical_pack_artifact(raw)

    assert hashlib.sha256(raw).hexdigest() == GOLDEN_SHA256
    assert canonical_pack_bytes(payload) == raw
    assert payload["collection"]["display_name"] == "Community Colors"


def test_non_standard_json_constants_are_rejected(tmp_path: Path) -> None:
    original = canonical_pack_bytes(_example_payload())
    raw = original.replace(
        b'"schema_version":1',
        b'"schema_version":NaN',
    )
    assert raw != original
    path = tmp_path / "nan.neko-knowledge.json"
    path.write_bytes(raw)

    with pytest.raises(ValueError):
        load_pack(path)
    with pytest.raises(ValueError):
        load_canonical_pack_artifact(raw)


def test_public_sdk_exports_portable_v1_contract() -> None:
    assert CollectionSpec is not None
    assert MAX_PACK_BYTES == 10 * 1024 * 1024
    assert PACK_SCHEMA_VERSION == 1
    assert validate_knowledge_identifier("a") == "a"
    assert validate_knowledge_identifier("a" * 64) == "a" * 64
    for invalid in (
        "",
        "-a",
        "a-",
        "A",
        "nul",
        "lpt9.data",
        " a ",
        "a" * 65,
        1,
    ):
        with pytest.raises(ValueError):
            validate_knowledge_identifier(invalid)


def test_term_and_tag_item_length_boundary_matches_schema() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    payload = _example_payload()
    payload["entries"][0]["terms"]["alias"] = ["x" * 300]
    payload["entries"][0]["tags"] = ["x" * 300]

    jsonschema.validate(payload, schema, cls=jsonschema.Draft202012Validator)
    assert validate_pack(payload).entries[0].aliases == ("x" * 300,)


@pytest.mark.parametrize("homepage", ("https://", "https:///missing-host"))
def test_homepage_requires_a_host_in_schema_and_runtime(homepage: str) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    payload = _example_payload()
    payload["source"]["homepage"] = homepage

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, schema, cls=jsonschema.Draft202012Validator)
    with pytest.raises(ValueError):
        validate_pack(payload)


def test_invalid_pack_reports_field_without_content(tmp_path: Path) -> None:
    payload = _example_payload()
    secret_content = payload["entries"][0]["content"]
    payload["entries"][0]["title"] = ""
    invalid_path = tmp_path / "invalid.neko-knowledge.json"
    invalid_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = _run_cli(invalid_path)

    assert result.returncode == 2
    assert "entries[0].title" in result.stderr
    assert secret_content not in result.stdout
    assert secret_content not in result.stderr


def test_missing_pack_is_an_operational_error(tmp_path: Path) -> None:
    result = _run_cli(tmp_path / "missing.neko-knowledge.json")

    assert result.returncode == 1
    assert "[FAIL] file:" in result.stderr
