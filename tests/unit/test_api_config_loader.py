"""API 配置加载器的轻量回归测试。"""

from utils.api_config_loader import get_cosyvoice_clone_model


def test_cosyvoice_intl_uses_region_supported_clone_model():
    """阿里国际版不能回退到仅北京区域支持的 v3.5 模型。"""
    assert get_cosyvoice_clone_model('cosyvoice') == 'cosyvoice-v3.5-plus'
    assert get_cosyvoice_clone_model('cosyvoice_intl') == 'cosyvoice-v3-plus'
    assert get_cosyvoice_clone_model('qwen_us') == 'cosyvoice-v3-plus'
    assert get_cosyvoice_clone_model('us') == 'cosyvoice-v3-plus'
    assert get_cosyvoice_clone_model('https://dashscope-us.aliyuncs.com/compatible-mode/v1') == 'cosyvoice-v3-plus'
