"""Download the pinned KWS asset and atomically publish a complete version."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow the documented standalone script entry point.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main_logic.voice_identity_service.wake_word_bundle import (  # noqa: E402,F401
    ASSETS, MODEL_NAME, MODEL_SHA256, MODEL_URL, install_bundle,
)


def _validate_bundle(path: Path) -> None:
    from config.voice_wake_word import DEFAULT_WAKE_WORD_KEYWORDS
    from main_logic.voice_input.wake_word.sherpa_backend import (
        SherpaWakeWordConfig, validate_wake_word_resources,
    )
    validate_wake_word_resources(SherpaWakeWordConfig(model_dir=str(path), keywords=DEFAULT_WAKE_WORD_KEYWORDS))


def provision(destination: Path, archive: Path | None = None) -> None:
    """Publish an authenticated immutable bundle; print its resolved path."""
    directory = install_bundle(destination, archive, validate=_validate_bundle)
    print(f"Model installed: {directory}")
    print("Enable wake words in Voice identity settings after preparing resources.")
    print(f"For deployment-managed settings: NEKO_WAKE_WORD_MODEL_DIR={directory}")
    print("A compatible bundled runtime or patched source runtime is required.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--archive", type=Path, help="Reuse an already downloaded, hash-verified release")
    args = parser.parse_args()
    provision(args.model_dir, args.archive)


if __name__ == "__main__":
    main()
