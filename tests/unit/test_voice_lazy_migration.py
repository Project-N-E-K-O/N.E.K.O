"""voice_id 并查集式惰性迁移的往返不变式测试（声音来源统一架构 §6）。

写侧 ``ConfigManager.voice_id_to_storage_value`` 把用户设的 legacy voice_id 迁成结构对象；
读侧 ``read_legacy_voice_id`` 把扁平串 / 结构对象两形态统一读回 legacy 串。核心不变式：
**store → read 往返还原原 legacy 串**，所以迁不迁移对下游 dispatch / validate 透明。
"""

from utils.config_manager import get_config_manager, get_reserved, set_reserved
from utils.voice_config import read_legacy_voice_id


def test_clone_prefix_storage_roundtrip():
    """clone 前缀（eleven:/gsv:）写成对象再读回 == 原串（确定性，不依赖运行时上下文）。"""
    cm = get_config_manager()
    for vid in ("eleven:abc", "gsv:my_voice"):
        stored = cm.voice_id_to_storage_value(vid)
        assert isinstance(stored, dict)  # 用户设音色 → 迁成结构对象
        assert read_legacy_voice_id(stored) == vid


def test_empty_voice_stored_as_empty_string():
    cm = get_config_manager()
    assert cm.voice_id_to_storage_value("") == ""
    assert cm.voice_id_to_storage_value(None) == ""
    assert read_legacy_voice_id(cm.voice_id_to_storage_value("")) == ""


def test_object_form_in_char_config_reads_back_legacy():
    """模拟运行时读：角色配置里 voice 已迁成对象时，get_reserved + read_legacy_voice_id
    给出 dispatch 消费的 legacy 串（即 _get_voice_id 的行为）。"""
    cfg = {}
    set_reserved(cfg, "voice_id", {"source": "clone", "provider": "elevenlabs", "ref": "abc"})
    raw = get_reserved(cfg, "voice_id", default="", legacy_keys=("voice_id",))
    assert read_legacy_voice_id(raw) == "eleven:abc"


def test_legacy_flat_string_still_reads():
    """未触碰的存量扁平串照常读出（惰性迁移不强制全表迁移）。"""
    cfg = {}
    set_reserved(cfg, "voice_id", "gsv:legacy_voice")
    raw = get_reserved(cfg, "voice_id", default="", legacy_keys=("voice_id",))
    assert read_legacy_voice_id(raw) == "gsv:legacy_voice"
