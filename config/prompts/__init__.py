"""Prompt template modules for Xiao8."""


def _apply_prompt_language_translations(namespace: dict, translations: dict[str, dict]) -> None:
    """Apply explicit per-module prompt translations without fallback behavior."""
    def merge(target: dict, values: dict) -> None:
        for key, value in values.items():
            if isinstance(target.get(key), dict) and isinstance(value, dict):
                merge(target[key], value)
            else:
                target[key] = value

    for name, values in translations.items():
        target = namespace.get(name)
        if isinstance(target, dict):
            merge(target, values)
