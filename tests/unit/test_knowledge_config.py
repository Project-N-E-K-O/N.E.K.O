from pathlib import Path
from unittest.mock import patch

from utils.config_manager import ConfigManager


def test_knowledge_directory_is_owned_by_the_runtime_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("NEKO_STORAGE_SELECTED_ROOT", raising=False)
    monkeypatch.delenv("NEKO_STORAGE_ANCHOR_ROOT", raising=False)
    monkeypatch.delenv("NEKO_STORAGE_CLOUDSAVE_ROOT", raising=False)
    with (
        patch.object(ConfigManager, "_get_documents_directory", return_value=tmp_path),
        patch.object(
            ConfigManager,
            "_get_standard_data_directory_candidates",
            return_value=[tmp_path / "standard_data"],
        ),
        patch.object(ConfigManager, "get_legacy_app_root_candidates", return_value=[]),
        patch.object(
            ConfigManager,
            "_get_project_root",
            return_value=tmp_path / "project_root",
        ),
    ):
        config_manager = ConfigManager("N.E.K.O")

    assert config_manager.knowledge_dir == config_manager.app_docs_dir / "knowledge"
    assert config_manager.ensure_knowledge_directory() is True
    assert config_manager.knowledge_dir.is_dir()
