#!/usr/bin/env python3
"""Install shared expressions on an audited YUI outfit, preserving model assets."""

import argparse
import copy
import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil


EXPRESSION = "expressions/listening.exp3.json"
MODEL_ROOT = Path(__file__).parent / "models"
PROFILE_ROOT = MODEL_ROOT / "yui-common"


def validate_outfit(model_path, model, outfit=None):
    registry = json.loads((MODEL_ROOT / "yui-outfits.json").read_text())
    moc = model_path.parent / model.get("FileReferences", {}).get("Moc", "")
    name = outfit or moc.stem
    expected = registry["outfits"].get(name)
    if expected is None:
        raise ValueError(f"Unaudited YUI outfit: {name}")
    if not moc.resolve().is_relative_to(model_path.parent.resolve()):
        raise ValueError("The MOC must be inside the model directory")
    if hashlib.sha256(moc.read_bytes()).hexdigest() != expected["moc_sha256"]:
        raise ValueError(
            f"YUI model changed; audit its rig before installing profiles: {name}"
        )
    return name


def with_listening_profile(model):
    result = copy.deepcopy(model)
    expressions = result.setdefault("FileReferences", {}).setdefault("Expressions", [])
    for expression in expressions:
        if (
            expression.get("Name") == "listening"
            and expression.get("File") != EXPRESSION
        ):
            raise ValueError(
                "The model already assigns listening to another expression; review it first."
            )
    if not any(item.get("File") == EXPRESSION for item in expressions):
        expressions.append({"Name": "listening", "File": EXPRESSION})
    mapping = result.setdefault("EmotionMapping", {}).setdefault("expressions", {})
    if mapping.get("listening", []) not in ([], [EXPRESSION]):
        raise ValueError("Custom mapping for listening; refusing to replace it")
    mapping["listening"] = [EXPRESSION]
    return result


def with_emotion_profile(model):
    result = with_listening_profile(model)
    expressions = result["FileReferences"]["Expressions"]
    mapping = result["EmotionMapping"]["expressions"]
    for asset in sorted(PROFILE_ROOT.glob("*.exp3.json")):
        name = asset.name.removesuffix(".exp3.json")
        file = "expressions/" + asset.name
        if any(
            item.get("Name") == name and item.get("File") != file
            for item in expressions
        ):
            raise ValueError(f"Conflicting expression: {name}")
        if not any(item.get("File") == file for item in expressions):
            expressions.append({"Name": name, "File": file})
        emotion = name.removeprefix("chat-")
        old = mapping.get(emotion, [])
        allowed = [[], [file]]
        if emotion == "neutral":
            allowed.append(["expressions/001.exp3.json"])
        if old not in allowed:
            raise ValueError(f"Custom mapping for {emotion}; refusing to replace it")
        mapping[emotion] = [file]
    return result


def install_model(model, *, backups=None, apply=False, emotions=False, outfit=None):
    model = model.resolve()
    old_bytes = model.read_bytes()
    old = json.loads(old_bytes)
    name = validate_outfit(model, old, outfit)
    new = with_emotion_profile(old) if emotions else with_listening_profile(old)
    assets = (
        sorted(PROFILE_ROOT.glob("*.exp3.json"))
        if emotions
        else [PROFILE_ROOT / "listening.exp3.json"]
    )
    # Validate and read the entire plan before writing any files.
    planned = {
        model.parent / "expressions" / asset.name: asset.read_bytes()
        for asset in assets
    }
    if old != new:
        planned[model] = (json.dumps(new, ensure_ascii=False, indent=2) + "\n").encode()
    changed = {
        target: data
        for target, data in planned.items()
        if not target.is_file() or target.read_bytes() != data
    }
    result = {
        "model": str(model),
        "outfit": name,
        "changed": bool(changed),
        "applied": False,
    }
    if not changed or not apply:
        return result
    if backups is None:
        raise ValueError("--apply requires --backups")
    saved = backups / (
        "YUI-Profile-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    )
    saved.mkdir(parents=True, mode=0o700)
    shutil.copy2(model, saved / model.name)
    originals = []
    for target in changed:
        if target == model:
            continue
        originals.append({"file": str(target), "existed": target.exists()})
        if target.exists():
            shutil.copy2(target, saved / target.name)
    # Complete the rollback receipt before the first mutation, including failures.
    (saved / "receipt.json").write_text(
        json.dumps({"model": str(model), "outfit": name, "files": originals}, indent=2)
        + "\n"
    )
    print(json.dumps({"backup": str(saved)}), flush=True)
    # Publish model references last, after all expression files are present.
    for target, data in changed.items():
        target.parent.mkdir(exist_ok=True)
        staged = target.with_suffix(".new")
        staged.write_bytes(data)
        staged.replace(target)
    result.update(applied=True, backup=str(saved))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--backups", type=Path)
    parser.add_argument(
        "--apply", action="store_true", help="Default is a read-only comparison"
    )
    parser.add_argument(
        "--emotions",
        action="store_true",
        help="Install chat and song mood profiles too",
    )
    parser.add_argument("--outfit", help="Audited outfit ID, for a renamed MOC file")
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                install_model(
                    args.model,
                    backups=args.backups,
                    apply=args.apply,
                    emotions=args.emotions,
                    outfit=args.outfit,
                )
            )
        )
    except (ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
