"""Fit or evaluate an offline voice-identity calibration candidate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from main_logic.voice_identity_service.calibration import (
    CalibrationError,
    CalibrationExample,
    CalibrationPackage,
    DATASET_SCHEMA_VERSION,
    evaluate_calibration,
    fit_linear_calibration,
)


def _reject_json_constant(value: str) -> None:
    raise CalibrationError(f"non-finite JSON number is not allowed: {value}")


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CalibrationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _loads_strict(text: str) -> object:
    return json.loads(
        text,
        object_pairs_hook=_strict_json_object,
        parse_constant=_reject_json_constant,
    )


def _load_examples(path: Path) -> tuple[CalibrationExample, ...]:
    value = _loads_strict(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CalibrationError("calibration dataset must be a JSON object")
    if (
        set(value) != {"schema_version", "examples"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != DATASET_SCHEMA_VERSION
    ):
        raise CalibrationError("unsupported calibration dataset schema")
    if not isinstance(value["examples"], list):
        raise CalibrationError("examples must be a list")
    return tuple(CalibrationExample.from_dict(item) for item in value["examples"])


def _write(path: Path | None, value: object) -> None:
    text = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if path is None:
        sys.stdout.write(text)
    else:
        path.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    fit = commands.add_parser("fit")
    fit.add_argument("--dataset", type=Path, required=True)
    fit.add_argument("--package-revision", required=True)
    fit.add_argument("--max-nonowner-as-owner-rate", type=float, required=True)
    fit.add_argument("--max-owner-as-nonowner-rate", type=float, required=True)
    fit.add_argument("--output", type=Path)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--dataset", type=Path, required=True)
    evaluate.add_argument("--package", type=Path, required=True)
    evaluate.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        examples = _load_examples(args.dataset)
        if args.command == "fit":
            package = fit_linear_calibration(
                examples,
                package_revision=args.package_revision,
                max_nonowner_as_owner_rate=args.max_nonowner_as_owner_rate,
                max_owner_as_nonowner_rate=args.max_owner_as_nonowner_rate,
            )
            _write(args.output, package.to_dict())
        else:
            package_value = _loads_strict(args.package.read_text(encoding="utf-8"))
            if not isinstance(package_value, dict):
                raise CalibrationError("calibration package must be a JSON object")
            package = CalibrationPackage.from_dict(package_value)
            _write(args.output, evaluate_calibration(package, examples))
    except (
        ArithmeticError,
        CalibrationError,
        OSError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
