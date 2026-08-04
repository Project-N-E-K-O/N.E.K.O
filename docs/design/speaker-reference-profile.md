# Phase 4C-C：仅内存 Speaker Reference / Profile

## 目标

本阶段只建立 provider-neutral 的模型身份、说话人参考向量和 Profile 生命周期。
它不进行 Enrollment 计算，不加载具体模型，不持久化数据，也不接入 ASR、Core、
API 或 UI。

## 依赖方向

```text
voice_identity.profile
  -> voice_identity.reference
      -> voice_identity.contracts
```

`contracts.py` 只包含不可变的 `SpeakerModelIdentity`；`reference.py` 是本阶段
唯一允许导入 NumPy 的领域模块。包初始化文件只保留 docstring，因此
`import main_logic.voice_identity` 不会触发 NumPy、模型或运行时导入。

领域模块不得依赖 `asr_client`、Core、Voice Turn、router、app、ONNX Runtime、
keyring 或 cryptography。ASR、Core 和 Voice Turn 可以作为外层消费者依赖本包；
本包不得反向依赖这些运行时层。

`scripts/check_core_contracts.py` 是针对可静态解析 import/call 的结构性工程门禁，
用于阻止意外依赖和越层调用；它不承担 Python 运行时隔离，也不枚举反射或刻意混淆
写法。仓库内代码仍以可信提交和代码审查作为边界。

## 数据合同

`SpeakerModelIdentity` 由调用方提供非空 model ID、非空 model revision 和正整数
embedding dimension。它不持有模型实例、资产路径或下载能力。

`SpeakerReference` 在构造时创建独占、C-contiguous 的一维 `float32` 副本，
验证维度、finite 和非零范数后执行 L2 normalization。调用方随后修改原数组不会
影响内部状态。内部 embedding backing storage 没有公开属性、JSON 表示或普通读取
接口。

需要消费向量的可信内部 adapter 只能调用 `copy_embedding()`，取得独立且由调用方
拥有的副本。调用方负责该副本的生命周期，并在使用后通过 `fill(0.0)` 尽力清零；
修改或清零导出副本不会影响 Reference 内部状态。该能力不是面向产品、网络或插件的
raw embedding 接口。

本合同以仓库内第一方调用者可信为前提，不防御恶意进程内代码、插件或 native
extension。任何能够持有 Reference 的恶意代码本就可以复制、持久化或发送数据；
需要隔离不可信代码时必须使用独立进程。`clone()` 创建独立所有权，关闭任一实例不会
影响其他 clone。

`SpeakerProfile` 接收调用方分配的非空 opaque generation，不解释顺序，也不分配、
回退或持久化 generation。构造时它 clone 输入 Reference，从而拥有独立副本；
外部只能取得新的 Reference clone，不能访问 Profile 内部实例。

## 生命周期与隐私

Reference 与 Profile 都提供幂等 `close()`。Reference 关闭时清零内部 embedding；
Profile 关闭时级联关闭其内部 Reference。关闭后除 `closed` 和安全 `repr` 外的使用
操作都会失败。调用方必须显式调用 `close()`；合同不依赖析构器或 GC 执行清理。
异常和 repr 不包含 embedding。

显式清零是 best-effort：Python、NumPy 或底层分配器仍可能保留不可寻址的历史
副本，因此本合同不宣称提供进程内的密码学擦除保证，也不构成 Python 沙箱。

## 明确不包含

- Enrollment service/session、TTL、PCM 或 verification threshold；
- CAM++ adapter、模型资产、产品/模型工作线程或子进程；
- ProfileStore、keyring、AES-GCM、文件或数据库；
- registry、service locator、manager fan-out activation；
- Core composition、麦克风 gate、runtime hot swap；
- raw embedding/similarity 产品接口、API、router 或 UI。

这些能力必须按 Enrollment compute、CAM++ worker、持久化、app-owned service、
session-start composition 和 UI 的顺序分别评审与交付。
