import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REACT_CHAT_ROOT = PROJECT_ROOT / "frontend" / "react-neko-chat"


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_react_chat_vite_config_is_not_emitted_next_to_its_source() -> None:
    node_config = json.loads(
        (REACT_CHAT_ROOT / "tsconfig.node.json").read_text(encoding="utf-8")
    )
    ignored_names = {
        line.strip()
        for line in (REACT_CHAT_ROOT / ".gitignore")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert node_config["compilerOptions"]["noEmit"] is True
    assert {"vite.config.js", "vite.config.d.ts"} <= ignored_names


def test_standalone_card_forge_sources_are_retired() -> None:
    retired_paths = (
        PROJECT_ROOT / "frontend" / "card-forge",
        PROJECT_ROOT / "local_server" / "card_forge_server",
        PROJECT_ROOT / "scripts" / "card_forge",
        PROJECT_ROOT / "main_logic" / "card_cache",
    )

    assert all(not path.exists() for path in retired_paths)
