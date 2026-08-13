# Phase 4C-B：可验证的 CAM++ Speaker Shadow 后端

## 1. 目标

本阶段只为现有、默认关闭的 Speaker Shadow 运行时提供一个可验证的
CAM++ score-only 后端。它不改变 ASR、Smart Turn、Silero VAD 或 Provider
的任何决策，也不把说话人身份能力接入 Core。

后端接收一份调用方已经持有的、仅内存的 192 维 CAM++ reference
embedding，并对现有 Shadow candidate PCM 计算余弦相似度。相似度只允许
进入测试或离线评估回调；生产 ASR 路径仍然完全由 Provider 和 endpointing
现有事实驱动。

## 2. 边界

本阶段新增：

- CAM++ 16 kHz frontend、ONNX 合同校验和 score-only backend；
- 可被 Windows `spawn` 序列化、构造时零 I/O 的 backend factory；
- 固定 revision、许可、大小和 SHA-256 的资产 manifest；
- 本地准备、离线评估及 Windows/Linux/Docker 发行链；
- backend、资产和发行合同的聚焦测试。

本阶段明确不包含：

- `voice_turn` 或 `voice_identity` 模块；
- Speaker Profile、Enrollment、UI、API、配置或持久化；
- Core composition、raw similarity observer 产品接口或运行中热切换；
- 对 `asr_client/runtime.py`、`endpointing/**`、现有 Shadow contracts/runtime、
  workers、routers 的修改；
- 模型下载、用户缓存或任何运行时网络回退。

Provider-neutral reference、显式 Enrollment 和 Core composition 必须分别经过
后续设计与独立 PR。

## 3. 依赖方向

```text
core/asr_runtime
  -> asr_client.runtime.SpeakerShadowFactory
      -> endpointing.detector_runtime
          -> speaker_shadow.contracts

speaker_shadow.campplus
  -> speaker_shadow.asset_manifest

speaker_shadow.runtime
  -> speaker_shadow.contracts.SpeakerShadowBackendFactory
  <- CampPlusBackendFactory (structural protocol implementation)
```

CAM++ 代码不得反向依赖 Core、endpointing、workers、Provider、route、queue、
candidate 生命周期或模型所有者。`speaker_shadow/__init__.py` 保持惰性且只含
包说明。

## 4. 生命周期和故障边界

Factory 构造只复制并归一化 reference embedding，保存可序列化的路径配置；
不得读盘、计算 SHA、导入 ONNX Runtime、创建锁、task、process 或 session。

首个达到现有 Shadow 最小时长的 candidate 才触发既有
`SpeakerShadowRuntime` 创建 `spawn` 子进程。子进程中的 backend `load()` 才：

1. 按统一规则解析并验证资产；
2. 函数内导入 `onnxruntime`；
3. 创建一个 CPU session并验证 input、output 和 metadata 合同。

`score()` 只返回有限的余弦分数，不拥有 ASR 执行权。加载、评分和关闭继续由
现有 Shadow runtime 的硬超时、fail-open、idle unload 和进程终止边界托管。
一个 runtime 最多持有一个 CAM++ session。

父进程与子进程各自持有独立的 reference 副本。Runtime 关闭时会调用父侧
factory 的幂等 `close()` 擦除父副本；子进程 backend 和 factory 关闭时分别擦除
各自副本。候选 embedding 与 feature/output 临时数组在计算后尽力清零。

## 5. 固定资产

资产只允许位于：

`main_logic/asr_client/speaker_shadow/models`

显式 override 存在时只验证该目录，失败不回退。未指定 override 时，顺序为：

1. PyInstaller `_MEIPASS` 下的 package-local 路径；
2. 冻结可执行文件父目录下的 package-local 路径；
3. 源码包内的 `models` 目录。

每个候选来源都必须在同一目录内取得 manifest 和权重，并执行完全相同的
revision、size 与 SHA-256 校验。发行产物还必须在该目录包含 notice。禁止跨目录
拼装、`data/speaker_models`、用户缓存和运行时下载。

权重不进入 Git、wheel 或 sdist。正式桌面和 Docker 构建在原生 runner 上按
manifest 准备资产，构建产物内再离线复验，并要求权重、manifest、notice 各一
份且不存在旧资产目录。

## 6. 隐私与可观测性

PCM、reference/candidate embedding、similarity、输入路径、candidate key 和身份
信息不得写入日志、持久化或 aggregate snapshot。生产错误仅使用低基数类型，
例如 `asset_missing`、`asset_size_mismatch`、`asset_sha256_mismatch`，不得拼接
绝对路径或实际摘要。

离线评估工具可以在进程内输出按输入序号排列的 similarity 和阈值对照，但不得
回显文件路径、embedding 或 PCM；退出前应释放并尽力擦除这些内存对象。

## 7. 验收

- frontend 与 `kaldi_native_fbank` 对确定性合成音频保持数值一致；
- embedding 固定为 float32、192 维、finite、L2-normalized；
- factory 构造零 I/O、可 pickle，并在关闭后擦除父侧 reference；
- 资产 identity、解析顺序、离线准备和损坏拒绝都有测试；
- Windows/Linux/Docker 产物各只包含一份固定权重、manifest 和 notice；
- 现有 Shadow 的 load/score/close 超时、idle unload、spawn 和完整关闭测试继续
  通过；
- `voice_turn/**`、`voice_identity/**`、Core、endpointing 和 workers 对主线保持
  零差异。
