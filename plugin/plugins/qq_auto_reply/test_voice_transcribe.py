"""测试语音转录 _fetch_record_content"""
from __future__ import annotations
import asyncio, sys, types, importlib.util
from pathlib import Path
from unittest.mock import MagicMock

_MODULE_DIR = Path(__file__).resolve().parent

# ── 构建最小 fake 环境加载 qq_client ──
_mock_mods = ["websockets", "websockets.exceptions", "websockets.asyncio",
              "websockets.asyncio.client", "PIL", "PIL.Image", "utils.file_utils"]
for m in _mock_mods:
    sys.modules[m] = MagicMock()

for pkg in ["plugin", "plugin.plugins", "plugin.plugins.qq_auto_reply"]:
    if pkg not in sys.modules:
        sys.modules[pkg] = types.ModuleType(pkg)
        sys.modules[pkg].__path__ = []

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

_spec = importlib.util.spec_from_file_location(
    "plugin.plugins.qq_auto_reply.qq_client",
    _MODULE_DIR / "qq_client.py")
_qq = importlib.util.module_from_spec(_spec)
sys.modules["plugin.plugins.qq_auto_reply.qq_client"] = _qq
_spec.loader.exec_module(_qq)
QQClient = _qq.QQClient

# ═══════════════════════════════════════════════════════════

async def test_no_transcriber():
    """无 transcriber → 仅标记 [语音]"""
    c = QQClient(onebot_url="ws://0.0.0.0:6199")

    async def _get_record(_):
        return {"data": {"base64": "AAAA"}}
    c.get_record = _get_record

    msg = {"raw_message": "你在说什么？"}
    await c._fetch_record_content(msg, ["file_001"])
    assert "[语音]" in msg["raw_message"], msg["raw_message"]
    print("[PASS] 无 transcriber →", msg["raw_message"])


async def test_with_transcriber():
    """有 transcriber → 转文字"""
    c = QQClient(onebot_url="ws://0.0.0.0:6199")
    c._voice_transcriber = lambda b64: asyncio.sleep(0, "我今天好开心呀") or "我今天好开心呀"

    async def _get_record(_):
        return {"data": {"base64": "AAAA"}}
    c.get_record = _get_record

    msg = {"raw_message": "你在说什么？"}
    await c._fetch_record_content(msg, ["file_001"])
    assert "我今天好开心呀" in msg["raw_message"], msg["raw_message"]
    assert "[语音]" in msg["raw_message"], msg["raw_message"]
    print("[PASS] 有 transcriber →", msg["raw_message"])


async def test_transcriber_empty():
    """转录返回空 → 回退 [语音]"""
    c = QQClient(onebot_url="ws://0.0.0.0:6199")
    c._voice_transcriber = lambda b64: asyncio.sleep(0, "") or ""

    async def _get_record(_):
        return {"data": {"base64": "AAAA"}}
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
    # 不应抛异常
    await c._fetch_record_content(msg, ["file_bad"])
    print("[PASS] get_record 失败 → 优雅降级")


async def test_transcriber_raises():
    """transcriber 抛异常 → 回退 [语音]"""
    c = QQClient(onebot_url="ws://0.0.0.0:6199")
    c._voice_transcriber = lambda b64: (_ for _ in ()).throw(RuntimeError("API炸了"))

    async def _get_record(_):
        return {"data": {"base64": "BBBB"}}
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
    """用本体 core_config 的 ASR key 测试各 provider"""
    providers = _get_transcribe_config()
    if not providers:
        print("  跳过真实API测试（未配置 ASSIST_API_KEY_*）")
        return

    import base64 as b64, glob, os as _os
    test_b64 = ""
    for base in [r"D:\qq\temp\Tencent Files\3281414178\nt_qq\nt_data\Ptt",
                 r"D:\qq\temp\Tencent Files"]:
        pattern = _os.path.join(base, "**", "*.amr")
        for f in sorted(glob.glob(pattern, recursive=True), key=_os.path.getmtime, reverse=True)[:5]:
            try:
                with open(f, "rb") as fh:
                    test_b64 = b64.b64encode(fh.read()).decode()
                print(f"  音频: {_os.path.basename(f)} ({len(test_b64)} chars)")
                break
            except Exception: continue
        if test_b64: break
    if not test_b64:
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
            return {"data": {"base64": test_b64}}
        c.get_record = _get_record

        msg = {"raw_message": "测试"}
        await c._fetch_record_content(msg, ["file_test"])
        print(f"[PASS] {prov_name} → {msg['raw_message']}")


async def main():
    await test_no_transcriber()
    await test_with_transcriber()
    await test_transcriber_empty()
    await test_get_record_fails()
    await test_transcriber_raises()
    await test_real_api()
    print("\n[OK] All voice tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
