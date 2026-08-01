# Phase 4C：无执行权的 Speaker Shadow

## 背景与拆分

Phase 4B 已把 Provider 故障与 ASR 结束权收口到独立 ASR runtime。Phase 4C
只增加一个默认关闭的旁路观察器，用候选音频评估说话人相似度，但不参与任何
在线决策。

交付拆成两个可独立回滚的 PR：

- PR-A 只提供通用合同、受限 runtime、Detector 镜像点和 Core 的 opaque
  factory bridge，不包含模型实现或资产。
- PR-B 堆叠在 PR-A 上，提供固定 revision 的 CAM++ backend、资产校验、准备与
  评估脚本以及发行链。模型权重不进入 Git，运行时不联网。

本文件描述 PR-A 的长期权限与依赖合同。后续 backend 不得放宽这些边界。

## 权限模型

Speaker Shadow 是纯观察器。它不能创建、推进、结束或撤销 ASR candidate，也
不能执行 commit、seal、final、cancel、route、Provider 选择或 fallback。

Detector 只镜像已经通过当前 ingress/candidate fence 的不可变 PCM16。Shadow
的 `submit()`、`finish_candidate()`、相似度、callback 结果和异常都不能成为
主链条件。`submit()` 与 `finish_candidate()` 只能做有界、非阻塞的立即入队；
队列满、stale、closing、closed 或 Shadow 异常时必须立即丢弃并返回，禁止等待
队列、worker、backend、关闭锁或推理完成。Provider PCM 的字节、顺序、时序、
终态、endpoint、lifecycle 与主链延迟不受影响。

Provider/ASR 的真实错误继续遵循 Phase 4B 的 fail-closed 规则；Shadow 的
factory、加载、推理、callback、reset 与关闭错误全部 fail-open。两类故障不得
相互转换。

## 依赖方向

```text
core/asr_runtime
  -> asr_client.runtime
       -> endpointing.detector_runtime
            -> speaker_shadow.contracts
       -> speaker_shadow.runtime/contracts

speaker_shadow.campplus (PR-B)
  -> speaker_shadow.runtime (PR-A)
  -> speaker_shadow.contracts (PR-A)
  -> speaker_shadow.asset_manifest (PR-B，拥有 manifest 解析与校验 API)
```

Core 只能从 `asr_client.runtime` 获取 `SpeakerShadowFactory`，不得直接导入
Speaker Shadow、endpointing 实现、CAM++ 或模型资产。endpointing 只能导入
`speaker_shadow.contracts`，不得知道 concrete backend。

`speaker_shadow` 是依赖叶子，不能反向导入 Core、父级 ASR runtime、
endpointing、workers、provider policy、lifecycle、voice input、routers 或
scripts。包 `__init__.py` 只保留说明文字，不重导出模型类，也不触发
ONNX Runtime import。

## 默认关闭与创建

Core 的 factory 是私有字段，默认值恒为 `None`。未配置 factory、factory 返回
`None` 或 Shadow config 未启用时，链路必须满足：

- 不复制 PCM；
- 不创建 asyncio task、执行 worker 或模型 session；
- 不导入模型 runtime；
- 不改变现有调用参数与 ASR 行为。

配置后的 factory 只允许做轻量、同步、无 IO 的纯对象构造；模型、进程和
worker 仍延迟到首个有效提交后创建。构造失败只禁用本次 Shadow。禁止把
factory 放入不可取消的后台线程，否则 ASR 启动取消后可能遗留无人回收的
观察器。

## Candidate fence 与代际

Shadow candidate 只使用匿名的 `SpeakerShadowCandidateKey`：Detector epoch、
Shadow generation 和观察 scope。该键不包含用户身份、路径、Provider 名称或
产品 ID。

Provider candidate scope 在 lifecycle 与音频 dispatcher 都接受同一 payload
后开启，只由既有的 seal_provider_candidate() fence 结束；局部 candidate
pause 不获得新的结束权。SmartTurn scope 在适配器实际消费当前 fence 接受的
PCM 时镜像；评估中的歧义尾帧必须等决策明确后再归属，COMPLETE 时只能在完成
fence 后以 successor candidate 进入。

invalidate、overflow reset、route reset/swap、endpointing failure 与 close 都
递增 generation 并清空当前 identity。旧队列 PCM、旧 finish 和旧推理结果只能
计为 stale 后丢弃，不能复活已 dropped 的 candidate，也不能串入 successor。

## 资源上限与执行隔离

输入固定为 16 kHz、mono、PCM16LE；单 candidate 最多保留 4 秒。每个 runtime
只有一个串行 backend 宿主和一个模型 session。queue、candidate buffer、
in-flight token 与 finalized tombstone 都有配置上限和不可突破的安全上限。

模型加载与 CPU 推理不得在事件循环运行。backend 操作必须有硬超时；关闭时
必须能够终止并回收执行宿主，不能在 `close()` 返回后遗留 runtime task、
worker 或模型 session。idle unload 释放 session，下一次有效候选再按需创建。

## 结果与隐私

每个 candidate 只能进入一个终态：`scored`、`insufficient`、`dropped` 或
`failed`。终态只影响聚合指标，不影响 ASR。

PCM、embedding、相似度、路径、candidate identity 与用户身份都不得持久化、
写日志或进入产品 API。生产 snapshot 只暴露计数、耗时和资源数量。原始相似度
只允许通过进程内测试/评估 callback 短暂使用，callback 结束后不保留。

## 后续 CAM++ 发行边界

PR-B 的模型解析顺序固定为：显式 override、PyInstaller `_MEIPASS`、可执行文件
父目录、package-local `models`。不得恢复 `data/speaker_models`、用户缓存 fallback
或运行时下载。包括显式 override 在内的每个来源都必须通过同一份 manifest
校验，并匹配固定的 revision、size 与 SHA256；生产加载不得绕过该校验。

资产 manifest 必须固定模型仓库、revision、license、size 与 SHA256。发行产物
只能包含一份正确权重。CAM++ PR 可单独回滚；PR-A 保持 factory 为 `None` 即为
完整止血。
