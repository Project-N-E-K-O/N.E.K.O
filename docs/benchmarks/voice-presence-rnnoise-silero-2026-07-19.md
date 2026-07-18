# RNNoise / Silero 语音存在检测预备报告（2026-07-19）

## 结论

当前不能在 #2398 中直接关闭 Silero，也不能把现有 RNNoise `peak >= 0.35` 当作单独的节流依据。

本次可复现预备基准中：

- 当前 RNNoise 单帧 peak 策略召回 100%，但负样本误触发率达到 82.09%，会被风扇型噪声、白/粉噪声、突发音和游戏撞击音频繁唤醒。
- RNNoise 改为“连续 100ms 且概率不低于 0.8”后，探索性结果为 97.50% 召回、0% 负样本误触发、98.75% balanced accuracy，触发延迟中位数 120ms、p95 320ms。
- Silero raw 16k（0.5 阈值、持续 200ms）为 95.42% 召回、0% 负样本误触发、97.71% balanced accuracy。
- Silero 接收 RNNoise/AGC/limiter 后音频时为 96.67% 召回、0% 负样本误触发、98.33% balanced accuracy。
- RNNoise 100ms 结果说明“空闲阶段只用 RNNoise 做预热证据”值得继续 shadow；它不构成删除 Silero 的生产证据。该阈值是在同一数据集上探索出来的，俄语组召回只有 89.58%，且本报告没有测试真实房间、电视、回声、远场麦克风和重叠说话。

因此 #2398 的推荐决策是：保留现有 RNNoise + Silero shadow 和安全降级，新增真实设备报告后再决定是否让 `LOCAL_LISTEN` 进入 RNNoise-only；暂不改变默认运行策略。

## 本报告回答什么

本报告只回答低成本资源问题：一个音频片段中是否可能有人说话。

```text
RNNoise / Silero
= 资源节流证据

SmartTurn COMPLETE
= 唯一逻辑 endpoint
```

即使未来 RNNoise-only 通过，也只允许它决定空闲 PCM 跳过、prewarm 和云端 transport 启停。它不能：

- 替代 SmartTurn；
- 让 Provider native final 或 Soniox `<fin>` 直接进入 Core；
- 把 PCM 送入 Core、Omni、小游戏或外部 consumer；
- 绕过 MicLease、ingress token、abort barrier 或有序 Activate/Seal；
- 在 SmartTurn 未 READY 时产生 Provider wire audio。

所有路径仍要求 `omni_mic_audio_bytes == 0`。

## 方法

### 语料

固定种子为 `2398`，共 307 个 2.5 秒片段、767.5 秒音频：

- 正样本 240 个：仓库内 en/ja/ko/ru/zh 教程 TTS，每种语言固定选 8 个文件；每个文件生成 clean、20/10/5/0/-5 dB SNR 六种片段。
- 负样本 67 个：静音、白噪声、粉噪声、50/60Hz 风扇型噪声、突发/键盘型脉冲各 12 个，加 7 个明确无语音的羽毛球撞击/挥拍音效。
- 合成噪声覆盖 -45/-35/-25/-15 dBFS；SNR 混合按活动语音区间 RMS 计算。
- 所有音频只在内存中处理；报告、日志和指标均不保存 PCM。

这不是带真实房间录音的最终 accuracy 报告。仓库 TTS 是已知语音正样本，合成噪声和人工筛选音效是已知非语音负样本，适合做实现筛选和发现明显误判，不足以单独决定生产阈值。

### 实际处理路径

- RNNoise：桌面 48kHz、480 samples/10ms 原生 runtime；使用 `AudioProcessor` 的真实 RNNoise → AGC → limiter → 16k resample 路径，每 20ms 读取一次 peak/EMA。
- Silero：v6.2.1 ONNX，16kHz、512 samples/32ms；模型 SHA-256 为 `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`。
- Silero presence 要求 200ms 持续证据，避免把单个 32ms spike 算作有人说话。
- RNNoise 额外扫描单帧 peak、EMA、连续 100ms 和连续 200ms 四种证据。

### 指标

由于正负样本数量不同，不能只看普通 accuracy。报告同时使用：

- speech recall / miss rate；
- negative specificity / false-positive rate；
- balanced accuracy；
- precision / F1；
- 首次预热触发延迟；
- 处理 realtime factor 和单进程 RSS 增量。

## 主结果

| 策略 | Speech recall | 负样本误触发 | Balanced accuracy | F1 | 触发延迟 median / p95 |
|---|---:|---:|---:|---:|---:|
| RNNoise peak ≥ 0.35（#2398 当前证据） | 100.00% | 82.09% | 58.96% | 89.72% | 0 / 20ms* |
| RNNoise 连续100ms ≥ 0.8（探索） | 97.50% | 0.00% | 98.75% | 98.73% | 120 / 320ms |
| RNNoise 连续200ms ≥ 0.7（探索） | 95.83% | 0.00% | 97.92% | 97.87% | 未单列 |
| Silero raw ≥ 0.5、持续200ms | 95.42% | 0.00% | 97.71% | 97.65% | 300 / 844ms |
| Silero after RNNoise ≥ 0.5、持续200ms | 96.67% | 0.00% | 98.33% | 98.31% | 332 / 794ms |

\* RNNoise peak 0.35 在 82.5% 的带噪语音片段中于真实语音开始前就被噪声触发，因此这个低延迟不代表有效的语音 onset 性能。

### 为什么当前 0.35 策略不能单独节流

| RNNoise peak 0.35 负样本 | 片段数 | 误触发率 |
|---|---:|---:|
| 静音 | 12 | 0% |
| 风扇型噪声 | 12 | 100% |
| 白噪声 | 12 | 100% |
| 粉噪声 | 12 | 100% |
| 突发/键盘型脉冲 | 12 | 100% |
| 游戏撞击/挥拍音效 | 7 | 100% |

RNNoise 本质上是降噪器携带的 speech probability。2.5 秒片段只要任意一个 10ms frame 超过 0.35 就打开 candidate，长时间观察会放大偶发峰值。简单提高单帧阈值仍不能形成稳定折中：

| RNNoise 单帧 peak 阈值 | Speech recall | 负样本误触发 |
|---:|---:|---:|
| 0.80 | 99.17% | 49.25% |
| 0.90 | 98.75% | 31.34% |
| 0.95 | 97.08% | 19.40% |
| 0.99 | 95.00% | 1.49% |

结论是必须引入时间条件；不能只替换硬编码阈值。

### RNNoise-only 探索结果

| RNNoise 连续100ms阈值 | Speech recall | 负样本误触发 | Balanced accuracy |
|---:|---:|---:|---:|
| 0.70 | 97.92% | 2.99% | 97.47% |
| 0.80 | 97.50% | 0.00% | 98.75% |
| 0.90 | 96.67% | 0.00% | 98.33% |

连续 100ms、阈值 0.8 是本语料上的最佳探索点，但不能直接作为产品默认值：

- 该点使用同一份语料选阈值并汇报结果，没有独立 holdout，存在过拟合。
- clean、20、10、5、0、-5dB 的召回分别为 97.5%、97.5%、92.5%、97.5%、100%、100%；10dB 反而更差，说明小样本与噪声类型组合仍在显著影响结果。
- en/ja/ko/ru/zh 召回分别为 100%、100%、97.92%、89.58%、100%；最低语言组未达到可发布标准。
- 未覆盖真实风扇机械谐波、空调、电视/音乐、扬声器回声、远距离和多人重叠说话。

## CPU 与内存

环境：Windows 10.0.26200、Python 3.11.15、16 logical CPUs。以下数字是同一进程中的一次实际 runtime 基准，不是跨设备保证值。

| 路径 | 处理时间 / 1秒音频 | Realtime factor | 近似 RSS 增量 |
|---|---:|---:|---:|
| RNNoise + AGC + limiter + 48k→16k 完整桌面管线 | 45.03ms | 4.50% | 0.45 MiB |
| Silero raw 16k | 4.09ms | 0.41% | 31.88 MiB |
| RNNoise后额外 Silero | 3.82ms | 0.38% | 已计入上行 Silero RSS |

解释：

- 如果 RNNoise 本来就因“降噪”开启，复用其 probability 的增量成本接近零；此时空闲阶段不加载 Silero，理论上可再省约 3.8–4.1ms CPU/每秒音频和约 31.9MiB RSS。
- 如果 RNNoise 原本关闭，仅为了节流而启动完整 RNNoise 管线，在这台机器上它的处理时间约为 Silero-only 的 11 倍；因此“RNNoise-only 一定更省 CPU”不成立。
- Silero 的主要成本是约 32MiB 模型/session 内存，而非持续 CPU。
- RSS 是按 RNNoise 后再加载 Silero的单进程差值，受 allocator 和加载顺序影响；最终发布前必须在 Electron 打包环境、低端 CPU 和移动端分别复测。

## 能否完全不启动 VAD

现在只能回答一半：

1. 空闲 prewarm：RNNoise 连续证据显示出可行性，值得让 `LOCAL_LISTEN` 先做 RNNoise-only shadow，未来可能在空闲期不加载 Silero。
2. SmartTurn 调度：本报告没有证明 RNNoise 能可靠产生 `CANDIDATE_PAUSE`。当前 Silero 不只参与 onset，还负责在持续静音后请求 SmartTurn evaluation。即使 RNNoise 能判断“可能有人说话”，也不能据此直接卸载整条 Silero 路径。

低风险演进顺序应为：

```text
阶段1（当前）
RNNoise + Silero shadow
→ SmartTurn 唯一 endpoint

阶段2（真实报告通过后）
LOCAL_LISTEN: RNNoise-only
候选打开后: lazy-load Silero
→ Silero 触发 pause
→ SmartTurn 唯一 endpoint

阶段3（另做 pause 报告后）
RNNoise onset + RNNoise silence/periodic evaluation
→ SmartTurn 唯一 endpoint
→ 才可能全程不加载 Silero
```

阶段2可以先获得大部分空闲内存收益，同时不把未经验证的 RNNoise silence 当成 SmartTurn 调度依据。

## 下一份真实报告的发布门槛

在改变 #2398 默认策略前，至少需要：

- 真实桌面麦克风：安静、风扇、空调、键盘、电视/音乐、扬声器回声、远场、多人说话和突发噪声；
- 多档设备和麦克风，独立 calibration/holdout，阈值只能在 calibration 集选择；
- 以每 10 分钟空闲时的 false prewarm 次数衡量长期误触发，而不是只看短 clip FPR；
- clean/20/10/5dB speech recall ≥99%，0dB ≥97%，任一语言/设备组 ≥97%；
- false prewarm ≤1 次/10分钟空闲音频；
- onset p95 ≤500ms，pre-roll 后首字丢失率为 0；
- 单独验证 RNNoise silence 或 periodic evaluation 是否能可靠请求 SmartTurn，不能用 presence 报告代替 pause 报告；
- Electron 打包环境和移动 16k 路径分别测 CPU/RSS。当前 RNNoise evidence 来自桌面 48k 路径，不能外推到移动端。

若未达到门槛，继续使用 RNNoise 预热 + Silero pause + SmartTurn endpoint；不得退回 Omni、Provider-native endpoint 或 Silero-only final。

## 复现

评测实现位于 `tools/voice_eval/evaluate_speech_presence.py`：

```powershell
uv run python tools/voice_eval/evaluate_speech_presence.py `
  --speech-per-locale 8 `
  --negative-per-kind 12 `
  --seed 2398 `
  --output speech-presence-2398.json
```

JSON 只包含模型分数、混淆矩阵、聚合时延、环境和所选仓库资产路径，不包含原始 PCM。
