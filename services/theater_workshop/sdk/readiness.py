"""Static compatibility of a validated candidate with an explicit runtime profile.

This does not run models or certify literary quality, route coverage, or latency.
"""
from __future__ import annotations

import hashlib
import json

from .packages import PublishCandidate


def assess_candidate_readiness(candidate: PublishCandidate, runtime_options: dict[str, bool]) -> dict:
    story = json.loads(candidate.json_bytes)
    issues = []
    if not runtime_options["review"]:
        for node_index, node in enumerate(story["nodes"]):
            node_path = f"nodes[{node_index}]"
            pieces = node["story_beat"].get("fixed_narrations", [])
            definitions = {piece["id"]: (index, piece) for index, piece in enumerate(pieces)}
            dependencies = {piece["id"]: [] for piece in pieces}

            def require(piece_id, chain):
                index, piece = definitions[piece_id]
                path = f"{node_path}.story_beat.fixed_narrations[{index}]"
                chain = [*chain, path]
                dependencies[piece_id].append(chain)
                # The formal compiler already enforces same-node, earlier-only
                # references; this follows declared dependencies, not prose.
                for parent_index, parent_id in enumerate(piece["after"]):
                    require(parent_id, [*chain, f"{path}.after[{parent_index}]"])

            for index, piece in enumerate(pieces):
                if piece["required_before_exit"]:
                    require(piece["id"], [
                        f"{node_path}.story_beat.fixed_narrations[{index}].required_before_exit"])
            for index, requirement in enumerate(node.get("completion_contract", {}).get("all", [])):
                if "fixed_narration_id" in requirement:
                    require(requirement["fixed_narration_id"], [
                        f"{node_path}.completion_contract.all[{index}].fixed_narration_id"])

            for index, piece in enumerate(pieces):
                if piece["trigger"]["type"] != "condition":
                    continue
                required = bool(dependencies[piece["id"]])
                issues.append({
                    "code": "conditional_fixed_narration_unavailable",
                    "severity": "blocking" if required else "warning",
                    "node_id": node["id"], "narration_id": piece["id"],
                    "path": f"{node_path}.story_beat.fixed_narrations[{index}].trigger",
                    "dependency_paths": dependencies[piece["id"]],
                    "message": ("目标配置关闭 review，必需的条件固定旁白无法触发。" if required else
                                "目标配置关闭 review，此可选条件固定旁白不会展示。"),
                })

    profile = dict(runtime_options)
    profile_hash = hashlib.sha256(json.dumps(
        profile, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return {
        "schema": "neko.workshop.runtime-readiness.v1",
        "project_id": candidate.project_id, "revision": candidate.revision,
        "story_id": candidate.story_id, "package_hash": candidate.package_hash,
        "runtime_options": profile, "profile_hash": f"sha256:{profile_hash}",
        "checks": ["conditional_fixed_narrations"],
        "status": "blocked" if any(row["severity"] == "blocking" for row in issues)
                  else "rehearsal_required",
        "rehearsal": "not_run", "issues": issues,
    }
