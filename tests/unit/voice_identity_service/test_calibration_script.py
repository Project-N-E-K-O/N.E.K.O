from __future__ import annotations

import json

import pytest

from main_logic.voice_identity_service.calibration import CalibrationError
from scripts.evaluate_voice_identity_calibration import _loads_strict, main


def test_fit_command_refuses_empty_dataset_without_writing_package(tmp_path) -> None:
    dataset = tmp_path / "empty.json"
    output = tmp_path / "candidate.json"
    dataset.write_text(json.dumps({"schema_version": 1, "examples": []}), encoding="utf-8")

    with pytest.raises(SystemExit) as raised:
        main(
            [
                "fit",
                "--dataset",
                str(dataset),
                "--package-revision",
                "candidate-test",
                "--max-nonowner-as-owner-rate",
                "0",
                "--max-owner-as-nonowner-rate",
                "0",
                "--output",
                str(output),
            ]
        )

    assert raised.value.code == 2
    assert not output.exists()


@pytest.mark.parametrize(
    "payload",
    [
        '{"schema_version": 1, "schema_version": 1, "examples": []}',
        '{"schema_version": 1, "examples": [], "value": NaN}',
    ],
)
def test_strict_json_rejects_duplicate_keys_and_nonfinite_numbers(payload) -> None:
    with pytest.raises(CalibrationError):
        _loads_strict(payload)


def test_cli_reports_invalid_numeric_json_without_traceback(tmp_path, capsys) -> None:
    dataset = tmp_path / "invalid.json"
    dataset.write_text('{"schema_version": 1, "examples": NaN}', encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        main(
            [
                "fit", "--dataset", str(dataset), "--package-revision", "candidate-test",
                "--max-nonowner-as-owner-rate", "0", "--max-owner-as-nonowner-rate", "0",
            ]
        )
    assert raised.value.code == 2
    assert "Traceback" not in capsys.readouterr().err
