# TTS 声音来源统一架构（voice-source unification）

> 状态：草案，待 review。本文是 PR #1818（特异 TTS provider 注册表）的演进目标——
> #1818 已落地的 `tts_provider_registry` 是这套架构的种子，本设计把它扩成完整的
> provider 注册表，并把"声音来源"提升为一等维度。

### 当前代码放置约定

Voice Clone 和 Voice Design 都属于可复用音色的**注册阶段**，provider
差异统一放在 `main_logic/voice_registration/providers/`：

```text
main_logic/voice_registration/providers/
├── base.py          # provider-neutral primitives
├── cosyvoice.py     # CosyVoice Clone + Design
├── minimax.py       # MiniMax CN/Intl Clone + Design
├── elevenlabs.py    # ElevenLabs Clone + two-stage Design
└── mimo.py          # MiMo Clone + Design
```

- `main_routers/characters_router/voice_cloning.py` 只承载 HTTP 请求编排；
  provider 的 endpoint、payload、响应解析和上游异常不得放进 Router。
- `main_logic/tts_client/workers/` 只负责注册完成后的运行时语音合成。
- 历史 `utils/voice_clone.py` 已移除；新代码直接 import 对应 provider adapter，
  不再为领域 client 提供 Utils 入口。
- 四个 hosted provider 必须保持模块对偶。上游不提供远端 `voice_id` 等能力时，
  差异写在该 provider 模块中并附官方 API 依据，不得靠散落的跨模块分支模拟对偶。

## 1. 问题

当前 `voice_id` 一个字段被迫编码两个正交的东西，再加上第三个维度完全缺失：

- **声音身份**：到底是哪个音色（`冰糖` / `default` / 某个克隆 id）。
- **后端路由**：用哪个 TTS provider、哪个 key、哪个 endpoint——靠**前缀**偷偷编码：
  - `gsv:<id>`（GPT-SoVITS，见 `config/__init__.py:34`）
  - `eleven:<id>`（ElevenLabs）
  - `__gptsovits_disabled__|<url>|<voice_id>` 这种占位符（`utils/gptsovits_config.py:24`，前端 `static/js/api_key_settings.js:2197` 写入）——把"被禁用的 gsv 配置"冻结进 voice_id，是最丑的一处。
- **声音来源（preset / clone / design）**：**当前没有这个分层**。预制走 `native_voice_registry`，克隆走 `voice_storage.json` + `voice_meta.provider`，design 还不存在。三者散落、无统一模型。

后果：加一个 provider 要改 8+ 处（#1818 的动机）；`get_tts_worker` 是一长串靠
`core_api_type` / `has_custom_voice` / `voice_meta.provider` / `ttsModelProvider` /
`GPTSOVITS_ENABLED` / 前缀嗅探拼出来的 if-else；mimo 被错挂进 native registry 只为蹭它的音色目录与 UI 管线。

## 2. 目标：两条正交维度 + 一个 provider 注册表

把纠缠的东西拆成**两条正交维度**，各自显式：

- **维度 A — TTS provider/backend**：谁来合成。
- **维度 B — 声音来源（source）**：`preset`（官方预制）/ `clone`（用户克隆）/ `design`（文字描述生成，未来）。

一个角色的声音配置变成显式结构（不再靠 voice_id 前缀推断）：

```jsonc
voice = {
  "source":   "preset" | "clone" | "design",
  "provider": "gemini" | "vllm_omni" | "gptsovits" | "cosyvoice" | "elevenlabs" | "minimax" | "mimo" | ...,
  "ref":      "<在该 provider 内的音色标识>",   // preset 名 / clone id / design id
  "config":   { "url": "...", "model": "...", "api_key_ref": "..." }  // 仅 local/hosted 自配 provider 需要
}
```

`provider` 独立成字段 → **彻底干掉 `gsv:` / `eleven:` / `__gptsovits_disabled__|` 前缀打包**。
`source` 独立成字段 → worker 知道把 `ref` 当预制名、克隆 id 还是 design spec 解释。

## 3. 统一 provider 注册表

把现有三个分裂的东西（`native_voice_registry` / `tts_provider_registry` / 内联 clone 分支）收敛成**一个** provider 注册表。每个 provider 声明自己的特征：

```python
@dataclass(frozen=True)
class TTSProvider:
    key: str
    kind: Literal["native", "local", "hosted"]   # 见 §4
    worker: Callable                              # 合成 worker
    supported_sources: frozenset[Literal["preset", "clone", "design"]]
    # 预制目录：静态 catalog（gemini/mimo）或动态拉取（gptsovits /api/v3/voices）
    preset_catalog: CatalogSpec | None
    clone: CloneCapability | None                 # 如何 enroll（仅支持 clone 的）
    design: DesignCapability | None               # 如何 design（未来；elevenlabs 先行）
    selection: Literal["core_tied", "dropdown"]   # native 跟核心；其余走 ttsModelProvider 下拉
    config_fields: ConfigSchema                   # 需要的 url/model/key 字段（local/hosted）
    probe: ProbeSpec                              # 连通性探测（沿用 #1818 的 probe_kind/sub_type/ws_path）
    auth: AuthSpec                                # api key 来源（core_api_key / tts slot / 专属桶）
    is_selected(ctx) -> bool                      # 选中判定，ctx 带 core_config+cm+voice 配置
    resolve(ctx) -> (worker, api_key, key)        # 分派结果
```

**dispatch 收敛成**：读角色 voice 配置 → 拿 `provider` → 注册表查 provider → `resolve()` 出 worker+auth；`source` 交给 worker 决定怎么用 `ref`。`get_tts_worker` 那串 if-else 退化成"按 provider 查表 + 少量优先级"。

### 3.1 两种选中机制 → 统一 DispatchContext（实现期发现）

provider 的"选中"并非单一机制，实现时必须统一：

- **配置选中**（vllm_omni / gptsovits / 未来 mimo-下拉）：靠 `ttsModelProvider` 下拉 / 开关，只需 `core_config` + `cm` 就能判定。
- **音色元数据选中**（minimax / elevenlabs / cosyvoice 克隆）：靠用户所选**克隆音色的 `voice_meta.provider`**（`_get_voice_meta(voice_id)`），需要 `voice_id` / `has_custom_voice` / `voice_meta`，外加 cosyvoice_intl 的 key 兜底、grok xAI 自定义 voice 的 interplay 等既有细节。

因此 `is_selected` / `resolve` 的入参从 `(core_config, cm)` 泛化成一个 **`DispatchContext`**（`core_config, cm, voice_id, has_custom_voice, voice_meta`）。配置选中的 provider 忽略多出的字段；音色选中的 provider 读 `voice_meta`。`get_tts_worker` 顶部构建一次 context，注册表按 priority 逐个 `is_selected(ctx)`。**务必保留**的既有行为：cosyvoice_intl key 缺失 → dummy（避免拿国内 key 打 intl 端点 401）、grok voice_meta=None 的 xAI 自定义 voice 短路、free preset voice 跳过 clone。

## 4. 修正后的 taxonomy（kind）

| kind | 含义 | 成员 | 选中 | 支持的 source |
|------|------|------|------|--------------|
| **native** | TTS 随核心 LLM 白送 | gemini / step / grok / free(+free_intl) | 跟 `core_api_type` | preset |
| **local** | 自部署 endpoint（你自己跑） | **vllm_omni、gptsovits** | `ttsModelProvider` 下拉 | preset + clone（gsv 参考音频） |
| **hosted** | SaaS，自带音色体系 | **cosyvoice、minimax、elevenlabs、mimo** | `ttsModelProvider` 下拉 | preset + clone（+ elevenlabs 未来 design） |

修正点（相对 #1818 当前状态）：
- **mimo 从 native 摘出 → 归 hosted**（它是 SaaS、有自己的音色体系，不是核心自带；当前错挂 native registry 只为蹭目录）。
- **gptsovits 从 `GPTSOVITS_ENABLED` 开关迁到 `ttsModelProvider` 下拉**，与 vllm 同机制；并**放宽 `is_local_http_url` 允许远程**。
- `local` 不含 mimo 后，名实相符（vllm 默认本地、gsv 本地，远程也是你自部署的盒子）。

## 5. 前端：来源优先（source-first）的选声器

前端从注册表元数据（`/api_providers → tts_providers`，#1818 已开）派生 UI：

1. 选 provider（native 由核心隐含）。
2. 按该 provider 的 `supported_sources` 渲染来源切换：
   - **preset**：拉该 provider 的预制目录（静态 catalog 或动态 `/voices`）。
   - **clone**：上传/录制样本 → enroll → 得 clone id（现有 voice_clone.js 流程归一到这里）。
   - **design**：文字描述 → 生成 → 得 design id（未来；先接 elevenlabs voice design）。
3. 只渲染该 provider 真支持的来源，不再用前缀区分。

## 6. 迁移与向后兼容（关键风险）

存量角色的 `voice_id` 里有 `gsv:` / `eleven:` 前缀、`__gptsovits_disabled__|` 占位符，`voice_storage.json` 已按 provider 分桶。迁移要点：

- **读路径兼容**：保留一个一次性 normalizer，把老的前缀化 voice_id 解析成新的 `{source, provider, ref}`（前缀 → provider，去前缀 → ref，clone 桶 → source=clone）。挂在 config 读取/cleanup 这个 choke point（参考 `cleanup_invalid_voice_ids` 的覆盖思路）。
- **写路径**：新结构落地后，保存即写新模型；老占位符 `__gptsovits_disabled__|` 退役（gsv 禁用＝provider 没被选中，是普通的"未选"状态，不需要把配置冻进 voice_id）。
- `voice_storage.json` 的 provider 分桶基本可直接映射成 `provider` + `source=clone`。

## 7. voice design 怎么接（核心前瞻）

design 只是 provider 声明的**第三种 source**，不需要新管线：

- provider 在 `supported_sources` 里加 `"design"` + 填 `DesignCapability`（描述 → 调 provider 的 voice-design API → 落一个 design id，之后当作一个可复用音色）。
- 前端 source-first 选声器自动多出 "design" tab（仅对 declare 了 design 的 provider 显示）。
- 第一个落地候选：ElevenLabs voice design。其余 provider 不支持就不显示，零侵入。

这正是为什么要先把 source 提升为一等维度——否则 design 又会被塞进 voice_id 变成第 4 种前缀。

## 8. 与 PR #1818 的关系 / 实施

- #1818 的 `tts_provider_registry`（vllm+gptsovits）是本架构 `local` 部分的种子，**演进**成 §3 的统一 `TTSProvider` 注册表即可，不浪费。
- 按"必须一步完成"：在 #1818 同分支上扩成完整架构（rename、gptsovits 迁下拉、mimo 归 hosted、clone 并入、source 维度、前端选声器、迁移 normalizer），一个 PR 收口。
- 内部实施顺序（同一 PR 内增量验证）：数据模型 + 注册表骨架 → dispatch 收敛（保 monkeypatch/pickling）→ 迁移 normalizer + 向后兼容测试 → 前端 source-first 选声器 → gsv 远程放宽 + 占位符退役 → 全量回归。

## 9. 已拍板决策

- **存储 schema**：✅ 单字段结构化对象——characters.json 里 voice 存成 `{source, provider, ref, config}` 对象（不平铺）。
- **落地方式**：✅ #1818 同分支扩成完整架构，一个 PR 收口；`tts_provider_registry` 演进成统一 provider 注册表，已有 commit 当种子不浪费。
- **ref 命名空间**：默认 `(provider, ref)` 复合键——不强制全局唯一，ref 只在所属 provider 内唯一（克隆 id 本就是 provider scoped）。如 review 有异议再调。
- **design 首个 provider**：默认锁定 ElevenLabs voice design；其余 provider 不 declare design 即不显示。
