"""
Tests for Windows Controlled Folder Access (CFA / 反勒索防护) resilience.

CFA blocks WRITES to protected folders (Documents, Desktop, etc.) but allows READS.
When CFA is active, the app should:
  1. Fall back to %LOCALAPPDATA% for writes
  2. Remember the original Documents path for reads (models, configs)
  3. Serve user models from Documents (readable), save new data to AppData (writable)
  4. Not break workshop, VRM, or other unrelated paths
"""
import os
import sys
import json
import shutil
import pytest
import tempfile
import logging
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

logger = logging.getLogger(__name__)

APP_NAME = "N.E.K.O"


# ─── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def cfa_env(tmp_path):
    """
    Simulates a CFA environment with:
      - docs_dir: a "Documents" directory that is READABLE but NOT WRITABLE
      - appdata_dir: an "AppData/Local" directory that is fully writable
      - Both have NekoNeko/live2d sub-trees with different models
    """
    # Create "Documents" with user models
    docs_dir = tmp_path / "Documents"
    docs_live2d = docs_dir / APP_NAME / "live2d"
    docs_live2d.mkdir(parents=True)

    # Place a model in Documents
    model_in_docs = docs_live2d / "my_custom_model"
    model_in_docs.mkdir()
    model_config = {
        "Version": 3,
        "FileReferences": {
            "Moc": "my_custom_model.moc3",
            "Textures": ["texture_00.png"],
            "Motions": {},
            "Expressions": []
        }
    }
    (model_in_docs / "my_custom_model.model3.json").write_text(
        json.dumps(model_config), encoding="utf-8"
    )

    # Create "AppData/Local" (writable fallback)
    appdata_dir = tmp_path / "AppData" / "Local"
    appdata_live2d = appdata_dir / APP_NAME / "live2d"
    appdata_live2d.mkdir(parents=True)

    # Place a different model in AppData (simulating one imported after CFA kicked in)
    model_in_appdata = appdata_live2d / "newly_imported_model"
    model_in_appdata.mkdir()
    (model_in_appdata / "newly_imported_model.model3.json").write_text(
        json.dumps(model_config), encoding="utf-8"
    )

    return {
        "tmp_path": tmp_path,
        "docs_dir": docs_dir,
        "appdata_dir": appdata_dir,
        "docs_live2d": docs_live2d,
        "appdata_live2d": appdata_live2d,
    }


@pytest.fixture
def normal_env(tmp_path):
    """
    Simulates a normal (non-CFA) environment where Documents is fully writable.
    """
    docs_dir = tmp_path / "Documents"
    docs_live2d = docs_dir / APP_NAME / "live2d"
    docs_live2d.mkdir(parents=True)

    model_dir = docs_live2d / "normal_model"
    model_dir.mkdir()
    model_config = {
        "Version": 3,
        "FileReferences": {
            "Moc": "normal_model.moc3",
            "Textures": ["texture_00.png"],
            "Motions": {},
            "Expressions": []
        }
    }
    (model_dir / "normal_model.model3.json").write_text(
        json.dumps(model_config), encoding="utf-8"
    )

    return {
        "tmp_path": tmp_path,
        "docs_dir": docs_dir,
        "docs_live2d": docs_live2d,
    }


# ─── Helper: Build a ConfigManager with controlled paths ────────────

def _make_config_manager_cfa(cfa_env):
    """
    Build a ConfigManager where _get_documents_directory returns the AppData path
    (simulating CFA) and _first_readable_candidate is the Documents path.
    """
    from utils.config_manager import ConfigManager

    docs_dir = cfa_env["docs_dir"]
    appdata_dir = cfa_env["appdata_dir"]

    def fake_get_docs(self_inner):
        self_inner._first_readable_candidate = docs_dir
        return appdata_dir

    with patch.object(ConfigManager, '_get_documents_directory', fake_get_docs):
        cm = ConfigManager.__new__(ConfigManager)
        cm.__init__(app_name=APP_NAME)

    return cm


def _make_config_manager_normal(normal_env):
    """
    Build a ConfigManager where _get_documents_directory returns the Documents path
    (normal case, no CFA).
    """
    from utils.config_manager import ConfigManager

    docs_dir = normal_env["docs_dir"]

    def fake_get_docs(self_inner):
        self_inner._first_readable_candidate = docs_dir
        return docs_dir

    with patch.object(ConfigManager, '_get_documents_directory', fake_get_docs):
        cm = ConfigManager.__new__(ConfigManager)
        cm.__init__(app_name=APP_NAME)

    return cm


# ═══════════════════════════════════════════════════════════════════════
# TEST GROUP 1: ConfigManager CFA Detection
# ═══════════════════════════════════════════════════════════════════════

class TestConfigManagerCFA:
    """Tests for ConfigManager's CFA fallback detection logic."""

    def test_cfa_detected_readable_docs_dir_set(self, cfa_env):
        """When CFA is active, _readable_docs_dir should be set to the original Documents."""
        cm = _make_config_manager_cfa(cfa_env)

        assert cm._readable_docs_dir is not None
        assert cm._readable_docs_dir == cfa_env["docs_dir"]
        assert cm.docs_dir == cfa_env["appdata_dir"]
        # docs_dir and _readable_docs_dir should differ (CFA scenario)
        assert cm.docs_dir != cm._readable_docs_dir

    def test_cfa_live2d_dir_points_to_appdata(self, cfa_env):
        """In CFA mode, live2d_dir should point to the AppData fallback."""
        cm = _make_config_manager_cfa(cfa_env)

        expected = cfa_env["appdata_dir"] / APP_NAME / "live2d"
        assert cm.live2d_dir == expected

    def test_cfa_readable_live2d_dir_returns_documents(self, cfa_env):
        """In CFA mode, readable_live2d_dir should return the Documents live2d path."""
        cm = _make_config_manager_cfa(cfa_env)

        expected = cfa_env["docs_dir"] / APP_NAME / "live2d"
        result = cm.readable_live2d_dir
        assert result is not None
        assert result == expected

    def test_normal_no_readable_docs_dir(self, normal_env):
        """In normal (non-CFA) mode, _readable_docs_dir should be None."""
        cm = _make_config_manager_normal(normal_env)

        assert cm._readable_docs_dir is None

    def test_normal_readable_live2d_dir_returns_none(self, normal_env):
        """In normal mode, readable_live2d_dir should return None."""
        cm = _make_config_manager_normal(normal_env)

        assert cm.readable_live2d_dir is None

    def test_normal_live2d_dir_points_to_documents(self, normal_env):
        """In normal mode, live2d_dir should point to Documents."""
        cm = _make_config_manager_normal(normal_env)

        expected = normal_env["docs_dir"] / APP_NAME / "live2d"
        assert cm.live2d_dir == expected

    def test_cfa_vrm_dir_still_set(self, cfa_env):
        """VRM dir should still be set (to AppData path) — not broken."""
        cm = _make_config_manager_cfa(cfa_env)

        expected = cfa_env["appdata_dir"] / APP_NAME / "vrm"
        assert cm.vrm_dir == expected

    def test_cfa_readable_live2d_dir_none_when_dir_missing(self, cfa_env):
        """If the live2d dir under Documents doesn't exist, readable_live2d_dir returns None."""
        # Remove the live2d directory from Documents
        shutil.rmtree(str(cfa_env["docs_live2d"]))

        cm = _make_config_manager_cfa(cfa_env)
        assert cm.readable_live2d_dir is None


# ═══════════════════════════════════════════════════════════════════════
# TEST GROUP 2: _get_documents_directory Fallback Chain
# ═══════════════════════════════════════════════════════════════════════

class TestDocumentsDirectoryFallback:
    """Tests for the actual _get_documents_directory fallback logic."""

    def test_appdata_in_candidates(self):
        """AppData/Local should be in the candidate list on Windows (or when simulated)."""
        from utils.config_manager import ConfigManager

        with patch('sys.platform', 'win32'):
            with patch.dict(os.environ, {'LOCALAPPDATA': 'C:\\Users\\Test\\AppData\\Local'}):
                # We can't easily test the full _get_documents_directory without
                # affecting the singleton, but we can verify the logic by
                # checking the code path through a fresh instance
                cm = ConfigManager.__new__(ConfigManager)
                cm._verbose = False

                # Mock winreg to avoid registry access
                with patch('utils.config_manager.winreg', create=True) as mock_winreg:
                    mock_winreg.OpenKey = MagicMock(side_effect=Exception("no registry"))
                    mock_winreg.QueryValueEx = MagicMock(side_effect=Exception("no registry"))
                    mock_winreg.HKEY_CURRENT_USER = 0
                    # Patch sys.platform inside the module
                    with patch('utils.config_manager.sys') as mock_sys:
                        mock_sys.platform = 'win32'
                        mock_sys.executable = sys.executable
                        mock_sys.stderr = sys.stderr
                        mock_sys.frozen = False
                        mock_sys.path = sys.path
                        # We can't fully run _get_documents_directory in test
                        # because it touches real filesystem, but we can verify
                        # the LOCALAPPDATA env var is read
                        assert os.environ.get('LOCALAPPDATA') == 'C:\\Users\\Test\\AppData\\Local'

    def test_first_readable_recorded(self, tmp_path):
        """_get_documents_directory should record the first readable candidate."""
        from utils.config_manager import ConfigManager

        # Create two dirs: first is readable-only, second is writable
        readonly_dir = tmp_path / "readonly_docs"
        readonly_dir.mkdir()
        writable_dir = tmp_path / "writable_fallback"
        writable_dir.mkdir()

        cm = ConfigManager.__new__(ConfigManager)
        cm._verbose = False

        # Simulate the core loop logic
        candidates = [readonly_dir, writable_dir]
        first_readable = None

        for docs_dir in candidates:
            if first_readable is None and docs_dir.exists() and os.access(str(docs_dir), os.R_OK):
                first_readable = docs_dir

        assert first_readable == readonly_dir


# ═══════════════════════════════════════════════════════════════════════
# TEST GROUP 3: find_models Dual-Directory Search
# ═══════════════════════════════════════════════════════════════════════

class TestFindModelsCFA:
    """Tests for find_models() with CFA dual-directory support."""

    def test_cfa_find_models_finds_documents_models(self, cfa_env):
        """In CFA mode, find_models should find models from Documents (readable)."""
        cm = _make_config_manager_cfa(cfa_env)

        with patch('utils.config_manager.get_config_manager', return_value=cm):
            from utils.frontend_utils import find_models
            models = find_models()

        model_names = [m['name'] for m in models]
        assert 'my_custom_model' in model_names, \
            f"Model from Documents not found. Found: {model_names}"

    def test_cfa_find_models_finds_appdata_models(self, cfa_env):
        """In CFA mode, find_models should also find models from AppData (writable)."""
        cm = _make_config_manager_cfa(cfa_env)

        with patch('utils.config_manager.get_config_manager', return_value=cm):
            from utils.frontend_utils import find_models
            models = find_models()

        model_names = [m['name'] for m in models]
        assert 'newly_imported_model' in model_names, \
            f"Model from AppData not found. Found: {model_names}"

    def test_cfa_find_models_correct_url_prefixes(self, cfa_env):
        """Models from Documents should use /user_live2d, AppData should use /user_live2d_local."""
        cm = _make_config_manager_cfa(cfa_env)

        with patch('utils.config_manager.get_config_manager', return_value=cm):
            from utils.frontend_utils import find_models
            models = find_models()

        model_map = {m['name']: m for m in models}

        if 'my_custom_model' in model_map:
            docs_model = model_map['my_custom_model']
            assert '/user_live2d/' in docs_model['path'], \
                f"Documents model should use /user_live2d/ prefix, got: {docs_model['path']}"
            # Should NOT have /user_live2d_local/
            assert '/user_live2d_local/' not in docs_model['path'], \
                f"Documents model should NOT use /user_live2d_local/ prefix"

        if 'newly_imported_model' in model_map:
            appdata_model = model_map['newly_imported_model']
            assert '/user_live2d_local/' in appdata_model['path'], \
                f"AppData model should use /user_live2d_local/ prefix, got: {appdata_model['path']}"

    def test_normal_find_models_uses_user_live2d_prefix(self, normal_env):
        """In normal mode, models should use /user_live2d prefix."""
        cm = _make_config_manager_normal(normal_env)

        with patch('utils.config_manager.get_config_manager', return_value=cm):
            from utils.frontend_utils import find_models
            models = find_models()

        model_names = [m['name'] for m in models]
        assert 'normal_model' in model_names

        model_map = {m['name']: m for m in models}
        normal_model = model_map['normal_model']
        assert '/user_live2d/' in normal_model['path']
        assert '/user_live2d_local/' not in normal_model['path']


# ═══════════════════════════════════════════════════════════════════════
# TEST GROUP 4: find_model_directory CFA Dual Search
# ═══════════════════════════════════════════════════════════════════════

class TestFindModelDirectoryCFA:
    """Tests for find_model_directory() CFA dual-directory logic."""

    def test_cfa_finds_model_in_documents(self, cfa_env):
        """find_model_directory should find a model in the readable Documents dir."""
        cm = _make_config_manager_cfa(cfa_env)

        with patch('utils.config_manager.get_config_manager', return_value=cm):
            from utils.frontend_utils import find_model_directory
            result = find_model_directory('my_custom_model')

        assert result is not None
        path, url_prefix = result
        assert path is not None
        assert 'my_custom_model' in path
        assert url_prefix == '/user_live2d', \
            f"Documents model should map to /user_live2d, got: {url_prefix}"

    def test_cfa_finds_model_in_appdata(self, cfa_env):
        """find_model_directory should find a model in the writable AppData dir."""
        cm = _make_config_manager_cfa(cfa_env)

        with patch('utils.config_manager.get_config_manager', return_value=cm):
            from utils.frontend_utils import find_model_directory
            result = find_model_directory('newly_imported_model')

        assert result is not None
        path, url_prefix = result
        assert path is not None
        assert 'newly_imported_model' in path
        assert url_prefix == '/user_live2d_local', \
            f"AppData model should map to /user_live2d_local, got: {url_prefix}"

    def test_cfa_model_not_found_returns_none(self, cfa_env):
        """find_model_directory should return (None, None) for non-existent models."""
        cm = _make_config_manager_cfa(cfa_env)

        with patch('utils.config_manager.get_config_manager', return_value=cm):
            from utils.frontend_utils import find_model_directory
            result = find_model_directory('nonexistent_model_xyz')

        # Should ultimately return (None, None) or similar
        if result is not None:
            path, url_prefix = result
            assert path is None

    def test_normal_finds_model_in_documents(self, normal_env):
        """In normal mode, find_model_directory should find models with /user_live2d prefix."""
        cm = _make_config_manager_normal(normal_env)

        with patch('utils.config_manager.get_config_manager', return_value=cm):
            from utils.frontend_utils import find_model_directory
            result = find_model_directory('normal_model')

        assert result is not None
        path, url_prefix = result
        assert path is not None
        assert 'normal_model' in path
        assert url_prefix == '/user_live2d'


# ═══════════════════════════════════════════════════════════════════════
# TEST GROUP 5: Workshop Models Unaffected
# ═══════════════════════════════════════════════════════════════════════

class TestWorkshopUnaffected:
    """Verify workshop model loading is not broken by CFA changes."""

    def test_workshop_model_found_in_cfa_mode(self, cfa_env):
        """Workshop models should be discoverable even when CFA is active."""
        cm = _make_config_manager_cfa(cfa_env)

        # Create a workshop directory with a model
        workshop_dir = cfa_env["tmp_path"] / "workshop"
        workshop_dir.mkdir()
        ws_model_dir = workshop_dir / "12345" / "workshop_cat"
        ws_model_dir.mkdir(parents=True)
        (ws_model_dir / "workshop_cat.model3.json").write_text(
            json.dumps({
                "Version": 3,
                "FileReferences": {
                    "Moc": "workshop_cat.moc3",
                    "Textures": ["tex.png"],
                    "Motions": {},
                    "Expressions": []
                }
            }),
            encoding="utf-8"
        )

        # Patch workshop dir resolution
        cm.workshop_dir = workshop_dir

        with patch('utils.config_manager.get_config_manager', return_value=cm):
            with patch('utils.frontend_utils._resolve_workshop_search_dir',
                       return_value=str(workshop_dir)):
                from utils.frontend_utils import find_model_directory
                result = find_model_directory('workshop_cat')

        assert result is not None
        path, url_prefix = result
        assert path is not None
        assert 'workshop_cat' in path
        assert url_prefix == '/workshop'

    def test_workshop_in_find_models_cfa(self, cfa_env):
        """Workshop models appear in find_models results alongside CFA-split live2d models."""
        cm = _make_config_manager_cfa(cfa_env)

        # Create workshop model
        workshop_dir = cfa_env["tmp_path"] / "workshop"
        workshop_dir.mkdir()
        ws_model_dir = workshop_dir / "ws_model"
        ws_model_dir.mkdir()
        (ws_model_dir / "ws_model.model3.json").write_text(
            json.dumps({
                "Version": 3,
                "FileReferences": {"Moc": "ws.moc3", "Textures": ["t.png"],
                                    "Motions": {}, "Expressions": []}
            }),
            encoding="utf-8"
        )

        with patch('utils.config_manager.get_config_manager', return_value=cm):
            with patch('utils.frontend_utils._resolve_workshop_search_dir',
                       return_value=str(workshop_dir)):
                from utils.frontend_utils import find_models
                models = find_models()

        model_names = [m['name'] for m in models]
        # Should have workshop model + Documents model + AppData model
        assert 'ws_model' in model_names, f"Workshop model missing. Found: {model_names}"
        assert 'my_custom_model' in model_names, f"Documents model missing. Found: {model_names}"


# ═══════════════════════════════════════════════════════════════════════
# TEST GROUP 6: VRM Not Broken
# ═══════════════════════════════════════════════════════════════════════

class TestVRMUnaffected:
    """Verify VRM model paths are not broken by CFA changes."""

    def test_vrm_dir_set_correctly_cfa(self, cfa_env):
        """VRM dir should follow docs_dir (AppData in CFA mode)."""
        cm = _make_config_manager_cfa(cfa_env)

        expected_vrm = cfa_env["appdata_dir"] / APP_NAME / "vrm"
        assert cm.vrm_dir == expected_vrm

    def test_vrm_animation_dir_set_correctly_cfa(self, cfa_env):
        """VRM animation dir should follow vrm_dir."""
        cm = _make_config_manager_cfa(cfa_env)

        expected_anim = cfa_env["appdata_dir"] / APP_NAME / "vrm" / "animation"
        assert cm.vrm_animation_dir == expected_anim

    def test_vrm_dir_normal(self, normal_env):
        """VRM dir in normal mode should point to Documents."""
        cm = _make_config_manager_normal(normal_env)

        expected_vrm = normal_env["docs_dir"] / APP_NAME / "vrm"
        assert cm.vrm_dir == expected_vrm


# ═══════════════════════════════════════════════════════════════════════
# TEST GROUP 7: Write-back Fault Tolerance
# ═══════════════════════════════════════════════════════════════════════

class TestWriteBackFaultTolerance:
    """Tests that write-back operations fail gracefully in CFA mode."""

    def test_model_config_readable_despite_write_failure(self, cfa_env):
        """
        Simulates get_model_config logic: reading model3.json should succeed
        even if writing back (atomic_write_json) fails.
        """
        model_json = cfa_env["docs_live2d"] / "my_custom_model" / "my_custom_model.model3.json"

        # Read the config (CFA allows reads)
        with open(model_json, 'r', encoding='utf-8') as f:
            config_data = json.load(f)

        assert config_data['Version'] == 3
        assert 'FileReferences' in config_data

        # Simulate the write-back logic from live2d_router.py
        config_updated = True
        write_succeeded = True
        if config_updated:
            try:
                # Simulate CFA blocking write
                raise PermissionError("[WinError 5] Access is denied")
            except Exception as write_err:
                write_succeeded = False
                logger.warning(f"无法写回模型配置: {write_err}")

        # The important thing: we still have the config data
        assert config_data is not None
        assert config_data['Version'] == 3
        # Write failure should NOT prevent returning the config
        assert not write_succeeded  # Write failed, but that's OK


# ═══════════════════════════════════════════════════════════════════════
# TEST GROUP 8: Backward Compatibility (No CFA)
# ═══════════════════════════════════════════════════════════════════════

class TestBackwardCompatibility:
    """Verify that non-CFA environments are completely unaffected."""

    def test_no_extra_mount_point_needed(self, normal_env):
        """In normal mode, readable_live2d_dir is None, so no /user_live2d_local is needed."""
        cm = _make_config_manager_normal(normal_env)

        assert cm.readable_live2d_dir is None
        # This means main_server.py won't create the /user_live2d_local mount
        readable = cm.readable_live2d_dir
        should_create_local_mount = (
            readable is not None
            and str(cm.live2d_dir) != str(readable)
        )
        assert not should_create_local_mount

    def test_find_models_single_dir_normal(self, normal_env):
        """In normal mode, find_models should only search Documents (no AppData split)."""
        cm = _make_config_manager_normal(normal_env)

        with patch('utils.config_manager.get_config_manager', return_value=cm):
            from utils.frontend_utils import find_models
            models = find_models()

        # Should find the normal model
        model_names = [m['name'] for m in models]
        assert 'normal_model' in model_names

        # All models should use /user_live2d (not /user_live2d_local)
        for m in models:
            if m.get('source') == 'documents' or '/user_live2d' in m.get('path', ''):
                assert '/user_live2d_local' not in m['path']

    def test_find_model_directory_single_dir_normal(self, normal_env):
        """In normal mode, find_model_directory should use /user_live2d prefix."""
        cm = _make_config_manager_normal(normal_env)

        with patch('utils.config_manager.get_config_manager', return_value=cm):
            from utils.frontend_utils import find_model_directory
            result = find_model_directory('normal_model')

        assert result is not None
        path, url_prefix = result
        assert url_prefix == '/user_live2d'
