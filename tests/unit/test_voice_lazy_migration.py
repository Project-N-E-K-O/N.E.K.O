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


# ── 不变式：characters.json 存的是「指向 voice 库的绑定引用」，不是 voice 本体 ──

def test_binding_is_a_reference_not_an_inlined_voice():
    """characters.json 里只存绑定引用 {source,provider,ref}，绝不 inline voice 库里的
    本体字段（audio_md5/file_url/created_at/prefix...）或端点 config。"""
    cm = get_config_manager()
    stored = cm.voice_id_to_storage_value("eleven:abc123")
    assert isinstance(stored, dict)
    assert set(stored.keys()) <= {"source", "provider", "ref"}
    # voice 本体只能在 voice_storage.json（库），不能漏进绑定
    forbidden = {"audio_md5", "file_url", "dashscope_base_url", "elevenlabs_base_url",
                 "created_at", "name", "prefix", "config"}
    assert not (set(stored.keys()) & forbidden)


def test_binding_roundtrips_to_exact_library_key():
    """绑定必须能还原成 voice 库里的精确 key（character→库内 voice 的链不断）。

    库 key 形态：elevenlabs/gptsovits 带前缀（voice_storage 就以前缀 key 存）、
    cosyvoice/minimax/free/native 裸 id。store→read 对每种都还原成同一个 key。
    """
    cm = get_config_manager()
    for library_key in ("eleven:abc123", "gsv:my_voice", "cosyclone-001", "voice-tone-X", "Puck"):
        stored = cm.voice_id_to_storage_value(library_key)
        assert read_legacy_voice_id(stored) == library_key, library_key
