from __future__ import annotations

from pathlib import Path
import sys

CLI_ROOT = Path(__file__).resolve().parents[2] / "neko_plugin_cli"
if str(CLI_ROOT) not in sys.path:
    sys.path.insert(0, str(CLI_ROOT))

from neko_plugin_cli.commands import _prompt


def test_fallback_select_retries_invalid_input_without_default(monkeypatch) -> None:
    answers = iter(["bad", "2"])
    monkeypatch.setattr("builtins.input", lambda _prompt_text: next(answers))

    result = _prompt._fallback_select(
        "Choose",
        [{"name": "One", "value": "one"}, {"name": "Two", "value": "two"}],
        default=None,
    )

    assert result == "two"
