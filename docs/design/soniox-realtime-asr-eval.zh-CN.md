# Soniox Real-Time ASR 准入与 Smoke

本评测只决定 Soniox 是否可作为海外 `auto` 路由的优先 ASR，不改变中国大陆默认跟随当前 Core 的策略。

## 准入线

| 维度 | 目标 |
|---|---:|
| first partial P95 | ≤ 500 ms |
| 源音频 EOS → `<end>` P95 | ≤ 600 ms |
| 连续稳定性 | 20 轮无重复、无丢失 |
| 中文效果 | 不显著低于现有 Core ASR 基线 |
| 海外主要语言 | en / ja / es 等达到可用水平 |

脚本只接受 16 kHz、单声道、PCM16 WAV。它按 80 ms 二进制帧实时上传，在源音频结束后补静音并等待真正的 `<end>`；不会在音频发完时立即发送空帧。因此测得的是语义 endpoint，而不是连接关闭耗时。

```powershell
$env:SONIOX_API_KEY = "<your-key>"
uv run scripts/soniox_realtime_smoke.py sample.wav `
  --region jp `
  --language-hints en,ja,es `
  --output docs/design/generated/soniox_realtime_smoke.json
```

`<end>` 与 `<fin>` 只作为控制 token，均不进入 transcript。输出文件包含测试原文，属于显式评测产物，不应提交真实用户语音或生产对话结果。

## 2026-07-15 在线 smoke 记录

使用仓库内置、非用户隐私的 16 kHz 单声道 PCM16 提示音测试：

- US / 英文：链路完成并收到 `<end>`；first token 约 3.07 秒，源 EOS → `<end>` 约 1.67 秒，未达到准入线。
- US / 中文：收到首 token，但最终返回 Soniox 408，未收到 `<end>`，未达到准入线。
- JP / 日文：当前测试 key 在 JP endpoint 返回 401，无法据此评价 JP 网络或识别质量。

结论：协议闭环已在线验证，但当前样本、区域授权与延迟结果不足以通过产品准入。`auto` 仍需显式地区配置和 Soniox key，且在完成真实海外语音 20 轮/P95 评测前保持实验开关，不扩大为所有用户默认。
