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


def test_card_forge_stays_out_of_production_frontend_builds() -> None:
    shell_build = _read("build_frontend.sh")
    batch_build = _read("build_frontend.bat")
    card_forge_readme = _read("local_server/card_forge_server/README.md")

    assert "CF_DIR=" not in shell_build
    assert 'set "CF_DIR=' not in batch_build
    assert "card-forge" not in shell_build.lower()
    assert "card-forge" not in batch_build.lower()
    assert "本地开发工具" in card_forge_readme
    assert "不会安装或构建 `frontend/card-forge`" in card_forge_readme
