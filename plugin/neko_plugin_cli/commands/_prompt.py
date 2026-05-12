"""Interactive prompt abstraction.

Uses ``questionary`` for rich interactive prompts (arrow-key selection,
checkboxes, etc.) when available.  Falls back to plain ``input()`` prompts
when questionary is not installed or when stdin is not a TTY.
"""

from __future__ import annotations

import sys

try:
    import questionary
    from questionary import Choice

    _HAS_QUESTIONARY = True
except ImportError:
    _HAS_QUESTIONARY = False


def is_interactive() -> bool:
    """Return True if we can use rich interactive prompts."""
    return _HAS_QUESTIONARY and sys.stdin.isatty()


def ask_text(message: str, *, default: str = "", validate: object = None) -> str | None:
    """Ask for a text input.  Returns None if the user cancels (Ctrl-C)."""
    if is_interactive():
        kwargs: dict = {"default": default}
        if validate is not None:
            kwargs["validate"] = validate
        return questionary.text(message, **kwargs).ask()
    return _fallback_text(message, default=default)


def ask_select(message: str, choices: list[dict[str, str]], *, default: str | None = None) -> str | None:
    """Ask the user to pick one option from a list.

    Each choice is ``{"value": "...", "name": "display text"}``.
    Returns the ``value`` of the selected choice, or None on cancel.
    """
    if is_interactive():
        q_choices = [
            Choice(title=c["name"], value=c["value"])
            for c in choices
        ]
        return questionary.select(
            message,
            choices=q_choices,
            default=default,
        ).ask()
    return _fallback_select(message, choices, default=default)


def ask_checkbox(message: str, choices: list[dict[str, str]], *, defaults: list[str] | None = None) -> list[str] | None:
    """Ask the user to pick zero or more options.

    Returns a list of selected ``value`` strings, or None on cancel.
    """
    default_set = set(defaults or [])
    if is_interactive():
        q_choices = [
            Choice(title=c["name"], value=c["value"], checked=(c["value"] in default_set))
            for c in choices
        ]
        return questionary.checkbox(message, choices=q_choices).ask()
    return _fallback_checkbox(message, choices, defaults=defaults)


def ask_confirm(message: str, *, default: bool = True) -> bool | None:
    """Ask a yes/no question.  Returns None on cancel."""
    if is_interactive():
        return questionary.confirm(message, default=default).ask()
    return _fallback_confirm(message, default=default)


# ---------------------------------------------------------------------------
# Plain-text fallbacks (no questionary)
# ---------------------------------------------------------------------------

def _fallback_text(message: str, *, default: str = "") -> str | None:
    suffix = f" [{default}]" if default else ""
    try:
        raw = input(f"{message}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    return raw or default


def _fallback_select(message: str, choices: list[dict[str, str]], *, default: str | None) -> str | None:
    while True:
        print(f"{message}")
        for i, c in enumerate(choices, 1):
            marker = " (default)" if c["value"] == default else ""
            print(f"  {i}. {c['name']}{marker}")
        try:
            raw = input("Enter number: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not raw and default:
            return default
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(choices):
                return choices[idx]["value"]
        except ValueError:
            pass
        print("Invalid choice, please try again.", file=sys.stderr)


def _fallback_checkbox(message: str, choices: list[dict[str, str]], *, defaults: list[str] | None) -> list[str] | None:
    default_set = set(defaults or [])
    print(f"{message} (comma-separated numbers)")
    for i, c in enumerate(choices, 1):
        marker = " *" if c["value"] in default_set else ""
        print(f"  {i}. {c['name']}{marker}")
    try:
        raw = input("Enter numbers: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw:
        return list(default_set)
    selected: list[str] = []
    for part in raw.split(","):
        try:
            idx = int(part.strip()) - 1
            if 0 <= idx < len(choices):
                selected.append(choices[idx]["value"])
        except ValueError:
            pass
    return selected


def _fallback_confirm(message: str, *, default: bool) -> bool | None:
    suffix = " [Y/n]" if default else " [y/N]"
    try:
        raw = input(f"{message}{suffix}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw:
        return default
    return raw in ("y", "yes", "1", "true")
