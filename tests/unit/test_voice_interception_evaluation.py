"""Acceptance guards must reject tempting but invalid positive evidence."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/voice_interception_evaluation.py"
spec = importlib.util.spec_from_file_location("voice_interception_evaluation", SCRIPT)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def file_entry(tmp_path, name, pcm=b"\x01\x00" * 8):
    path = tmp_path / name
    path.write_bytes(pcm)
    return {"path": name, "sha256": hashlib.sha256(pcm).hexdigest()}


def dataset(tmp_path):
    rows = []
    for index, split in enumerate(("train", "development", "acceptance")):
        row = dict(file_entry(tmp_path, f"{split}.pcm", bytes([index, 0]) * 8), id=split,
                   split=split, speaker_ids=[f"speaker-{index}"], session_id=f"session-{index}",
                   original_recording_sha256s=[hashlib.sha256(split.encode()).hexdigest()],
                   role="mixture", origin="real", label_status="human_verified")
        rows.append(row)
    return {"schema_version": 1, "recordings": rows}


def frozen(tmp_path):
    data = dataset(tmp_path)
    contract = dict(schema_version=1, purpose="pre_fit_acceptance_contract",
                    dataset_digest=evaluation.document_digest(data), sample_rate=16000,
                    evaluated_case_ids=["acceptance"],
                    decision_layouts=[dict(scoring_samples=24000, decision_start=0, decision_end=2400)],
                    gates=dict(max_owner_word_error_rate=.2, max_guest_word_intrusion_rate=.01,
                               min_owner_sample_coverage=.8, max_unscorable_rate=.1,
                               max_added_reply_ms=500, max_tree_memory_increment_bytes=262144000))
    for field in ("separator_sha256", "speaker_model_sha256", "reference_protocol_sha256", "preprocessing_sha256", "scoring_parameters_sha256"):
        contract[field] = "a" * 64
    return evaluation.freeze_contract(contract, data, tmp_path)


def observations(frozen):
    return dict(contract_digest=frozen["contract_digest"], cases=[dict(
        case_id="acceptance", labels="human_verified", owner_words=20, owner_word_errors=1,
        guest_words=20, guest_intrusions=0, owner_samples=100, accepted_owner_samples=90,
        scored_intervals=20, unscorable_intervals=0)])


def receiver(tmp_path):
    first = dict(file_entry(tmp_path, "candidate.pcm"), delivery_id="one", capture_id="capture", start=0,
                 end=8, segment_id=0, kind="audio", identity="owner_confirmed", candidate_id="tfmap-0")
    second = dict(file_entry(tmp_path, "candidate-next.pcm", b"\x02\x00" * 8), delivery_id="two", capture_id="capture",
                  start=12, end=20, segment_id=1, kind="audio", identity="owner_confirmed", candidate_id="tfmap-1")
    gap = dict(delivery_id="gap", capture_id="capture", start=8, end=12, kind="gap")
    actual = []
    for source in (first, second):
        item = copy.deepcopy(source)
        item.update(file_entry(tmp_path, "received-" + source["path"], (tmp_path / source["path"]).read_bytes()))
        item["receipt"] = "receiver_read"
        actual.append(item)
    return dict(schema_version=1, sample_rate=16000, receiver_scope="local independent PCM receiver",
                expected=[first, gap, second], received=actual)


def test_real_disjoint_files_can_prepare_research_only(tmp_path):
    result = evaluation.validate_dataset(dataset(tmp_path), tmp_path)
    assert result["split_ready"] and result["release_status"] == "research_only"


@pytest.mark.parametrize("dimension", ["speaker_ids", "session_id", "original_recording_sha256s"])
def test_all_provenance_axes_cannot_cross_split(tmp_path, dimension):
    data = dataset(tmp_path)
    data["recordings"][2][dimension] = data["recordings"][0][dimension]
    with pytest.raises(evaluation.EvidenceError, match="cross-split"):
        evaluation.validate_dataset(data, tmp_path)


def test_reference_same_subject_allowed_but_same_recording_rejected(tmp_path):
    data = dataset(tmp_path)
    ref = copy.deepcopy(data["recordings"][2])
    ref.update(id="reference", role="reference")
    data["recordings"].append(ref)
    with pytest.raises(evaluation.EvidenceError, match="reference reused"):
        evaluation.validate_dataset(data, tmp_path)
    ref["original_recording_sha256s"] = ["f" * 64]
    ref.update(file_entry(tmp_path, "different-reference.pcm", b"\x07\x00" * 8))
    assert evaluation.validate_dataset(data, tmp_path)["split_ready"]


def test_machine_transcript_cannot_certify_acceptance(tmp_path):
    data = dataset(tmp_path)
    data["recordings"][2]["label_status"] = "machine_only"
    with pytest.raises(evaluation.EvidenceError, match="human-verified"):
        evaluation.validate_dataset(data, tmp_path)


def test_content_and_owner_coverage_both_required(tmp_path):
    package = frozen(tmp_path)
    measured = observations(package)
    assert evaluation.evaluate_observations(package, measured)["content_gates_passed"]
    measured["cases"][0].update(owner_word_errors=20, accepted_owner_samples=0)
    result = evaluation.evaluate_observations(package, measured)
    assert not result["content_gates_passed"]
    assert "min_owner_sample_coverage" in result["failed_gates"]
    assert result["production_ready"] is False


def test_missing_acceptance_cases_rejected(tmp_path):
    package = frozen(tmp_path)
    measured = observations(package)
    measured["cases"][0]["case_id"] = "easier-substitute"
    with pytest.raises(evaluation.EvidenceError, match="acceptance case"):
        evaluation.evaluate_observations(package, measured)


def test_modified_freeze_rejected(tmp_path):
    package = frozen(tmp_path)
    measured = observations(package)
    package["contract"]["gates"]["min_owner_sample_coverage"] = .01
    with pytest.raises(evaluation.EvidenceError, match="modified"):
        evaluation.evaluate_observations(package, measured)


def test_independent_receiver_exact_pcm_and_gap_segments(tmp_path):
    result = evaluation.audit_receiver(receiver(tmp_path), tmp_path)
    assert result["accepted_samples"] == 16 and result["gap_count"] == 1
    assert result["provider_confirmed_chunks"] == 0


@pytest.mark.parametrize("mutation", ["duplicate", "extra", "missing", "wrong_pcm", "gap_join", "transport_only", "unconfirmed", "reordered"])
def test_receiver_rejects_invalid_delivery(tmp_path, mutation):
    data = receiver(tmp_path)
    if mutation == "duplicate":
        data["received"].append(copy.deepcopy(data["received"][0]))
    elif mutation == "extra":
        data["received"][0]["delivery_id"] = "unknown"
    elif mutation == "missing":
        data["received"].pop()
    elif mutation == "wrong_pcm":
        (tmp_path / data["received"][0]["path"]).write_bytes(b"\x09\x00" * 8)
    elif mutation == "gap_join":
        data["expected"][2]["segment_id"] = 0
    elif mutation == "transport_only":
        data["received"][0]["receipt"] = "writer_accepted"
    elif mutation == "unconfirmed":
        data["expected"][0]["identity"] = "uncertain"
    else:
        data["expected"][0]["start"] = 1
    with pytest.raises(evaluation.EvidenceError):
        evaluation.audit_receiver(data, tmp_path)


def test_receiver_completion_order_cannot_replace_capture_order(tmp_path):
    data = receiver(tmp_path)
    data["received"].reverse()
    with pytest.raises(evaluation.EvidenceError, match="reordered"):
        evaluation.audit_receiver(data, tmp_path)


def test_old_capture_cannot_resume_after_new_capture(tmp_path):
    data = receiver(tmp_path)
    new_capture = copy.deepcopy(data["expected"][0])
    new_capture.update(capture_id="replacement", delivery_id="replacement-one")
    data["expected"].insert(2, new_capture)
    with pytest.raises(evaluation.EvidenceError, match="old capture"):
        evaluation.audit_receiver(data, tmp_path)


def test_file_identity_cannot_be_hidden_by_new_provenance_labels(tmp_path):
    data = dataset(tmp_path)
    data["recordings"][2].update({key: data["recordings"][0][key] for key in ("path", "sha256")})
    with pytest.raises(evaluation.EvidenceError, match="cross-split leakage: file"):
        evaluation.validate_dataset(data, tmp_path)


def test_duplicate_json_keys_rejected(tmp_path):
    path = tmp_path / "ambiguous.json"
    path.write_text('{"schema_version": 1, "schema_version": 2}')
    with pytest.raises(evaluation.EvidenceError, match="duplicate JSON"):
        evaluation.load_document(path)


def test_cli_never_overwrites_frozen_evidence(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(dataset(tmp_path)))
    output = tmp_path / "audit.json"
    assert evaluation.main(["dataset", "--manifest", str(manifest), "--output", str(output)]) == 0
    with pytest.raises(SystemExit):
        evaluation.main(["dataset", "--manifest", str(manifest), "--output", str(output)])


def test_cli_freeze_evaluate_and_receiver_write_distinct_artifacts(tmp_path):
    package = frozen(tmp_path)
    data = dataset(tmp_path)
    for name, document in (("dataset", data), ("contract", package["contract"]),
                           ("observations", observations(package)), ("receiver", receiver(tmp_path))):
        (tmp_path / f"{name}.json").write_text(json.dumps(document))
    assert evaluation.main(["freeze", "--dataset", str(tmp_path/"dataset.json"), "--contract", str(tmp_path/"contract.json"),
                            "--output", str(tmp_path/"frozen.json")]) == 0
    assert evaluation.main(["evaluate", "--frozen", str(tmp_path/"frozen.json"), "--observations", str(tmp_path/"observations.json"),
                            "--output", str(tmp_path/"result.json")]) == 0
    assert evaluation.main(["receiver", "--manifest", str(tmp_path/"receiver.json"), "--output", str(tmp_path/"receiver-result.json")]) == 0
    assert not json.loads((tmp_path/"result.json").read_text())["production_ready"]


@pytest.mark.parametrize("replace_root", [False, True])
def test_resource_identity_and_observation_completion(monkeypatch, replace_root):
    class ProcessError(Exception):
        pass
    class Process:
        pid = 7
        def __init__(self, pid):
            self.calls = 0
        def create_time(self):
            self.calls += 1
            return 100 + int(replace_root and self.calls > 2)
        def is_running(self):
            return True
        def children(self, recursive):
            return []
        def memory_info(self):
            return types.SimpleNamespace(rss=200, private=100)
        def cpu_times(self):
            return types.SimpleNamespace(user=1, system=2)
        def name(self):
            return "owned-test-process"
    monkeypatch.setitem(sys.modules, "psutil", types.SimpleNamespace(Process=Process, Error=ProcessError, NoSuchProcess=ProcessError))
    tick = iter((0, 0, .25, .5, .75, 1.0))
    monkeypatch.setattr(evaluation, "time", types.SimpleNamespace(monotonic=lambda: next(tick), sleep=lambda duration: None))
    result = evaluation.measure_process_tree(7, seconds=.5, interval=.1, hardware_id="fixture")
    assert result["sampled_tree_rss_peak_bytes"] == 200
    assert result["production_ready"] is False
    if replace_root:
        assert not result["observation_completed"] and result["failure"] == "root_process_exited_or_replaced"
    else:
        assert result["observation_completed"] and result["failure"] is None


@pytest.mark.parametrize("value", [float("nan"), True, "1"])
def test_finite_numeric_evidence_required(tmp_path, value):
    package = frozen(tmp_path)
    package["contract"]["gates"]["max_added_reply_ms"] = value
    with pytest.raises(evaluation.EvidenceError):
        evaluation.freeze_contract(package["contract"], dataset(tmp_path), tmp_path)
