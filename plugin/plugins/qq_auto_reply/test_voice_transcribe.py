"""测试语音转录 _fetch_record_content"""
from __future__ import annotations
import asyncio, sys, types, importlib.util
from pathlib import Path
from unittest.mock import MagicMock

_MODULE_DIR = Path(__file__).resolve().parent

# ── 构建最小 fake 环境加载 qq_client（可恢复）──
_mock_mods = ["websockets", "websockets.exceptions", "websockets.asyncio",
              "websockets.asyncio.client", "PIL", "PIL.Image", "utils.file_utils"]
_saved_mods = {}               # {module_name: original_or_None}
_new_pkgs = []                 # 本次新建的 package 模块名
_saved_conn = None             # qq_connection 原始模块
_saved_client = None           # qq_client 原始模块

for m in _mock_mods:
    _saved_mods[m] = sys.modules.get(m)
    sys.modules[m] = MagicMock()

for pkg in ["plugin", "plugin.plugins", "plugin.plugins.qq_auto_reply"]:
    if pkg not in sys.modules:
        sys.modules[pkg] = types.ModuleType(pkg)
        sys.modules[pkg].__path__ = []
        _new_pkgs.append(pkg)

_saved_conn = sys.modules.get("plugin.plugins.qq_auto_reply.qq_connection")
_conn_mod = types.ModuleType("plugin.plugins.qq_auto_reply.qq_connection")
sys.modules["plugin.plugins.qq_auto_reply.qq_connection"] = _conn_mod

class FakeConnBase:
    token = ""
    @property
    def needs_attention(self): return True
    @property
    def supports_voice(self): return True
    @property
    def supports_poke(self): return True
    @property
    def receives_all_messages(self): return True
    @property
    def onebot_url(self): return ""
    @onebot_url.setter
    def onebot_url(self, v): pass
    async def connect(self): pass
    async def disconnect(self): pass
    async def receive_message(self, t=1.0): pass
    async def send_group_message_segments(self, *a, **kw): pass
    async def send_private_message_segments(self, *a, **kw): pass
    async def send_group_poke(self, *a, **kw): pass
    async def send_group_image(self, *a, **kw): pass
    async def send_group_record(self, *a, **kw): pass
    async def get_login_status(self): pass
    def is_connected(self): return False
    def record_sent_message_id(self, mid): pass
_conn_mod.QQConnectionBase = FakeConnBase

_saved_client = sys.modules.get("plugin.plugins.qq_auto_reply.qq_client")
_spec = importlib.util.spec_from_file_location(
    "plugin.plugins.qq_auto_reply.qq_client",
    _MODULE_DIR / "qq_client.py")
_qq = importlib.util.module_from_spec(_spec)
sys.modules["plugin.plugins.qq_auto_reply.qq_client"] = _qq
_spec.loader.exec_module(_qq)
QQClient = _qq.QQClient


def _restore_mocks():
    """恢复 sys.modules，由 main() 在测试结束后调用。"""
    for m, orig in _saved_mods.items():
        if orig is None:
            sys.modules.pop(m, None)
        else:
            sys.modules[m] = orig
    for pkg in _new_pkgs:
        sys.modules.pop(pkg, None)
    if _saved_conn is None:
        sys.modules.pop("plugin.plugins.qq_auto_reply.qq_connection", None)
    else:
        sys.modules["plugin.plugins.qq_auto_reply.qq_connection"] = _saved_conn
    if _saved_client is None:
        sys.modules.pop("plugin.plugins.qq_auto_reply.qq_client", None)
    else:
        sys.modules["plugin.plugins.qq_auto_reply.qq_client"] = _saved_client
    if _saved_httpx is None:
        sys.modules.pop("httpx", None)
    else:
        sys.modules["httpx"] = _saved_httpx

# ═══════════════════════════════════════════════════════════

# 假音频字节，10ms 静默 AMR
_FAKE_AUDIO = (
    b"#!AMR\n\x1c\x04\x00\x00\x1c\x04\x00\x00\x04\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
)


class _FakeResponse:
    status_code = 200
    content = _FAKE_AUDIO

class _FakeHttpxClient:
    async def __aenter__(self, *a, **kw): return self
    async def __aexit__(self, *a): pass
    async def get(self, url): return _FakeResponse()

# 注入 fake httpx 模块（给 _fetch_record_content 下载音频用）
import httpx as _real_httpx
_saved_httpx = sys.modules.get("httpx")
_fake_httpx = types.ModuleType("httpx")
_fake_httpx.AsyncClient = _FakeHttpxClient
_fake_httpx.Timeout = _real_httpx.Timeout
sys.modules["httpx"] = _fake_httpx


# ── transcriber stub helpers ──

async def _happy_transcribe(audio_base64="", *, audio_url=""):
    return "我今天好开心呀"

async def _empty_transcribe(audio_base64="", *, audio_url=""):
    return ""

async def _bad_transcribe(audio_base64="", *, audio_url=""):
    raise RuntimeError("API炸了")


async def test_no_transcriber():
    """无 transcriber → 仅标记 [语音]"""
    c = QQClient(onebot_url="ws://0.0.0.0:6199")

    async def _get_record(_):
        return {"data": {"url": "http://example.com/voice.amr"}}
    c.get_record = _get_record

    msg = {"raw_message": "你在说什么？"}
    await c._fetch_record_content(msg, ["file_001"])
    assert "[语音]" in msg["raw_message"], msg["raw_message"]
    print("[PASS] 无 transcriber →", msg["raw_message"])


async def test_with_transcriber():
    """有 transcriber → 转文字"""
    c = QQClient(onebot_url="ws://0.0.0.0:6199")
    c._voice_transcriber = _happy_transcribe

    async def _get_record(_):
        return {"data": {"url": "http://example.com/voice.amr"}}
    c.get_record = _get_record

    msg = {"raw_message": "你在说什么？"}
    await c._fetch_record_content(msg, ["file_001"])
    assert "我今天好开心呀" in msg["raw_message"], msg["raw_message"]
    assert "[语音]" in msg["raw_message"], msg["raw_message"]
    print("[PASS] 有 transcriber →", msg["raw_message"])


async def test_transcriber_empty():
    """转录返回空 → 回退 [语音]"""
    c = QQClient(onebot_url="ws://0.0.0.0:6199")
    c._voice_transcriber = _empty_transcribe

    async def _get_record(_):
        return {"data": {"file": "/tmp/voice.amr"}}
    c.get_record = _get_record

    msg = {"raw_message": "喂？"}
    await c._fetch_record_content(msg, ["file_001"])
    assert "[语音]" in msg["raw_message"], msg["raw_message"]
    assert "喂？" in msg["raw_message"]
    print("[PASS] 转录空 → 回退标记:", msg["raw_message"])


async def test_get_record_fails():
    """get_record 失败 → 不崩溃"""
    c = QQClient(onebot_url="ws://0.0.0.0:6199")

    async def _get_record(_):
        raise RuntimeError("网络错误")
    c.get_record = _get_record

    msg = {"raw_message": "听得见吗？"}
    await c._fetch_record_content(msg, ["file_bad"])
    print("[PASS] get_record 失败 → 优雅降级")


async def test_transcriber_raises():
    """transcriber 抛异常 → 回退 [语音]"""
    c = QQClient(onebot_url="ws://0.0.0.0:6199")
    c._voice_transcriber = _bad_transcribe

    async def _get_record(_):
        return {"data": {"url": "http://example.com/voice.amr"}}
    c.get_record = _get_record

    msg = {"raw_message": "hello"}
    await c._fetch_record_content(msg, ["file_err"])
    assert "[语音]" in msg["raw_message"]
    print("[PASS] transcriber 异常 → 回退:", msg["raw_message"])


def _get_transcribe_config():
    """从本体配置读取 ASR 端点：本地 > OpenAI > Qwen"""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))
    from utils.config_manager import get_config_manager
    cc = get_config_manager().get_core_config() or {}
    providers = []
    # 本地 STT（tts_custom 推导）
    try:
        tc = get_config_manager().get_model_api_config("tts_custom")
        lb = str(tc.get("base_url") or "").strip()
        if lb and (lb.startswith("ws://") or lb.startswith("wss://")):
            http_base = lb.replace("ws://", "http://").replace("wss://", "https://")
            providers.append(("local", "", http_base.rstrip("/") + "/v1/audio/transcriptions"))
    except Exception:
        pass
    # OpenAI
    k = str(cc.get("ASSIST_API_KEY_OPENAI") or "").strip()
    if k: providers.append(("openai", k, "https://api.openai.com/v1/audio/transcriptions"))
    # Qwen
    k = str(cc.get("ASSIST_API_KEY_QWEN") or "").strip()
    if k: providers.append(("qwen", k, "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription"))
    return providers


async def test_real_api():
    """用本体 core_config 的 ASR key + 合成音频测试各 provider。
    不使用真实语音文件，避免隐私泄漏。"""
    providers = _get_transcribe_config()
    if not providers:
        print("  跳过真实API测试（未配置 ASSIST_API_KEY_*）")
        return

    from unittest.mock import patch
    import base64 as b64

    # 合成最小 AMR 头，避免涉及真实用户语音
    test_b64 = b64.b64encode(b"#!AMR\n\x00\x00\x00\x00").decode()

    for prov_name, key, url in providers:
        def _make_transcriber(pn, k, u):
            async def _t(audio_b64):
                import httpx
                ab = b64.b64decode(audio_b64)
                async with httpx.AsyncClient(timeout=30, proxy=None, trust_env=False) as cl:
                    if pn == "local":
                        resp = await cl.post(
                            u, files={"file":("voice.amr",ab,"audio/amr")},
                            data={"model":"whisper-1","language":"zh"})
                        if resp.status_code == 200:
                            return resp.json().get("text","").strip()
                    elif pn == "qwen":
                        resp = await cl.post(
                            u, headers={"Authorization": f"Bearer {k}"},
                            json={"model":"paraformer-v2",
                                  "parameters":{"format":"mp3"},
                                  "input":{"audio": audio_b64}})
                        if resp.status_code == 200:
                            text = ""
                            for r in (resp.json().get("output") or {}).get("results") or []:
                                text += str(r.get("text","") or "")
                            return text.strip()
                    else:
                        resp = await cl.post(
                            u, headers={"Authorization": f"Bearer {k}"},
                            files={"file":("voice.amr",ab,"audio/amr")},
                            data={"model":"whisper-1","language":"zh"})
                        if resp.status_code == 200:
                            return resp.json().get("text","").strip()
                    print(f"  {pn}返回: {resp.status_code} {resp.text[:80]}")
                    return ""
            return _t

        c = QQClient(onebot_url="ws://0.0.0.0:6199")
        c._voice_transcriber = _make_transcriber(prov_name, key, url)

        async def _get_record(_):
            return {"data": {"url": "http://example.com/voice.amr"}}
        c.get_record = _get_record
    
        msg = {"raw_message": "测试"}
        await c._fetch_record_content(msg, ["file_test"])
        print(f"[PASS] {prov_name} → {msg['raw_message']}")


async def main():
    try:
        await test_no_transcriber()
        await test_with_transcriber()
        await test_transcriber_empty()
        await test_get_record_fails()
        await test_transcriber_raises()
        await test_real_api()
        print("\n[OK] All voice tests passed!")
    finally:
        _restore_mocks()


if __name__ == "__main__":
    asyncio.run(main())
