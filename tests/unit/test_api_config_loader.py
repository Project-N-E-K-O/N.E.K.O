"""API 配置加载器的轻量回归测试。"""

from utils.api_config_loader import (
    get_cosyvoice_clone_model,
    get_cosyvoice_user_preferred_model,
)


def test_cosyvoice_intl_uses_region_supported_clone_model():
    """阿里国际版不能回退到仅北京区域支持的 v3.5 模型。"""
    assert get_cosyvoice_clone_model('cosyvoice') == 'cosyvoice-v3.5-plus'
    assert get_cosyvoice_clone_model('cosyvoice_intl') == 'cosyvoice-v3-plus'
    assert get_cosyvoice_clone_model('qwen_us') == 'cosyvoice-v3-plus'
    assert get_cosyvoice_clone_model('us') == 'cosyvoice-v3-plus'
    assert get_cosyvoice_clone_model('https://dashscope-us.aliyuncs.com/compatible-mode/v1') == 'cosyvoice-v3-plus'


def _patch_core_config(monkeypatch, core_config):
    class _CM:
        def get_core_config(self):
            return core_config

    monkeypatch.setattr('utils.config_manager.get_config_manager', lambda: _CM())


def test_user_preferred_model_adopts_cosyvoice_model_id(monkeypatch):
    """A cosyvoice-v* model on the TTS endpoint must drive enrollment (Issue #3147)."""
    _patch_core_config(monkeypatch, {'TTS_MODEL': 'cosyvoice-v3.5-flash'})
    assert get_cosyvoice_user_preferred_model('cosyvoice') == 'cosyvoice-v3.5-flash'


def test_user_preferred_model_ignores_other_vendor_ids(monkeypatch):
    """Other vendors' model IDs / blanks are rejected; callers fall back to the default."""
    for value in ('', 'tts-1', 'speech-01-turbo', 'qwen3-tts-flash-realtime'):
        _patch_core_config(monkeypatch, {'TTS_MODEL': value})
        assert get_cosyvoice_user_preferred_model('cosyvoice') is None


def test_user_preferred_model_skipped_for_intl(monkeypatch):
    """Intl only supports cosyvoice-v3-plus for enrolled voices (the existing default)."""
    _patch_core_config(monkeypatch, {'TTS_MODEL': 'cosyvoice-v3.5-flash'})
    assert get_cosyvoice_user_preferred_model('cosyvoice_intl') is None
