"""Export trace-grounded rejection samples and summarize manually supplied labels."""

import argparse
import hashlib
import json
from pathlib import Path
import random


LABELS = {None, "true_violation", "false_positive", "uncertain"}


def rejection_rows(paths):
    for path in sorted(paths):
        candidate, context = None, {}
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            event, data = row.get("event"), row.get("data", {})
            if event == "trace.started":
                context = data
            elif event == "actor.candidate":
                candidate = data.get("performance")
            elif event == "contract.checked":
                body = data.get("candidate", candidate)
                findings = data.get("findings", [])
                verdicts = data.get("confirmation_verdicts", [])
                # Include blocked reports even when protocol repair used the
                # confirmation slot without returning per-finding verdicts.
                items = list(verdicts)
                keys = {(v.get("rule"), v.get("candidate_ref"), v.get("quote")) for v in items}
                for finding in findings:
                    item = {"rule": finding.get("rule"), "quote": finding.get("quote"),
                            "candidate_ref": (finding.get("candidate") or {}).get("id")}
                    if (item["rule"], item["candidate_ref"], item["quote"]) not in keys:
                        items.append(item)
                blocked = {(f.get("rule"), (f.get("candidate") or {}).get("id"), f.get("quote")) for f in findings}
                for index, item in enumerate(items):
                    identity = (item.get("rule"), item.get("candidate_ref"), item.get("quote"))
                    if not item.get("rule") or not item.get("quote"):
                        continue
                    decision = ("blocked" if identity in blocked else
                                "cleared" if item.get("verdict") == "clear" else "warning")
                    key = f"{path.resolve()}:{row.get('sequence')}:{index}"
                    yield {"id": hashlib.sha256(key.encode()).hexdigest()[:20],
                           "trace": str(path.resolve()), "sequence": row.get("sequence"),
                           "context": context, "rule": item["rule"], "quote": item["quote"],
                           "candidate_ref": item.get("candidate_ref"), "candidate": body,
                           "decision": decision, "confirmation": item,
                           "warnings": data.get("review_warnings", []),
                           "label": None, "note": ""}


def sample_rows(rows, *, size=30, seed=0):
    # Shuffle within rules, then round-robin across rules to avoid a frequent
    # rule consuming the whole manual review. This is a stratified sample,
    # not an estimate of the population's overall false-positive rate.
    rng, groups = random.Random(seed), {}
    for row in rows:
        groups.setdefault(row["rule"], []).append(row)
    for group in groups.values():
        rng.shuffle(group)
    result = []
    while len(result) < size:
        added = False
        for rule in sorted(groups):
            if groups[rule]:
                result.append(groups[rule].pop())
                added = True
                if len(result) == size:
                    break
        if not added:
            break
    return result


def summarize(rows):
    groups, seen = {}, set()
    for row in rows:
        if row["id"] in seen:
            raise ValueError("duplicate_sample_id")
        seen.add(row["id"])
        label = row.get("label")
        if label not in LABELS:
            raise ValueError("invalid_manual_label")
        counts = groups.setdefault(row["rule"], dict.fromkeys(
            ("true_violation", "false_positive", "uncertain", "unlabeled"), 0))
        counts[label or "unlabeled"] += 1
    for counts in groups.values():
        denominator = counts["true_violation"] + counts["false_positive"]
        counts["reviewed_decisive"] = denominator
        counts["false_positive_rate"] = counts["false_positive"] / denominator if denominator else None
    return {"sample_size": len(rows), "by_rule": groups,
            "scope": "Manually labeled sample only; unknown/unlabeled excluded from each denominator. No automatic rule changes."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    sample = subs.add_parser("sample")
    sample.add_argument("trace_root", type=Path)
    sample.add_argument("output", type=Path)
    sample.add_argument("--size", type=int, default=30)
    sample.add_argument("--seed", type=int, default=0)
    sample.add_argument("--decision", choices=("blocked", "all"), default="blocked")
    report = subs.add_parser("report")
    report.add_argument("sample", type=Path)
    report.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    if args.command == "sample":
        if args.size <= 0:
            parser.error("--size must be positive")
        rows = [row for row in rejection_rows(args.trace_root.rglob("*.jsonl"))
                if args.decision == "all" or row["decision"] == args.decision]
        payload = {"selection": "stratified_by_rule", "seed": args.seed,
                   "eligible": len(rows), "requested": args.size,
                   "label_values": sorted(label for label in LABELS if label is not None),
                   "rows": sample_rows(rows, size=args.size, seed=args.seed)}
    else:
        payload = summarize(json.loads(args.sample.read_text(encoding="utf-8"))["rows"])
    # Never silently overwrite hand labels or a previous report.
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "count": len(payload.get("rows", []))
                      if args.command == "sample" else payload["sample_size"]}))


if __name__ == "__main__":
    main()
