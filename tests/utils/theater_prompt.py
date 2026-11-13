"""Read theater prompt data for assertions independently of its wire layout."""

import json
import re


def without_public_destination_indexes(value):
    """Compare original evidence while ignoring request-local display labels."""
    if isinstance(value, dict):
        return {key: without_public_destination_indexes(child) for key, child in value.items()
                if key != "public_destination_index"}
    if isinstance(value, list):
        return [without_public_destination_indexes(child) for child in value]
    return value


def contract_audit_payload(payload, messages):
    """Supply complete authored-rule coverage in an otherwise unchanged model fixture."""
    if not isinstance(payload, dict):
        return payload
    data = prompt_payload(messages[1].content)
    if "finding" in data or "findings" in data:
        findings = data.get("findings", [data.get("finding")])
        def verdict(original, finding):
            if not isinstance(original, dict) or not {"applicable", "confirmed"} <= set(original):
                return original
            positive = original["applicable"] and original["confirmed"]
            rule = next(row for row in data["clearance_sources"] if row["type"] == "contract_rule" and row["text"] == finding["rule"])
            return {"verdict": "violation" if positive else "clear", "reason": "按本测试明确指定的主体、阶段和条件结论复核。",
                    "clearance": [] if positive else [
                        {"source_ref": finding["candidate"]["id"], "quote": finding["candidate"]["text"]},
                        {"source_ref": rule["id"], "quote": rule["text"]}]}
        if "verdicts" in payload:
            return {**payload, "verdicts": [verdict(row, finding) for row, finding in zip(payload["verdicts"], findings, strict=True)]}
        converted = verdict(payload, findings[0])
        return {**{key: value for key, value in payload.items() if key not in {"applicable", "confirmed"}}, **converted}
    if "violations" not in payload:
        return payload
    checks = []
    indices = {row["rule_index"] for row in data.get("author_checks_required", [])}
    for row in data.get("author_checks_required", []):
        findings = [item for item in payload["violations"] if item["rule_index"] == row["rule_index"]]
        assert len(findings) <= 1, "One authored rule has one independently reviewed finding"
        checks.append({"rule_index": row["rule_index"], "status": "violation" if findings else "clear",
                       "findings": [{key: finding[key] for key in ("candidate_ref", "quote", "prior_ref")}
                                    for finding in findings]})
    compact = {"clear": [row["rule_index"] for row in checks if row["status"] == "clear"], "uncertain": [],
               "violations": [{"rule_index": row["rule_index"], **finding}
                              for row in checks for finding in row["findings"]]}
    fixed_causes = {}
    cause_index = next((row["rule_index"] for row in data["author_boundaries"]
                        if row["text"].startswith("程序固定原文的展示须有")), None)
    for source in data["sources"]:
        if not source.get("program_fixed_original"):
            continue
        finding = next((row for row in payload["violations"] if row["rule_index"] == cause_index
                        and row["candidate_ref"] == source["id"]), None)
        fixed_causes[source["id"]] = ({key: finding[key] for key in ("quote", "prior_ref")}
                                     if finding is not None else "clear")
    return {**payload, **({"author_checks": compact} if "author_checks_required" in data else {}),
            **({"fixed_original_cause": fixed_causes} if fixed_causes else {}),
            "player_authority": {row["id"]: "none" for row in data["sources"] if row["id"].startswith("c")},
            "violations": [item for item in payload["violations"] if item["rule_index"] not in indices]}


def prompt_sections(content):
    """Decode each paired data block without rewriting source strings."""

    return {
        label: json.loads(payload)
        for label, payload in re.findall(
            r"======以下为([^\n=]+)======\n(.*?)\n======以上为\1======",
            str(content), re.DOTALL,
        )
    }


def prompt_payload(content):
    """Expose original field paths in partitioned and unchanged review prompts."""

    text = str(content)
    sections = prompt_sections(text)
    if not sections:
        return json.loads(text[text.index("{"):])
    result = {}
    for data in sections.values():
        for key, value in data.items():
            if key in result:
                assert isinstance(result[key], dict) and isinstance(value, dict)
                assert not result[key].keys() & value.keys(), key
                result[key] = {**result[key], **value}
            else:
                result[key] = value
    return result


def prompt_reference(data, path):
    """Read a request-local dotted path with array indexes."""

    for token in re.findall(r"[^.\[\]]+", path):
        data = data[int(token)] if isinstance(data, list) else data[token]
    return data


def pending_transition_text(data):
    pending = data["pending_transition"]
    if "visible_performance" in pending:
        return pending["visible_performance"]
    source = prompt_reference(data, pending["content_ref"])
    if isinstance(source, str):
        return source
    return "".join(f"（{block['text']}）" if block["type"] == "action" else block["text"]
                   for block in source)


def serialized_prompt_tokens(system, human):
    """Count the complete two-message wire representation independently of Actor packing."""
    from utils.tokenize import count_tokens

    return count_tokens(json.dumps([
        {"role": "system", "content": system}, {"role": "user", "content": human},
    ], ensure_ascii=False))
