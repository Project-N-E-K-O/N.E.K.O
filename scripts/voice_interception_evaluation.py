"""Offline acceptance evidence for voice interception; never registers a model.

Use ``uv run --no-sync python scripts/voice_interception_evaluation.py --help``.
Input PCM and recordings stay local. No provider calls or credential reads.
All relative evidence paths resolve against the containing JSON document.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any


class EvidenceError(ValueError):
    """The evidence is incomplete or contradicts its acceptance contract."""


def digest_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError(f"{name} must be nonempty")
    return value


def _integer(value: Any, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise EvidenceError(f"{name} must be an integer >= {minimum}")
    return value


def _number(value: Any, name: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise EvidenceError(f"{name} must be finite")
    return float(value)


def _sha(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise EvidenceError("expected lowercase SHA-256")
    return value


def _file(entry: dict, base: Path) -> Path:
    path = base / _text(entry.get("path"), "path")
    if digest_file(path) != _sha(entry.get("sha256")):
        raise EvidenceError("file SHA-256 mismatch")
    return path


def _pairs(pairs: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_document(path: Path) -> dict:
    def reject(value: str) -> None:
        raise EvidenceError(f"nonfinite JSON value: {value}")
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_pairs, parse_constant=reject)
    if not isinstance(value, dict):
        raise EvidenceError("expected JSON object")
    return value


def document_digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_dataset(dataset: dict, base: Path) -> dict:
    """Check declared provenance and disjoint speakers, sessions and roots.

    References can share the subject identity within a split, but cannot be the
    same original recording as evaluation/training audio. Names alone cannot
    certify real identities or recording sessions; the result states this.
    """
    if type(dataset.get("schema_version")) is not int or dataset.get("schema_version") != 1:
        raise EvidenceError("unsupported dataset schema")
    rows = dataset.get("recordings")
    if not isinstance(rows, list) or not rows:
        raise EvidenceError("recordings must be nonempty")
    seen_ids: set[str] = set()
    partitions: dict[tuple[str, str], str] = {}
    roots: dict[str, set[str]] = {}
    counts = {name: 0 for name in ("train", "development", "acceptance", "diagnostic")}
    for row in rows:
        if not isinstance(row, dict):
            raise EvidenceError("recording must be an object")
        identity = _text(row.get("id"), "id")
        if identity in seen_ids:
            raise EvidenceError("duplicate recording id")
        seen_ids.add(identity)
        split = row.get("split")
        if split not in counts:
            raise EvidenceError("unknown split")
        counts[split] += 1
        _file(row, base)
        people = row.get("speaker_ids")
        if not isinstance(people, list) or not people or len(set(people)) != len(people):
            raise EvidenceError("speaker_ids must be unique and nonempty")
        for person in people:
            _text(person, "speaker_id")
        session = _text(row.get("session_id"), "session_id")
        root_hashes = row.get("original_recording_sha256s")
        if not isinstance(root_hashes, list) or not root_hashes:
            raise EvidenceError("original recording roots are required, including all mixture parents")
        role = row.get("role")
        if role not in ("reference", "speech", "mixture"):
            raise EvidenceError("unknown recording role")
        if row.get("origin") not in ("real", "synthetic"):
            raise EvidenceError("recording origin must be declared")
        if row.get("label_status") not in ("human_verified", "machine_only", "unlabelled"):
            raise EvidenceError("label_status must be declared")
        if role != "reference" and split == "acceptance" and row["label_status"] != "human_verified":
            raise EvidenceError("acceptance requires human-verified identity and word labels")
        keys = [("speaker", person) for person in people] + [("session", session)]
        keys += [("root", _sha(root)) for root in root_hashes] + [("file", row["sha256"])]
        for key in keys:
            prior = partitions.setdefault(key, split)
            if prior != split:
                raise EvidenceError(f"cross-split leakage: {key[0]}")
        for root in [*root_hashes, row["sha256"]]:
            roles = roots.setdefault(root, set())
            roles.add(role)
            if "reference" in roles and len(roles) > 1:
                raise EvidenceError("reference reused as evaluated/training speech")
    ready = all(counts[name] for name in ("train", "development", "acceptance"))
    return {"counts": counts, "split_ready": bool(ready), "dataset_digest": document_digest(dataset),
            "evidence_scope": "declared provenance and local file integrity; speaker/session declarations require human review",
            "release_status": "research_only"}


def freeze_contract(contract: dict, dataset: dict, base: Path) -> dict:
    """Validate a pre-fit contract without granting registration authority."""
    data = validate_dataset(dataset, base)
    if type(contract.get("schema_version")) is not int or contract.get("schema_version") != 1 or contract.get("purpose") != "pre_fit_acceptance_contract":
        raise EvidenceError("expected explicit pre-fit acceptance contract")
    if contract.get("dataset_digest") != data["dataset_digest"]:
        raise EvidenceError("contract dataset digest mismatch")
    case_ids = contract.get("evaluated_case_ids")
    expected = {row["id"] for row in dataset["recordings"] if row["role"] != "reference" and row["split"] == "acceptance"}
    if not isinstance(case_ids, list) or not case_ids or len(set(case_ids)) != len(case_ids):
        raise EvidenceError("unique evaluated_case_ids required")
    if set(case_ids) != expected:
        raise EvidenceError("contract must cover every acceptance recording")
    for field in ("separator_sha256", "speaker_model_sha256", "reference_protocol_sha256", "preprocessing_sha256", "scoring_parameters_sha256"):
        _sha(contract.get(field))
    rate = _integer(contract.get("sample_rate"), "sample_rate", 1)
    if rate != 16000:
        raise EvidenceError("only canonical 16k evidence is supported")
    layouts = contract.get("decision_layouts")
    if not isinstance(layouts, list) or not layouts:
        raise EvidenceError("decision layouts required")
    previous = 0
    for layout in layouts:
        if not isinstance(layout, dict):
            raise EvidenceError("decision layout must be an object")
        length = _integer(layout.get("scoring_samples"), "scoring_samples", 1)
        start = _integer(layout.get("decision_start"), "decision_start")
        end = _integer(layout.get("decision_end"), "decision_end", 1)
        if not 0 <= start < end <= length or length <= previous:
            raise EvidenceError("invalid or unordered decision layout")
        previous = length
    gates = contract.get("gates", {})
    for name in ("max_owner_word_error_rate", "max_guest_word_intrusion_rate", "min_owner_sample_coverage", "max_unscorable_rate"):
        value = _number(gates.get(name), name)
        if not 0 <= value <= 1 or name == "min_owner_sample_coverage" and value == 0:
            raise EvidenceError("gate outside supported domain")
    for name in ("max_added_reply_ms", "max_tree_memory_increment_bytes"):
        if _number(gates.get(name), name) <= 0:
            raise EvidenceError("performance gate must be positive")
    return {"contract": contract, "contract_digest": document_digest(contract), "dataset": data,
            "release_status": "research_only", "pre_fit_claim_verified": False,
            "note": "Freeze this artifact before fitting; a digest alone does not prove pre-fit chronology."}


def evaluate_observations(frozen: dict, observations: dict) -> dict:
    """Apply frozen paired identity/content gates; never accepts empty output."""
    contract = frozen["contract"]
    if frozen.get("contract_digest") != document_digest(contract):
        raise EvidenceError("frozen contract was modified")
    if observations.get("contract_digest") != frozen["contract_digest"]:
        raise EvidenceError("observations use a different contract")
    rows = observations.get("cases")
    if not isinstance(rows, list) or not rows:
        raise EvidenceError("no evaluated cases")
    totals = dict(owner_words=0, owner_word_errors=0, guest_words=0, guest_intrusions=0,
                  owner_samples=0, accepted_owner_samples=0, scored_intervals=0, unscorable_intervals=0)
    seen: set[str] = set()
    case_failures: list[dict] = []
    for row in rows:
        identity = _text(row.get("case_id"), "case_id")
        if identity in seen:
            raise EvidenceError("duplicate evaluated case")
        seen.add(identity)
        if row.get("labels") != "human_verified":
            raise EvidenceError("machine transcripts cannot certify content accuracy")
        for field in totals:
            totals[field] += _integer(row.get(field), field)
        if row["accepted_owner_samples"] > row["owner_samples"] or row["guest_intrusions"] > row["guest_words"] or row["unscorable_intervals"] > row["scored_intervals"]:
            raise EvidenceError("inconsistent paired observations")
        for name, numerator, denominator, lower in (
            ("max_owner_word_error_rate", "owner_word_errors", "owner_words", False),
            ("max_guest_word_intrusion_rate", "guest_intrusions", "guest_words", False),
            ("min_owner_sample_coverage", "accepted_owner_samples", "owner_samples", True),
            ("max_unscorable_rate", "unscorable_intervals", "scored_intervals", False),
        ):
            if row[denominator]:
                value = row[numerator] / row[denominator]
                if (value < contract["gates"][name]) if lower else (value > contract["gates"][name]):
                    case_failures.append({"case_id": identity, "gate": name, "value": value})
    if seen != set(contract["evaluated_case_ids"]):
        raise EvidenceError("missing or unexpected acceptance case")
    for field in ("owner_words", "guest_words", "owner_samples", "scored_intervals"):
        if not totals[field]:
            raise EvidenceError(f"missing denominator: {field}")
    measured = {"max_owner_word_error_rate": totals["owner_word_errors"] / totals["owner_words"],
                "max_guest_word_intrusion_rate": totals["guest_intrusions"] / totals["guest_words"],
                "min_owner_sample_coverage": totals["accepted_owner_samples"] / totals["owner_samples"],
                "max_unscorable_rate": totals["unscorable_intervals"] / totals["scored_intervals"]}
    gates = contract["gates"]
    failures = [key for key, value in measured.items() if key.startswith("min_") and value < gates[key]]
    failures += [key for key, value in measured.items() if key.startswith("max_") and value > gates[key]]
    return {"totals": totals, "measured": measured, "failed_gates": failures, "case_failures": case_failures,
            "content_gates_passed": not failures and not case_failures, "production_ready": False,
            "remaining": ["minimum-target-hardware", "30-minute continuous scenario", "two-hour stability", "actual provider and audible reply evidence"]}


def audit_receiver(manifest: dict, base: Path) -> dict:
    """Compare locally expected candidate PCM with independent receiver files.

    Segment boundaries must advance after every declared gap. A writer receipt
    is never described as provider acknowledgement. Duplicate/unknown retries
    and extra receiver chunks are rejected by exact key-set comparison.
    """
    if type(manifest.get("schema_version")) is not int or manifest.get("schema_version") != 1 or type(manifest.get("sample_rate")) is not int or manifest.get("sample_rate") != 16000:
        raise EvidenceError("unsupported receiver schema")
    expected = manifest.get("expected")
    received = manifest.get("received")
    if not isinstance(expected, list) or not isinstance(received, list):
        raise EvidenceError("expected and received must be lists")
    keys: list[str] = []
    audio: dict[str, dict] = {}
    segments: dict[str, int] = {}
    must_advance: set[str] = set()
    cursors: dict[str, int] = {}
    closed_captures: set[str] = set()
    current_capture = None
    for row in expected:
        key = _text(row.get("delivery_id"), "delivery_id")
        if key in keys:
            raise EvidenceError("duplicate expected delivery")
        keys.append(key)
        capture = _text(row.get("capture_id"), "capture_id")
        if capture != current_capture:
            if capture in closed_captures:
                raise EvidenceError("old capture resumed after replacement")
            if current_capture is not None:
                closed_captures.add(current_capture)
            current_capture = capture
        start = _integer(row.get("start"), "start")
        end = _integer(row.get("end"), "end", 1)
        if end <= start or start != cursors.get(capture, 0):
            raise EvidenceError("missing, overlapping or reordered original sample range")
        cursors[capture] = end
        if row.get("kind") == "gap":
            must_advance.add(capture)
            continue
        if row.get("kind") != "audio" or row.get("identity") != "owner_confirmed":
            raise EvidenceError("unconfirmed or unknown output")
        _text(row.get("candidate_id"), "candidate_id")
        current = _integer(row.get("segment_id"), "segment_id")
        segment = segments.get(capture, -1)
        if current < segment or capture in must_advance and current <= segment:
            raise EvidenceError("audio joined across a gap")
        segments[capture] = current
        must_advance.discard(capture)
        path = _file(row, base)
        if path.stat().st_size != (end - start) * 2:
            raise EvidenceError("PCM16 sample length mismatch")
        audio[key] = row
    actual: dict[str, dict] = {}
    for row in received:
        key = _text(row.get("delivery_id"), "delivery_id")
        if key in actual:
            raise EvidenceError("duplicate receiver delivery")
        actual[key] = row
        wanted = audio.get(key)
        if wanted is None:
            raise EvidenceError("unexpected receiver PCM")
        for field in ("capture_id", "start", "end", "segment_id", "sha256"):
            if row.get(field) != wanted[field]:
                raise EvidenceError(f"receiver mismatch: {field}")
        _file(row, base)
        if row.get("receipt") not in ("receiver_read", "provider_confirmed"):
            raise EvidenceError("transport write/unknown result is not receiver evidence")
        if row["receipt"] == "provider_confirmed":
            _text(row.get("provider_confirmation_id"), "provider_confirmation_id")
    if set(actual) != set(audio):
        raise EvidenceError("receiver missing expected PCM")
    if list(actual) != list(audio):
        raise EvidenceError("receiver audio reordered")
    return {"accepted_chunks": len(audio), "received_chunks": len(actual), "gap_count": len(expected) - len(audio),
            "accepted_samples": sum(row["end"] - row["start"] for row in audio.values()),
            "scope": _text(manifest.get("receiver_scope"), "receiver_scope"),
            "provider_confirmed_chunks": sum(row["receipt"] == "provider_confirmed" for row in received),
            "production_ready": False}


def measure_process_tree(pid: int, *, seconds: float, interval: float, hardware_id: str) -> dict:
    """Sample a specified live process tree without launching/stopping services.

    RSS sums include shared pages and exclude external GPU/device allocations.
    A process identity is PID plus creation time; exited children's last CPU
    observations are retained. Missing/access-denied evidence is not zero usage.
    Baseline starts at observation, so launch-time peaks require earlier capture.
    """
    import psutil
    _integer(pid, "pid", 1)
    if not 0 < _number(interval, "interval") <= 5 or not 0 < _number(seconds, "seconds") <= 7200:
        raise EvidenceError("sampling requires positive duration <= 7200s and interval <= 5s")
    _text(hardware_id, "hardware_id")
    try:
        root = psutil.Process(pid)
        root_created = root.create_time()
    except psutil.Error as exc:
        raise EvidenceError("target process is not observable") from exc
    seen: dict[tuple[int, float], dict] = {}
    cpu: dict[tuple[int, float], float] = {}
    samples: list[dict] = []
    start = time.monotonic()
    deadline = start + seconds
    failure = None
    try:
        while True:
            if root.create_time() != root_created or not root.is_running():
                failure = "root_process_exited_or_replaced"
                break
            rss = 0
            private = 0
            private_supported = True
            live = []
            for process in [root, *root.children(recursive=True)]:
                try:
                    key = (process.pid, process.create_time())
                    memory = process.memory_info()
                    times = process.cpu_times()
                    cpu[key] = times.user + times.system
                    seen.setdefault(key, {"pid": key[0], "created": key[1], "name": process.name()})
                    rss += memory.rss
                    if hasattr(memory, "private"):
                        private += memory.private
                    else:
                        private_supported = False
                    live.append(process.pid)
                except psutil.NoSuchProcess:
                    # The vanished child is explicitly tracked as observation loss.
                    failure = "child_exited_during_snapshot"
            now = time.monotonic()
            samples.append({"elapsed_seconds": now-start, "rss_bytes": rss,
                            "private_bytes": private if private_supported else None,
                            "tree_cpu_seconds_observed": sum(cpu.values()), "live_pids": live})
            if now >= deadline:
                break
            time.sleep(min(interval, deadline-now))
    except (psutil.Error, OSError) as exc:
        failure = type(exc).__name__
    if not samples:
        raise EvidenceError("no process resource evidence")
    elapsed = samples[-1]["elapsed_seconds"]
    baseline = samples[0]
    return {"hardware_id": hardware_id, "requested_seconds": seconds, "observed_seconds": elapsed,
            "observation_completed": failure is None and elapsed >= seconds,
            "failure": failure, "root_pid": pid, "root_created": root_created,
            "sampled_tree_rss_peak_bytes": max(row["rss_bytes"] for row in samples),
            "sampled_tree_rss_increment_bytes": max(row["rss_bytes"] for row in samples)-baseline["rss_bytes"],
            "tree_cpu_seconds_observed_increment": samples[-1]["tree_cpu_seconds_observed"]-baseline["tree_cpu_seconds_observed"],
            "processes": list(seen.values()), "samples": samples, "production_ready": False,
            "scope": "sampled PID tree after observation start; not ownership proof, launch peak, GPU memory, ASR content or audible latency acceptance"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("dataset", "receiver"):
        sub = commands.add_parser(name)
        sub.add_argument("--manifest", type=Path, required=True)
        sub.add_argument("--output", type=Path, required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--dataset", type=Path, required=True)
    freeze.add_argument("--contract", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--frozen", type=Path, required=True)
    evaluate.add_argument("--observations", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    resources = commands.add_parser("resources")
    resources.add_argument("--pid", type=int, required=True)
    resources.add_argument("--seconds", type=float, required=True)
    resources.add_argument("--interval", type=float, default=.1)
    resources.add_argument("--hardware-id", required=True)
    resources.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "dataset":
            result = validate_dataset(load_document(args.manifest), args.manifest.parent)
        elif args.command == "receiver":
            result = audit_receiver(load_document(args.manifest), args.manifest.parent)
        elif args.command == "freeze":
            result = freeze_contract(load_document(args.contract), load_document(args.dataset), args.dataset.parent)
        elif args.command == "evaluate":
            result = evaluate_observations(load_document(args.frozen), load_document(args.observations))
        else:
            result = measure_process_tree(args.pid, seconds=args.seconds, interval=args.interval, hardware_id=args.hardware_id)
        # Do not replace a frozen contract or prior evaluation silently.
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
    except (EvidenceError, OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
