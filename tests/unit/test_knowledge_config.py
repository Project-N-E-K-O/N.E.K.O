from pathlib import Path
from unittest.mock import patch

from utils.config_manager import ConfigManager
from config.public_knowledge_settings import (
    PUBLIC_KNOWLEDGE_AUTO_CONTEXT_BUDGET_SECONDS,
    PUBLIC_KNOWLEDGE_EXPLICIT_LOOKUP_BUDGET_SECONDS,
)


def _make_config_manager(tmp_path):
    with patch.object(ConfigManager, "_get_documents_directory", return_value=tmp_path), patch.object(
        ConfigManager, "_get_standard_data_directory_candidates", return_value=[tmp_path / "standard_data"]
    ), patch.object(ConfigManager, "get_legacy_app_root_candidates", return_value=[]), patch.object(
        ConfigManager, "_get_project_root", return_value=tmp_path / "project_root"
    ):
        return ConfigManager("N.E.K.O")


def test_knowledge_directory_is_owned_by_the_runtime_root(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    assert config_manager.knowledge_dir == Path(config_manager.app_docs_dir) / "knowledge"
    assert config_manager.ensure_knowledge_directory() is True
    assert config_manager.knowledge_dir.is_dir()


def test_turn_retrieval_budgets_keep_automatic_context_strict():
    assert 0 < PUBLIC_KNOWLEDGE_AUTO_CONTEXT_BUDGET_SECONDS <= 0.5
    assert (
        PUBLIC_KNOWLEDGE_AUTO_CONTEXT_BUDGET_SECONDS
        < PUBLIC_KNOWLEDGE_EXPLICIT_LOOKUP_BUDGET_SECONDS
        <= 2.0
    )
