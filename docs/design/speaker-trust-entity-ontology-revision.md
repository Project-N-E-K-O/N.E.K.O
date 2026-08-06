# §2 实体本体（修订版）—— 替换原 §2，并增补 §2.10–§2.14

> 本次修订回应维护者的新要求：「可以拆，但需要有一个本体能 link 跨渠道的同一实体，且 qq 这里可以自动链接。」
> 文中所有代码位置均在 worktree `trust-identity-neutral`（`origin/main`）重新核实过。**凡标注「已核实」的都是我本次亲自读代码/跑代码确认的；凡属推断或未验证的，逐条显式标注。**

---

## 2.0 对维护者要求的直接回答

**一句话结论：QQ 两条通道的自动链接不可行；但「拆」这件事在数据上其实已经生效了，维护者真正缺的是本体和碰撞探测，这两样本设计都给。**

拆成三条分别回答。

### (1) 「qq 这里可以自动链接」—— 不可行，且我不设计替代启发式

三条独立封堵，逐条回代码复核成立：

| # | 封堵 | 证据 |
|---|---|---|
| A | **无共现观测** | `__init__.py:189-201` `_make_qq_connection()` 按单值 `qq_connection_mode`（`config_store.py:79`，已核实逐字为 `"qq_connection_mode": "napcat",  # "napcat" \| "open_platform"`）二选一 return **一个**连接对象；`runtime_ops_service.py:42-48` 启动时把类型不匹配的旧 client `disconnect()` 后重建。两条通道永不同时活跃 ⇒ 同一真人的 uin 与 openid 不可能在同一时刻被观测到 |
| B | **无目录可 join** | `qq_open_plat.py:73-77` `get_friend_list` / `get_group_list` 硬编码 `return []`；`display_name_service.py:18-19` 把这写成「该通道的**预期形态**」而非 TODO。该通道全部出网调用只有 6 处（`:483` token、`:556` gateway、`:324`/`:381` messages、`:521` files、`:534` PUT），**零个用户查询端点**。NapCat 侧实际调用的 OneBot action 只有 7 个，无一返回 openid |
| C | **凭据形态封死反查** | `qq_open_plat.py:481-496` 用 appId+clientSecret 换的是 **app 级 token**，全文无 authorization_code / redirect_uri / scope / 用户同意环节。openid 的设计目的就是让应用拿不到 uin。仓库自己的产品文案已认账：`i18n/zh-CN.json:461`「ID 为加密 openid（非 QQ 号）」（7 语种一致） |

**判据的正确性标尺**：本仓库唯一一次成功的自动跨标识链接是遥测侧的 `device_alias_edges`（`local_server/telemetry_server/storage.py:1108-1116`），它成立的**全部**依据是客户端在**同一条已认证 payload 里同时上报** `device_id` + `device_id_legacy`（`utils/token_tracker/reporting.py:470`）。**没有共现就没有边。** QQ 侧封堵 A 说明共现在结构上不存在。

**唯一在两侧都存在的同类字段是显示名**，而它在代码里的取值口径本身就不一致：`qq_client.py:455` 先 `nickname` 后 `card`，`message_dispatcher.py:64` 先 `card` 后 `nickname`（**优先级相反**）；`card` 还是按群作用域；`qq_open_plat.py:582` 的 `author.username` 在本仓无任何证据会被填充。基于它自动合并 = 把两个人焊成一个 entity = 账本双份 = 评审认定的致命类。**我不设计这个启发式，也不设计它的任何变体**（时序相邻、编辑距离、bootstrap 提权、首见配对）。

### (2) 「可以拆」—— 在非碰撞情形下**今天已经是拆开的**

这是本次修订最重要的一条发现，它改变了整个问题的形状。

已核实：trust 池按**裸 actor 字符串**分区（`permission.py:323` `self._speaker_trust_profiles.setdefault(qq, ...)`，`qq` 来自 `:316` 的 `partition(":")`）。而 napcat 给的是裸 uin、开放平台给的是 openid，**这是两个不同的字符串** ⇒ 已经是两条独立账本、两个独立 `account_id`（`qq:<uin>` vs `qq:<openid>`）、彼此不共享任何 trust。

> **所以「拆」的实际增量只有一件事：防住 uin 与 openid 的字符串碰撞。** 除此之外，把 platform 段改成 `qq.napcat` / `qq.open` 不会让任何一对本来就不同的账号变得「更不同」。

而碰撞概率是**未知量**（openid 字符集在仓内无证据：无 fixture、无校验、无注释；`qq_open_plat.py:610` 的 `<@!(\d+)>` 假设纯数字，但 `_convert_event` 全仓**零测试覆盖**，属抄自 napcat 的未验证代码，不构成证据）。

**并且我发现了字节级拆分的一条不可逆代价，两份前置方案的作者都不知道（见 §2.10.1）：改变 `speaker_id` 字节会让同一个真人的历史 fact 行被永久打成 `speaker_provenance_mixed`，销毁该行的 speaker_id / speaker_label / speaker_trust，且该行从此永久退出 trust 信号。这不是账本问题，是记忆语料损坏，`unbind` 与任何链接机制都救不了。**

⇒ **本设计：`account_id` 一个字节不改，channel 落成「观测属性」而非「键」，碰撞变成运行时可观测事件。字节级拆分保留为条件升级路径（§2.12），触发条件是探测器真的响了。**

### (3) 「需要一个本体能 link 跨渠道的同一实体」—— 这是真正的交付物，且完全可行

`entity ← account` 两层本体（§2.1）+ `bind`/`unbind`/`merge`/`forget`（§2.9）完整支持跨渠道链接，且对 `qq:<uin>` ↔ `qq:<openid>` 这一对**开箱即用**（它们本来就是两个不同的 account_id）。

**「首次建边」必须由人做一次；建边之后的一切都是自动的**——传播、归并、跨渠道 `trust_inputs` 求和、`same_entity` 自证禁令、未来第三个平台接入时的连通分量收敛，一律不需要人再介入。

### 字面要求 vs 意图

| | 内容 | 本设计 |
|---|---|---|
| **字面要求** | 系统自己判断出「napcat 的 123456 和开放平台的 ABCD1234 是同一个人」 | **做不到**，见 (1) |
| **意图（我的读解）** | 换通道不能让所有人白干 / 两条通道的账不能串 | **可以拿到**，见下 |

意图拆成四层，前三层全自动、零用户动作、结构上不可能错合：

1. **两条通道的账本本来就不串**（非碰撞情形），且本设计新增探测器让这一点**可证**而非**假设**。
2. **存量账本零损失**：`account_id` 字节不变 ⇒ 三处 SHA256 烘焙输入全不变 ⇒ 存量 `processed_signal_events` 逐条命中（§2.10.2 有逐处核实）。
3. **建边之后全自动**：union-find 收敛、账本聚合、自证禁令全部自动。
4. **首次建边**：一次人工动作。挂在用户切换通道后**本来就必须做**的那次 dashboard 编辑上（§2.9.4），零额外步骤——但**这不叫自动，我如实标注**。

---

## 2.1 两层：`entity`（人） ← `account`（平台账号）

不变（原 §2.1 的骨架全部保留）。本次修订只增加一句话的定位澄清：

> **`account` = 一个平台上的一个稳定标识符。** 「同一平台的不同接入通道」（QQ 的 napcat / 开放平台）在本体上**不是新的一层**——它们产出的是**不同的 actor 字符串**，因而本来就是**不同的 account**。通道信息以观测属性的形式记录在 account 记录上（§2.3），不参与任何键的构成。

---

## 2.2 account_id 的形状：**一个字节不改**（含逐条兼容性核对）

**`account_id` 完全沿用 `stable_speaker_id`（`memory/speaker_trust.py:173-185`），形状 `platform:actor`，QQ 两条通道一律 `qq:<actor>`。**

### 2.2.1 与 `stable_speaker_id` 正则的逐条核对

已核实 `speaker_trust.py:173-185` 的实际约束：

```python
text = str(value or "").strip()
if not text or len(text) > 96 or any(ch.isspace() or not ch.isprintable() for ch in text): return None
platform, sep, actor = text.partition(":")
if not sep or not platform or not actor: return None
if not re.fullmatch(r"[A-Za-z0-9_.-]+", platform): return None
if not re.fullmatch(r"[A-Za-z0-9_.:@-]+", actor): return None
return f"{platform.lower()}:{actor}"
```

| 校验项 | 本设计（`qq:123456` / `qq:ABCD1234`） | 结论 |
|---|---|---|
| 非空 / 无空白 / 可打印 | 与今天逐字相同 | **不变** |
| 总长 ≤96 | platform 段仍是 `qq`（2 字符）⇒ actor 预算仍是 **93** | **不变**（对照：`qq.open:` ⇒ 88，`qq.napcat:` ⇒ 86，见 §2.12） |
| `partition(":")` 三段非空 | 不变 | **不变** |
| platform `[A-Za-z0-9_.-]+` | `qq` 通过 | **不变** |
| actor `[A-Za-z0-9_.:@-]+` | 与今天完全同一条路径 | **不变**（openid 字符集风险**既不新增也不消除**，见 §2.14 R11） |
| `platform.lower()` 幂等 | 不变 | **不变** |

> **关键推论：本设计对 `stable_speaker_id` 的输入分布零改变，因此不存在任何「过去能过、现在过不了」或反之的情形。** 这条必须配一条回归护栏测试（§2.13 T7），防止将来有人「顺手」把 channel 拼进 `speaker_id`。

### 2.2.2 与 `memory/scopes.py` 三段校验的逐条核对

已核实 `scopes.py:88-96`：

```python
if kind == SUBJECT_GROUP_PARTICIPANT:
    components = subject_id.split(':')
    if len(components) != 3 or any(not component for component in components):
        raise MemoryScopeError("group_participant subject_id must be platform:group:speaker")
```

| 项 | 核对 |
|---|---|
| group_participant 恰好 3 段 | `qq:<group>:<speaker>` 仍是 3 段 —— **不变** |
| `_encode_component`（`scopes.py:60-71`） | 只转义 `%` 与 `:`，本设计不引入任何新字符 —— **不变** |
| `facts.py:1304` 的 `rsplit(':', 1)[0]` 同群前缀判定 | 前缀仍是 `qq:<group>` —— **不变** |
| `persona_section_key`（`scopes.py:156-157`）= `scoped_persona:{kind}:{subject_id}` | 不变 —— **全部存量 persona section 不变孤儿** |
| 默认 scope（`scopes.py:114-115`）`f"{kind}:{subject_id}"` | 不变 |

**`memory_bridge.py:49-73` 的三个 subject builder 一行不改。** 原文档 §7 PR4 那条「三个 subject builder 改走 `speaker_account_id()`」的 bullet **必须删除**——两份前置方案与两位评审在这一点上完全一致，我复核后确认：改 subject_id 会让 `entry_matches_subject` 全 False，全部存量 scoped 记忆与 persona section 变成不可达孤儿。

### 2.2.3 `channel` 的形状

```python
# memory/identity.py
_CHANNEL_RE = re.compile(r"\A[a-z0-9_]{1,16}\Z")     # ← 必须锚定，理由见下

def normalize_channel(value) -> str | None:
    """观测属性的归一。None / 空 / 非法 ⇒ None（= 通道未知），绝不抛。"""
    text = str(value or "").strip().lower()
    return text if _CHANNEL_RE.fullmatch(text) else None
```

QQ 的两个取值：`"napcat"` / `"open"`。其余平台不发该字段 ⇒ `None`。存量记录无该字段 ⇒ `None`（通道未知）。

> **`None` 是一等公民，不是缺省值的占位。** 「通道未知」是存量数据的**真实状态**（`permission.py:176-221` 的 profile 结构只有 `adjustment` / `message_count` / 两个环，**零 provenance 字段**——已核实），不能被伪造成任何具体通道。原方案用「空串 = napcat」的做法在这里被否决：它把一个未知量硬编码成了一个断言。

### 2.2.4 【必须修的既有 bug】wire 正则未锚定 —— 影响本设计**与原文档 §4.1**

**已实测**（`pydantic==2.11.7`，仓内 `requirements.txt:369`；实测环境 2.11.5，行为一致）：Pydantic v2 的 `Field(pattern=...)` 是**未锚定的 search 语义**，不是 `fullmatch`。

```
pattern=r'[a-z0-9_]*'  ⇒  PASS: 'OPEN', 'q#q', 'a/b', '中文'      ← 等于零校验
```

**这条同时命中原文档 §4.1 已经写死的 `ActivityEvent` 模型**，实测结果：

```python
class A(BaseModel):
    id: str = Field(min_length=8, max_length=96, pattern=r"[A-Za-z0-9_.:-]+")

A(id='participant:猫娘 A:12:34:56')   # ← PASS（！）
A(id='x\nyyyyyyy')                    # ← PASS（换行符也过）
```

⇒ 原文档 §4.1 那句「`pattern` 不含 `|`、不含空白、限长 96 —— 这直接堵掉 minimal-seam 评审第 3 条指出的坑」**是错的**，该坑今天并没有被堵上；§8.5 测试 #5 只验证了「插件发的是哈希值」，没有验证「服务端会拒绝非哈希值」，所以这个护栏是空的。

**修法（两处一起改，PR1/PR2）。⚠️ 锚点语法有坑，已实测确认：**

`Field(pattern=...)` 由 pydantic-core 用 **Rust `regex` crate** 编译，不是 Python `re`。
它**不认识 `\Z`**（大写），写了会在**模型定义时**直接抛 `SchemaError: regex parse error:
unrecognized escape sequence` —— 即上面初稿给的 `\A...\Z` 根本编译不过。

实测矩阵（`.venv` 内 `pydantic 2.11.7`）：

| pattern | `'participant:猫娘 A:12:34:56'` | `'x\nyyyyyyy'` | `'good.id\n'` | `'ok.id:12345678'` |
|---|---|---|---|---|
| `[A-Za-z0-9_.:-]+`（今天） | PASS | PASS | PASS | PASS ← **等于零校验** |
| `\A[A-Za-z0-9_.:-]+\Z` | 编译失败 | 编译失败 | 编译失败 | 编译失败 |
| `\A[A-Za-z0-9_.:-]+\z` | REJECT | REJECT | REJECT | PASS ← **正确** |
| `^[A-Za-z0-9_.:-]+$` | REJECT | REJECT | REJECT | PASS ← **同样正确** |

`^...$` 在这里也安全：Rust regex 默认非 multiline，且 `$` **不**像 Python 那样容忍结尾换行
（见表中 `'good.id\n'` 一列）。两种写法二选一，全仓统一即可。

```python
speaker_channel: str | None = Field(default=None, pattern=r"\A[a-z0-9_]{1,16}\z")
id: str = Field(min_length=8, max_length=96, pattern=r"\A[A-Za-z0-9_.:-]+\z")   # ← 既有模型的修正
```

注意 `memory/identity.py` 里的 `_CHANNEL_RE`（§2.2.3）走的是 Python `re`，
那边 `\A...\Z` 合法且配 `fullmatch` 是对的 —— **两处不能照抄同一个字符串**，
这正是本坑最容易复发的地方。守卫测试要覆盖「wire 层与服务端层对同一批值给出一致判定」。

配一条 property 测试：任何含空白 / 换行 / CJK / `#` 的值必须 422。

---

## 2.3 `channel`：**观测属性，不是键**（三条论证）

这是本次修订的核心结构决定，也是与两份前置方案分道扬镳的地方。两份方案都试图把 channel 做成键（复合键 `qq:123#open`，或字节级 `qq.open:123`），**两条路我都验证为不成立**。

### 论证 1：signal 轴在数据上**无法**做通道归属（这条决定性）

已核实 `memory/facts.py`：

- `:1365` `target_id = stable_speaker_id(prior.get('speaker_id'))` —— target 取自**历史 fact 行**
- `:1409` 事件体 `'speaker_id': target_id`
- 设计 §3.3 的 `_apply_trust_mutations_locked` 按 `ev["speaker_id"]` 路由到目标账本

**而 fact 行上永远不带 channel**（这正是「speaker_id 一个字节不改」的直接推论；即便改了，历史行也不会被回填）。

⇒ 「target 的 channel = 本请求的 channel」是一条**无依据的推断**，不是记录下来的事实。owner 现在纠正的可能是三个月前、上一个通道时代写下的一句话。

前置方案 A 用一句「调用点用本请求的 channel 解释 source 与 target 两侧」带过了这一条。**这句话在 target 侧是错的。** 而且它今天恰好不出事的唯一原因是 `_in_signal_scope`（`facts.py:1292-1321`，已核实）会让跨通道时代的老 fact 行因 subject_id 全变而结构性掉出信号域——**把安全性挂在一条方案自己都不知道自己依赖的不变量上**，而 PR7 的 entity 级 union 正朝着放宽它的方向走。

> 有人会提议「按 fact 行时间戳 + 通道切换历史时间线推断 target 的 channel」。**这与昵称启发式是同一类错误**：用一个不可验证的推断去路由账本条目。而且切换历史在全部存量部署上从空开始（`settings_service.py:830-832` 的切换只改一个字符串 + 打一条日志，**不留可查历史**——已核实），t=0 时该推断没有任何输入。**否决。**

### 论证 2：activity 轴单独通道化，收益接近零

activity 轴的 channel 确实在 ingest 时刻可知（连接器打戳）。但 activity 的全部影响被 `SPEAKER_TRUST_ACTIVITY_MAX_BONUS = 0.02` 封顶（已核实 `config/memory_settings.py:261`），相对 `SPEAKER_TRUST_ARBITRATION_MARGIN = 0.15` 与 `SPEAKER_TRUST_ADJUSTMENT_LIMIT = 0.30` 可忽略。

⇒ 「signal 不能通道化 + activity 通道化没意义」⇒ **通道不能成为账本分区键。**

### 论证 3：把 channel 做成键会引入新的不可逆路径

前置方案 A 的复合键需要配套「存量记录归属哪个通道」的判定，而存量 profile **零 provenance**（已核实 `permission.py:176-221`）。两位评审各自独立构造出了可达的错合场景：

- 打标表把 NapCat 时代的数字 key 打成 `open` ⇒ 同 actor 的开放平台用户**直接继承**（不是 orphan）；
- `_adopt_legacy_locked` 的「主防线」门禁读的是**本次新增、无回填路径**的 `channel_observations`，在全部存量部署上线时刻恒为空 ⇒ 收养门禁平凡通过 ⇒ 陌生人整体继承他人账本；且收养是「改键」而非「新建」，碰撞探测器**结构性看不见**这条路径；
- 幂等哨兵 `legacy_import` 挂在 account 记录上，而键变成了随 `deployment_mode` 变化的**可变量** ⇒ 切模式重启即二次导入 ⇒ 在「一次点击绑定」处兑现为双算（-0.08 → -0.16，越过 margin 0.15）。

**这三条我逐条回代码复核，全部成立。** 它们不是实现瑕疵，是「用未知量当键」的必然推论。

### 结论：channel 的定位

```
channel 是「某次观测经由哪条通道到达」的记录，
不是「这个账号属于哪条通道」的断言。
```

它的**唯一用途**是：

| 用途 | 说明 |
|---|---|
| **碰撞探测** | 同一 `account_id` 被两条通道观测到 ⇒ loud warning + 响应回传 + 计数（§2.11 D3） |
| **运维诊断** | `GET /internal/trust/profile` 暴露 `channels_observed`，人工 bind 时给操作者看 |
| **拍板依据** | 探测器为零 ⇒ 字节级拆分无收益；非零 ⇒ 触发 §2.12 的条件升级 |

**它绝不参与**：任何键的构成、任何账本的分区、任何自动 bind/merge 的判据、任何权限判定。

---

## 2.4 entity_id：确定性派生，无分配器

**原 §2.2 全部保留，一字不改。** 由于本设计不引入 account_key，前置方案 A 提出的「`derive_entity_id` 必须从 account_key 派生」这条修正**自动失效**（它是复合键方案的必要补丁，本设计没有复合键）。

`derive_entity_id(account_id, generation)` 保持原样。命名空间不相交的证明（entity_id 无冒号 / account_id 必含冒号）原样成立。

> 保留原方案那条洞察的**教训**（写进 `memory/identity.py` 模块 docstring）：**确定性派生的前提是「派生输入 = 身份的唯一真相」。** 任何时候有人想给 `derive_entity_id` 加第二个身份维度，必须先回答「两个在该维度上不同的账号，会不会被派生到同一个 entity_id」——若会，则确定性派生会**主动制造**错合。

---

## 2.5 存储形状（`<memory_dir>/speaker_trust.json`）

相对原 §2.3 的增量用 `// ★` 标出。

```jsonc
{
  "version": 2,                                   // ★ 1 → 2（新增 channel 观测容器）
  "updated_at": "2026-08-04T11:32:11+00:00",

  "legacy_barriers": { "qq": { /* 不变，见 §5.1 */ } },

  "account_index": { "qq:123456": "ent_3f9a1c8b2d4e6f0a1b2c3d4e" },   // 键仍是 account_id

  // ★ 新增：通道观测台账。纯诊断，绝不参与任何判定
  "channel_observations": {
    "qq": {
      "napcat": {"first_seen": "…", "last_seen": "…", "accounts": 812},
      "open":   {"first_seen": "…", "last_seen": "…", "accounts": 3}
    }
  },
  // ★ 新增：碰撞台账。account_id → 观测到的通道集合
  "channel_collisions": {
    "qq:123456": {"channels": ["napcat", "open"], "detected_at": "…", "hits": 4}
  },

  "entities": {
    "ent_3f9a1c8b2d4e6f0a1b2c3d4e": {
      "entity_id": "ent_3f9a1c8b2d4e6f0a1b2c3d4e",
      "status": "active",
      "created_at": "...", "updated_at": "...",
      "accounts": {
        "qq:123456": {                            // ← 键仍是 account_id，**不带任何后缀**
          "account_id": "qq:123456",
          "generation": 0,
          "bound_at": "...",
          "adjustment": -0.08,
          "message_count": 17,
          "processed_activity_events": ["activity_ab12…"],
          "processed_signal_events": ["7c9d…"],
          "legacy_import": {"source": "…", "at": "…"},
          // ★ 新增：观测属性。绝不参与键、绝不参与判定
          "channels_seen": {"napcat": {"first": "…", "last": "…", "count": 1204}}
        }
      }
    },
    "ent_oldoldoldoldoldoldoldol": {
      "status": "merged", "merged_into": "ent_3f9a…", "merged_at": "…", "accounts": {}
    }
  },

  "forgotten": { "ent_9aa0…": {"forgotten_at": "…", "accounts": ["qq:999888"]} }
}
```

**`_copy_on_write` 的容器清单（原 §3.5）必须补三项**：`channel_observations`（及被改动的 platform 子 dict）、`channel_collisions`、account 记录的 `channels_seen`。

**`version: 1 → 2` 的迁移是恒等映射**：v1 池读入后补两个空容器即可，`account_index` 与全部 account 键**逐字节不变**。

---

## 2.6 关键结构决定：账本按 account 分区，不按 entity 汇总

**原 §2.4 全部保留，一字不改。** 本次修订加固两点：

1. `_merge_entities_locked` 里的「`acc_id` 不可能已在 survivor 里」必须是 `assert`（原文已要求）。在本设计下**不相交性是结构保证的**：`account_index` 是一个函数，而 account 的键是 `account_id`（不可变、由 `stable_speaker_id` 唯一归一），**不存在任何会让同一账号产生两个键的路径**——这正是拒绝复合键换来的性质。
2. `_normalize_pool` 检出同一 account 出现在两个 entity 下时的合并规则（`adjustment` 相加 / `message_count` 取 `min(cap, 和)` / 两环 `dedup_keep_order` 并集 + warning）保留，用于承接手改脏数据。

---

## 2.7 base / owner 的归属

原 §2.5 表格保留。**三条加固：**

| 量 | 归属 | 本次修订 |
|---|---|---|
| `base`（权限档派生） | account × 请求局部 | 不变 |
| `adjustment` / `activity` | entity 全局 | 不变 |
| `speaker_is_owner` | **account 局部**授权位，绝不由 entity 推导 | **加固**：也绝不由 channel 推导，见下 |
| 自证禁令判定 | entity 级（PR7 起） | 不变 |

**【明确否决】不把 bootstrap 提权改成通道感知。**

前置方案 A 把这条包装成「顺带修一个现存 bug」。我复核后判定它是**权限提升 fail-open**，必须否决：

- `message_dispatcher.py:44` 的 `if permission_mgr.list_users(): return` 是**全局判空**。
- 改成按当前通道过滤后，从 NapCat 切到开放平台时过滤结果**恒空** ⇒ 切换后**第一个私聊 bot 的陌生人自动获得 admin** ⇒ `base = 1.0`（`SPEAKER_TRUST_BY_PERMISSION_LEVEL["admin"]`，已核实 `memory_settings.py:250`）⇒ `speaker_is_owner=True` ⇒ 获得对全体说话人账本签发 confirmation/correction 的权力 + 主人记忆读取权。
- 现有的全局判空**不是疏漏，是让通道切换 fail closed 的那道门**。

⇒ **保持现状。** 切换通道后主人必须手工把自己的新 id 填进 dashboard —— 这是正确的代价，而且它恰好就是 §2.9.4 的人工 bind 落点。

> 这里有一处自相矛盾必须点名：前置方案 A 一方面正确且强硬地封杀了「拿 bootstrap 提权当跨通道锚点」（对的），另一方面又让 bootstrap **更容易触发**（错的）。后者比前者更糟——当锚点至少还需要一个已存在的主人 entity，fail-open 则是凭空授予。

**【降级为遗留风险，见 §2.14 R12】`trusted_users` 的通道混池。** `trusted_users` 两种模式共用同一份列表（`config_store.py:85`），`_normalize_qq`（`permission.py:68-70`）只 `strip()`。碰撞时陌生人可通过旧通道遗留行继承 admin 档位。缓解（PR4）：**新写入**的行记录 `channel`；查询时「行无 channel（存量）⇒ 通配；行有 channel ⇒ 必须匹配」。**这对存量行不解决问题，我如实标注**——存量行的通道确实未知，把未知伪造成具体值正是 §2.3 论证 3 否决的做法。

---

## 2.8 解析 API

原 §2.6 保留，**签名一字不改**（入参仍是 `account_id`，不是任何复合键）：

```python
class TrustSnapshot:
    def entity_of(self, account_id: str) -> str | None: ...
    def same_entity(self, a: str, b: str) -> bool: ...          # 未加载 ⇒ False，不变
    def barrier_pending(self, platform: str) -> bool: ...
    def trust_inputs(self, account_id: str) -> tuple[float, int]: ...
    def resolve_trust(self, account_id: str | None, *, tier=None, base=None) -> float | None: ...
    # ★ 新增，只读诊断，不参与任何判定
    def channel_collision(self, account_id: str) -> bool: ...
    def channels_seen(self, account_id: str) -> tuple[str, ...]: ...
```

`resolve_trust` 的 `None` 语义（原 §2.6）**逐字不变**——这是保住弃权语义的关键，不得因本次修订产生任何位移。

**`speaker_channel` 不进 `resolve_trust`。** 它只在写路径的临界区内被记录（§2.11 D3），不影响任何分数。

---

## 2.9 链接机制

### 2.9.1 数据结构

`entities[*].accounts` 的键是 `account_id`；`account_index: account_id → entity_id` 是 union-find 的落表形态（先例：`local_server/telemetry_server/storage.py:1135-1189` 的 `canonical_map` + `canonical_alias`）。

### 2.9.2 mutator（原 §2.7 保留，一字不改）

```python
_resolve_entity_locked(state, entity_id, *, depth=8) -> str | None   # 沿 merged_into + 路径压缩，超深拒绝不猜
_bind_locked(state, account_id, entity_id)                            # account 已属另一实体 ⇒ 等价于 merge
_unbind_locked(state, account_id) -> str                              # 子记录整条移出，generation+1，两环原样带走
_merge_entities_locked(state, a, b, *, now) -> str                    # 存活者按 (created_at, entity_id) 排序
_forget_entity_locked(state, entity_id) -> dict
```

**幂等性（可证，逐条）：**

| 操作 | 幂等性 | 论证 |
|---|---|---|
| `bind(a, E)` | **幂等** | 已在 E 下 ⇒ no-op 且 `dirty=False`（不写盘）；在别的 E' 下 ⇒ 退化成 `merge(E, E')`，而 merge 幂等 |
| `merge(A, B)` | **幂等 + 可交换 + 可结合** | 存活者由 `sorted((created_at, entity_id))` 决定 ⇒ 两个方向的并发请求收敛到同一结果；`a == b` 直接返回 |
| `unbind(a)` | **非幂等（有意）** | 每次 `generation+1` 建新实体。第二次调用对已 seed 的 account ⇒ no-op 返回现 entity_id |
| `forget(E)` | 幂等 | 墓碑已存在 ⇒ no-op |
| **账本** | **零损** | merge 退化成 account 子字典的**不相交搬迁**（§2.6）；`adjustment` 纯加法且只在读取处夹 ⇒ 合并前后 `effective_trust` 的变化只来自求和范围变大 |

### 2.9.3 API

| 方法 | 路径 | 触发者 | PR |
|---|---|---|---|
| POST | `/internal/identity/accounts/bind` | **人工**（dashboard / 运维） | PR7 |
| POST | `/internal/identity/accounts/unbind` | **人工**（误合唯一回退入口） | PR7 |
| POST | `/internal/identity/entities/merge` | **人工** | PR7 |
| POST | `/internal/identity/entities/forget` | **人工** | PR7 |
| GET | `/internal/trust/profile?account_id=…` | 只读诊断（新增暴露 `channels_seen` / `channel_collision`） | PR1 |

**全部不进 `_STORAGE_LIMITED_MODE_ALLOWED_PATHS`**（`runtime.py:77-82`），与原 §4.8 一致。

### 2.9.4 触发者：**没有任何自动 bind**

```
自动建边的代码路径数量 = 0
```

`_bind_locked` / `_merge_entities_locked` **只有** PR7 那 4 个 HTTP 端点这一个调用点，无任何内部调用点。原文档 §7 PR7 的硬序约束「PR7 之前绝不能存在任何非恒等 alias」保持不变。

> **相对前置方案的关键删除：不做 `_adopt_legacy_locked`（存量领养）。** 两位评审各自独立证明它是一条真实的自动合并路径，且其「主防线」在上线时刻为空、探测器结构性看不见它。而在本设计下它**根本不需要存在**——存量裸 key 与活体 `account_id` 是**同一个字符串**（`permission.py:47` 的 `_normalize_qq` 只 strip，其上游 `sender_id` 与 `session_memory_service.py:985` 的 `f"qq:{sender_id}"` 是同一个变量），迁移就是恒等映射，没有任何缝需要「领养」去补。**领养机制是复合键方案为自己制造的问题所准备的解药。取消复合键，问题与解药一并消失。**

**人工 bind 的落点（不新建 UI）：**

已核实 `trusted_users` 是仓库里唯一「用户亲手把一个 id 归给一个有名字的人」的持久化结构（`permission.py:55-66`，行形状 `{"qq","level","nickname",…}`，UI 在 `static/script.js:293` 与 `:1120-1129`）。

切换通道后用户**必然**要去 dashboard 编辑那一行（因为 §2.7 保留了 fail-closed 的 bootstrap）。今天他只能**新建一行**，旧行的 trust 就此失联。改动：把「保存」拆成两个按钮——

- 「新增用户」（今天的行为）
- **「替换 ID（保留信赖度）」**：改这一行的 id，同时 `POST /internal/identity/accounts/bind {"account_id": "qq:<新>", "entity_id": entity_of("qq:<旧>")}`

用户点的是他**本来就要点的那个保存**。这次点击是一个**显式的人身断言**（「这一行还是同一个人」），语义上恰好就是 bind 需要的证据。

**排序/展示规则（必须写死）**：候选旧账号列表**只能按账本权重排序**（`|adjustment|` + `message_count`），**绝不能按昵称相似度排序、绝不预选**。把相似度放进 UI 排序 = 把被否决的启发式塞给用户当默认答案。

### 2.9.5 如果维护者坚持要「无人参与」

唯一诚实的方向是**挑战-应答**，不是离线特征匹配：切换模式时留一个短窗，bot 在旧通道私聊发一次性码，用户在新通道回码；码必须单次消费、不可猜、短 TTL、原子消费。

代价必须说清：`qq_auto_reply` 全仓**没有任何聊天指令解析**（grep `startswith("/")` 在该目录下唯一命中是 `qq_client.py:89` 的文件路径判断），也没有 bind/verify 流程。这是**新增能力**，不是补一个字段。**而且它仍然需要人配合一次——它同样不叫自动。**

---

## 2.10 存量归属与 event_id / 账本完整性（经核实的实际后果）

### 2.10.1 【新发现，两份前置方案都不知道】字节级拆分会不可逆地污染记忆语料

**这是本次修订最重要的发现，也是拒绝字节级拆分的决定性理由。**

已核实 `memory/facts.py:3453-3498` `_reconcile_existing_provenance`（我逐行读过原文）：

```python
existing_speaker_id = stable_speaker_id(existing.get('speaker_id'))
if existing.get('speaker_provenance_mixed') is True:
    desired_provenance = {'speaker_provenance_mixed': True}       # ← :3472 吸收态，永远粘住
elif existing_speaker_id is None:
    desired_provenance = dict(request_provenance)
elif existing_speaker_id == request_provenance['speaker_id']:     # ← :3476
    desired_provenance = provenance_of_entries((existing, request_provenance))
else:
    desired_provenance = {'speaker_provenance_mixed': True}       # ← :3480-3481
...
for key in provenance_keys:                                        # ← :3489-3491
    existing.pop(key, None)                                        #    speaker_id / speaker_label
existing.update(desired_provenance)                                #    / speaker_trust 全部被摘掉
```

**若 `speaker_id` 字节改变（`qq:123` → `qq.napcat:123`）**，同一个真人的 pre-flip 历史行与 post-flip 观察在去重命中时走 `else` 分支：

| 后果 | 证据 | 可逆性 |
|---|---|---|
| 该行的 `speaker_id` / `speaker_label` / `speaker_trust` **被 pop** | `:3489-3491` | **不可逆**（`_rollback_uncommitted_facts` 只在同一次请求落盘失败时回滚） |
| 该行**永久停止产生 trust 信号** | `facts.py:1363` `if prior.get('speaker_provenance_mixed') is True: continue` | 不可逆 |
| 该行在 `scoped_refine` 被排除 | `scoped_refine.py:185-190` `speaker_provenance_mixed is not True` 过滤 | 不可逆 |
| 该行在 `fact_dedup` trust 仲裁被排除 | `fact_dedup.py:1290-1291` | 不可逆 |
| `mixed=True` **永远粘住** | `:3472` 第一分支；全仓无任何写 `False` 的路径 | 不可逆 |

调用点是 `:3601`（exact 去重）**和** `:3710`（FTS5 语义去重），`source='user_observation'` 正是 scoped_history 的默认值。**语义去重不要求文本相同 ⇒ 爆炸半径远大于「原句复述」。**

同源路径还有 `fact_dedup.py` 的 `_fold_survivor_provenance`（`len(attributed_ids) > 1 ⇒ mixed`，已核实）与 `speaker_trust.py:556-573` 的 `provenance_of_entries`（`len(speaker_ids) != 1 ⇒ mixed`，已核实）。

> **这正是评审问的「把同一个人拆成两个」，只是落点在记忆语料的 provenance 层而不是 trust 账本层。`unbind` 修不了、任何链接机制都修不了、entity 层完全够不着它。** 而原文档 §1.2(1) 恰恰把 mixed 定性为「全仓无清除路径的吸收态」——字节级拆分会**主动、持续、批量**地制造它。

**本设计（`account_id` 字节不变）完全不触发这条路径**：`existing_speaker_id == request_provenance['speaker_id']` 恒成立 ⇒ 走 `:3476` 的正常合并分支。

### 2.10.2 本设计的账本完整性：**失配为零**（逐处核实）

`speaker_id` 字节不变 ⇒ 三处 SHA256 烘焙的输入全部不变：

| # | 位置 | 公式 | 本设计 |
|---|---|---|---|
| 1 | `speaker_trust.py:518-520` | `trust_event_id(kind, source_fact_id, target_speaker_id)` | 第三参仍是 `qq:123456` ⇒ **不变** |
| 2 | `facts.py:1390-1400` | `signal_identity = json[name, source_id, kind, subject_id, scope, fact_id]` → `signal_key` | `source_id` 与 `subject_id` 均不变 ⇒ **不变** |
| 3 | `session_memory_service.py:2004-2007` | `"activity_" + sha256(f"qq:{sender_id}\|{stable_activity}")` | 前缀不变 ⇒ **不变**（该 id 在 PR4 因「批级→逐条」而变，那是原 §4.2 已显式接受的**唯一一次**变更，与本修订无关，**不叠加**） |

⇒ **存量 `processed_signal_events` 逐条命中，历史纠错零重放。** 迁移是恒等映射（`normalize_account_id(f"qq:{bare_key}")` = 今天的 `f"qq:{sender_id}"`）。

**同时不发生的四件事**（若字节级拆分则全部会发生）：

| # | 后果 | 机制（已核实） |
|---|---|---|
| 1 | **fresh 环双算** | `source_id` 变 ⇒ `signal_key` 变 ⇒ `event_id` 变 ⇒ 存量环不命中 ⇒ 同一语义信号二次计分。单笔 `CORRECTION_DELTA=0.08` ⇒ 双记 **0.16 > `ARBITRATION_MARGIN=0.15`**；环里只存 id 不存 kind（`permission.py:322-327`）⇒ 事后不可反算 |
| 2 | **replay 环永久失效** | `facts.py:1350-1352` `stable_speaker_id(recorded['source_speaker_id']) == source_id` ⇒ 全部升级前持久化的 signal 事件**永久停止重放** ⇒ 丢响应后的幂等补发保证对历史事件静默失效（欠计，fail-closed，但是静默的） |
| 3 | **自证禁令 fail open** | `facts.py:1365-1367` 是纯字符串相等。老行 `qq:123` vs 新 source `qq.napcat:123` 不等 ⇒ **主人可以用新协议给自己的旧发言盖 confirmation**。这是安全回归 |
| 4 | **插件侧选择性丢弃** | `permission.py:319` `if platform != "qq": continue`（**静默，无日志**）。注意：它判的是事件里 `speaker_id` 的前缀，而那是**目标 fact 行**的 speaker_id ⇒ 老行照常通过、新行被丢 ⇒ **选择性丢弃，不是全量**（前置方案在这里描述错了；选择性比全量更阴，因为它让账本按 fact 行新旧产生偏斜） |

> **纠正一条被反复引用的过强论据**：「环里只存 id 不存 kind ⇒ 事后无法反算 ⇒ 不可修复」这句在两份方案里各被当支点引用了三次。已核实 `facts.py:1466-1476`：**fact 行保留了完整事件体**（kind / target speaker_id / source_speaker_id / observation_id / subject 三元组），归档侧也有 `aload_archived_speaker_trust_signal_facts`。所以重建源**存在**，只是不完备（`ascoped_forget` 会连行带事件删掉，原 §3.8 R3 已写）。论据方向对，**强度被夸大了**——既然它同时是闸门必要性的支柱，用词该准确。

### 2.10.3 存量归属：**不做任何通道判定**

原文档 §5.2 有一句错话必须改：

> 原文：「源：`business_config.json` 的 `speaker_trust_profiles`，key 是**裸 QQ 号**字符串」

**已核实这句只在从未跑过 `open_platform` 的部署上成立。** `permission.py:319` 的过滤只看 `platform != "qq"`，开放平台下 speaker_id 是 `qq:<openid>` ⇒ 同样入池，key 就是 openid；`config_store.py:87-88` 的 `speaker_trust_profiles` 与 `trusted_users` 同在一个 business_config、两种模式共用；`permission.py:176-221` 的 profile 结构零 provenance。

**改成**：「key 是**裸 actor** 字符串，**通道未知且不可判**。」

**本设计对此的处理：不判。** 迁移逐条做 `normalize_account_id(f"qq:{bare_key}")`，与运行期路径同一个函数、同一个结果。`legacy_import` 哨兵按 `(source, account_id)` 记，account_id 是**不可变量** ⇒ 原 §5.3 的幂等语义**逐字保持**，不存在前置方案 A 那条「键随 deployment_mode 变化 ⇒ 切模式重启即二次导入」的击穿。

> 有一条**单向硬判据**存在（`qq_client.py:626/680/759` 三处发送侧硬做 `int(user_id)` ⇒ **非纯数字 key 一定不是 NapCat 起源**），但本设计**不使用它**——因为本设计不需要给任何 key 定通道。记录在此供 §2.12 的条件升级路径使用，并附反向证据：开放平台侧的 `@` 提取正则**也**写死 `\d+`（`qq_open_plat.py:610`），且 `_convert_event` 全仓**零测试覆盖**，说明该假设从未被守卫。

### 2.10.4 `areconcile_from_facts` 保持可用

原 §3.8 的灾备工具在本设计下**判据不变**：fact 行上有、池里没有 ⇒ 那次折叠丢了。折进哪条记录？折进 `stable_speaker_id(event['speaker_id'])` 对应的 account —— 与写路径同一个函数、同一个结果。

> 若采用复合键或字节级拆分，这个工具**会失效**：它是离线扫描，没有「本请求的 channel」可借，面对一条历史事件无法确定该折进哪条记录，折错 = 账本错配且不可逆。前置方案 A 的 20 项 file_delta 里该工具一次都没出现。**本设计保留它可用，这是「channel 不做键」的又一个直接收益。**

---

## 2.11 错合防御与回退路径

### 防御（按强度排序）

**D1（结构性，最强）：不存在任何自动 bind 路径。**
`_bind_locked` / `_merge_entities_locked` 的调用点只有 PR7 的 4 个人工端点。自动建边的代码路径数量为 0，因此「系统自己把两个人合成一个」在结构上不可达。

**D2（结构性）：account 键不可变。**
键是 `account_id`，由 `stable_speaker_id` 唯一归一，不含任何随配置/时间变化的成分。⇒ 幂等哨兵、`account_index` 函数性、merge 不相交性全部结构成立。

**D3（可观测）：碰撞探测器 —— 把不可知量变成可观测量。**

写路径临界区内（与账本落库同一次原子写）：

```
记录 channels_seen[channel]（first/last/count）与 channel_observations[platform][channel]
若同一 account_id 的 channels_seen 出现第二个通道：
  → 记 channel_collisions[account_id]（channels 集合 + hits + detected_at）
  → logger.warning（loud，带 account_id，不带任何 PII）
  → 本段响应 trust.channel_collision = true
  → 账本照常落库（本设计下账本行为与探测无关）
若出现从未见过的 channel 值（拼错）：
  → 响应 trust.new_channel = "<value>" + warning（不阻断）
```

> **这条防线的意义：§2.0(2) 那个「openid 会不会长得像纯数字」的不可知量，从「设计的前提假设」降级成「运行时可观测事件」。探测器为零 ⇒ 字节级拆分无收益，收工；探测器非零 ⇒ 那才是启动 §2.12 的触发条件，而且此时你已经知道代价是值得的。**

**D4（红线，写进 `memory/trust_store.py` 模块 docstring）：显式封杀清单。**

绝不基于以下任何一项建立 entity 边：

1. **显示名 / 昵称 / 群名片** —— `qq_client.py:455`（先 nickname）、`message_dispatcher.py:64`（先 card，**优先级相反**）、`qq_open_plat.py:582`。用户可改、非唯一、`card` 按群作用域。
2. **bootstrap 提权**（`message_dispatcher.py:44-54`）—— 触发条件是 `list_users()` 为空这个**配置状态**，不是身份证明。
3. **时序相邻** —— 切换前后第一个说话的人。
4. **任何编辑距离 / 相似度 / 形状启发式**。
5. **channel 本身** —— 它是观测属性，不是身份证据。

**D5（授权面）：`speaker_is_owner` 与 tier 绝不由 entity 或 channel 推导**（§2.7）。

### 回退路径

| 错误 | 可逆性 | 回退方式 |
|---|---|---|
| 人工 bind 绑错人 | **账本层完全可逆** | `POST /internal/identity/accounts/unbind`。`_unbind_locked` 把 account 子记录**整条**移出、generation+1 建新实体、两环原样带走。账本零损（§2.6 的分区结构保证搬迁是不相交移动） |
| 人工 merge 合错实体 | **可逆但有约束** | 对每个被并入的 account 逐个 `unbind`。丢失的只有 entity 级元数据（`created_at`、原 entity_id）。**建议 PR7 在 `merged_into` 链上加一个 `merged_accounts` 列表**（成本一行，收益是 merge 的完整逆操作） |
| 探测到碰撞 | 不需要回退 | 账本本来就是分开的；探测器只是告知。处置见 §2.12 |
| 通道观测记错（拼错 channel） | 完全可逆 | 纯诊断数据，不影响任何账本值；改正后覆盖即可 |
| 两个人的账本被**求和进同一条记录** | **不可逆** | **本设计下不存在这条路径**（D1 + 无自动 bind + 无领养 + 无复合键） |

**【必须诚实说明的一条：回退不是全知全能。】**

误合期间写下的每一条 fact 行，都被永久盖上按虚高 `trust_inputs` 求和算出的 `speaker_trust`（`routes.py` 注入 → `facts.py:3430-3451` → `fact_entry.update(request_provenance)`）。这个戳是 `fact_dedup.py` / `scoped_refine.py` / `persona/corrections.py` 三处仲裁的输入，**`unbind` 不会重写它**。

⇒ 「误合完全可逆」这句只在**账本层**成立，**fact 行层不成立**。原文档 §5.1/R1 本来就承认了同族性质（「那段时间写入的 fact 行……仲裁永久弃权」），本次把它明确套用到链接的可逆性论证上。**在本设计下，唯一能产生误合的是人工 bind，所以这条风险的曝光面 = 人点错的次数。**

---

## 2.12 条件升级路径：如果碰撞探测器响了

**触发条件**：`channel_collisions` 非空，或外部实测证实 openid 与裸 uin 的字符集重叠。

**此时字节级拆分才有净收益**，形状为 `qq.napcat:<uin>` / `qq.open:<openid>`，并**必须**同时付以下代价（全部已在 §2.10.2 核实）：

| # | 必付项 | 说明 |
|---|---|---|
| 1 | **`_reconcile_existing_provenance` 必须实体化** | `facts.py:3476` 的 `==` 改成「`==` 或 `legacy_account_form` 相等 或 `same_entity`」。**这是拆分的前置阻塞项，不是可选优化**（§2.10.1） |
| 2 | **`fact_dedup.py:1294-1312` 与 `scoped_refine.py:185-205` 必须同批处理** | 两处 D 类等值比较会从「恒真短路」变成「活的行为分支」：同一真人的 pre/post-flip 行会开始互相做确定性 trust 仲裁 |
| 3 | **`signal_identity` 的 `source_id` 必须投影** | `facts.py:1391` 改成 `legacy_account_form(source_id)`，否则 fresh 环双算 0.16 > margin 0.15。**target（`trust_event_id` 第三参）绝不投影** |
| 4 | **自证禁令必须三段判据** | `facts.py:1365-1367` 加 `legacy_account_form` 相等 与 `same_entity`；PR7 从「可选」升格为**前置必要条件** |
| 5 | **`platform_root()` 白名单** | `SPEAKER_TRUST_LEGACY_BARRIERS` 按 `"qq"` 索引，而 `account_platform("qq.napcat:1")` 返回 `"qq.napcat"` ⇒ 不改则闸门对活体流量**恒 miss**、§5.1 的双算防护形同虚设。必须是**白名单**而非「含点即折叠」（否则将来 `bilibili.live` 会被误折进 `bilibili`） |
| 6 | **`permission.py:319` 改走 `platform_root`** | 否则 PR4→PR5 窗口内选择性静默丢弃回传事件 |
| 7 | **长度预算收窄** | actor 预算 93 → 88（`qq.open:`）/ 86（`qq.napcat:`）。超长 ⇒ `stable_speaker_id` 返回 None ⇒ **422 ⇒ 卡死的不是 trust 而是整条 scoped_history 记忆写入** |
| 8 | **subject_id 仍然不拆** | §2.2.2 的理由不变 |

**方法论要求（这是两份前置方案共同的根因）**：原文档 §7 PR7 写着「其余 D 类等值比较全部不动，每处加 `# entity-resolution hook` 注释（**显式推迟，不是遗漏**）」。**那条推迟的前提恰恰是 `speaker_id` 永远逐字不变**。字节级拆分**作废这个前提**，因此升级路径的第一项工作必须是：

> **对全仓每一处 `speaker_id` 的等值 / 同一性判定做一次穷举审计并逐条定性**，至少显式覆盖 `facts.py:3453-3498`、`facts.py:1344-1367`、`fact_dedup.py:1294-1348`、`scoped_refine.py:185-205`、`speaker_trust.py:556-573`、`permission.py:315-325`，每处配一条「同一真人跨通道不得被判为两人」的回归测试。**否则不要拆。**

---

## 2.13 逐文件增量（映射到原 §7 的 PR1–PR7）

### PR1 —— 服务端骨架（dormant）

| 文件 | 改动 |
|---|---|
| `memory/identity.py`（新增） | 在原计划基础上增 `_CHANNEL_RE = re.compile(r"\A[a-z0-9_]{1,16}\Z")` 与 `normalize_channel()`。**`derive_entity_id` / `normalize_account_id` / `account_platform` 一字不改**。模块 docstring 写入 §2.4 的「确定性派生前提」教训 |
| `memory/trust_store.py`（新增） | 池 `version: 2`；顶层新增 `channel_observations` / `channel_collisions`；account 子记录新增 `channels_seen`；`_copy_on_write` 容器清单补这三项；`TrustSnapshot` 增 `channel_collision()` / `channels_seen()` 两个只读诊断。**模块 docstring 写入 D4 的五条封杀清单** |
| `config/memory_settings.py` | 新增 `SPEAKER_TRUST_CHANNEL_MAX_LEN = 16`。`config/__init__.py` 的 `:200-208` / `:593-601` 同步补 import 与 `__all__` |
| `app/memory_server/routes.py` | `GET /internal/trust/profile` 增暴露 `channels_seen` / `channel_collision` |
| `tests/unit/test_speaker_identity.py`（新增） | T6：`normalize_channel` 的锚定行为（含 `'OPEN'` / `'q#q'` / `'中文'` / 含换行 一律 None）；entity_id 与 account_id 命名空间不相交 property |

### PR2 —— wire 扩展

| 文件 | 改动 |
|---|---|
| `app/memory_server/routes.py` | `ScopedHistorySegment`(:975-999) 与 `ScopedHistoryRequest`(:1002-1033) 各加 `speaker_channel: str \| None = Field(default=None, pattern=r"\A[a-z0-9_]{1,16}\Z")`。**同批修正既有 `ActivityEvent.id` 的未锚定 pattern**（§2.2.4）。校验：给了 `speaker_channel` 但无 trust 来源 ⇒ 422（对齐 §4.3 第 5 条口径）。**不做平台×通道白名单**（保持平台中立，理由与原 §1.2(4) 砍掉保留平台词表同源） |
| `tests/unit/test_group_memory_scopes.py` | 补 `speaker_channel` 与 `ActivityEvent.id` 的锚定断言（含空白/换行/CJK 一律 422） |

### PR3 —— 迁移 + 闸门

| 文件 | 改动 |
|---|---|
| `memory/trust_store.py` | `_import_locked` **一字不改**（`normalize_account_id(f"{platform}:{bare_key}")`，哨兵按 `(source, account_id)`）。**不新增 `_adopt_legacy_locked`，不新增 `origin_evidence`，不新增打标表** |
| `tests/unit/test_speaker_trust_migration.py` | 新增 T8：napcat-only 部署的池键与本次修订前**逐字节相同**（零迁移证明） |

### PR4 —— 插件翻转（唯一行为变更 PR）

| 文件 | 改动 |
|---|---|
| `plugin/.../qq_client.py` | 类上加 `CHANNEL: ClassVar[str] = "napcat"`；`:446`（notice）与 `:471`（message）的内部信封各加 `"channel": self.CHANNEL`。**必须在 ingest 时刻打戳，不能在 flush 时刻读 config** —— 消息在会话缓冲里可能跨越一次模式切换（`settings_service.py:830-832` 的切换是即时的，缓冲不清空） |
| `plugin/.../qq_open_plat.py` | 类上加 `CHANNEL: ClassVar[str] = "open"`；`_convert_event` 两个分支（`:584-601` C2C / `:603-631` 群）各加 `"channel": self.CHANNEL` |
| `plugin/.../message_dispatcher.py` | `:310` 附近把 `message["channel"]` 一起取出并透传。**`:44` 的 bootstrap 早退一行不改**（§2.7 明确否决通道感知化） |
| `plugin/.../backlog_service.py` | `QQBacklogMessage` 加 `channel`，`:70-78` / `:95-119` 两处构造点填入。backlog 行跨重启存活 ⇒ 会跨模式切换存活 ⇒ 重放时必须带**原通道**而非当前通道 |
| `plugin/.../session_memory_service.py` | `:976-988` / `:1405-1421` 在发 `speaker_tier` 的同时发 `speaker_channel`（**取自消息信封，不取自 `_qq_settings`**）。`:2004-2007` 的 activity event_id **一个字节不改** |
| `plugin/.../memory_bridge.py` | 两个 post 方法加 `speaker_channel` 关键字。`PLATFORM` / `speaker_account_id()` 只产出 `qq:<actor>`。**三个 subject builder（`:49-73`）一行不改**——原 §7 PR4 那条 bullet 删除 |
| `plugin/.../permission.py` | `trusted_users` 行**新写入时**记 `channel`；`get_permission_level()` / `list_users()` 加可选 channel 过滤，规则「行无 channel ⇒ 通配；行有 channel ⇒ 须匹配」（§2.7，存量行残留风险记为 R12）。`_normalize_qq` 保持只 strip |

### PR5 / PR6 —— 不变

原文档删除清单与 wire 清理不受本次修订影响。

### PR7 —— 实体链接

| 文件 | 改动 |
|---|---|
| `memory/trust_store.py` | 原 5 个 mutator 不变。**建议新增 `merged_accounts` 列表**（§2.11 回退表） |
| `app/memory_server/routes.py` | 4 个 identity 端点 |
| `memory/facts.py` | `:1365-1367` 的自证禁令实体化（原计划不变，默认 `identity=None` 时逐字退化） |
| `plugin/.../static/script.js` + `i18n/*.json` | 「替换 ID（保留信赖度）」按钮（§2.9.4）。**排序键只能是账本权重**。8 语种同步 |

### 必须新增的守卫测试

| # | 测试 | 守的是什么 |
|---|---|---|
| T1 | 通道化后 `speaker_id` 在 wire 与 fact 行上**逐字节等于**通道化前 | 防回归：防止将来有人把 channel 拼进 speaker_id |
| T2 | 同一 `account_id` 在两个 channel 下各跑一轮 ⇒ **同一条**账本记录、`channel_collisions` 计数 = 1、`adjustment` 只累加一次 | 碰撞语义 |
| T3 | `_bind_locked` / `_merge_entities_locked` 的调用点数量断言（静态扫描）= 仅 4 个 HTTP handler | **D1 的结构性守卫** |
| T4 | 迁移前后池键逐字节相同（napcat-only 与 open-only 两种部署各一例） | 恒等迁移 |
| T5 | bind → unbind 后账本**逐位**等于 bind 前 | 回退零损 |
| T6 | `normalize_channel` / `speaker_channel` / `ActivityEvent.id` 的锚定行为（含换行、CJK、`#`） | §2.2.4 的既有 bug |
| T7 | merge 的幂等 / 可交换 / 可结合 / 账本零损 | §2.9.2 |
| T8 | 切通道后 bootstrap 提权**仍然 fail closed**（新通道第一个陌生人**拿不到** admin） | **§2.7 的否决项守卫**，防止将来有人「顺手修这个 bug」 |

---

## 2.14 遗留问题与需要维护者拍板的点

### 2.14.1 新增的遗留风险（在原 §9.1 的 R1–R9 之后）

| # | 风险 | 缓解 / 定性 |
|---|---|---|
| **R10** | **openid 字符集未知** —— 仓内无 fixture、无校验、无注释；`qq_open_plat.py:610` 的 `\d+` 假设来自零测试覆盖的抄写代码 | **已降级为可观测量**（D3 探测器），不再是设计的阻塞假设。探测器为零 ⇒ 无碰撞；非零 ⇒ 触发 §2.12 |
| **R11** | **`author.id` 是否跨群稳定 —— 仓内零证据，本设计无法缓解** | 若开放平台的群成员标识确实是 `member_openid` 语义（app×群×人 作用域），则同一真人在两个群就是**两个不同的 account**。这不是合并风险而是**碎裂**风险：`qq:` 在开放平台侧自身就不是「一人一 id」，会动摇「account = 平台账号」这层本体前提，并让该通道的 trust 账本按群碎成 N 份、永远攒不到 cap(=20)。**上线前阻塞项，只能实测**（见下） |
| **R12** | **`trusted_users` 存量行的通道混池** | 新写入行记 channel；存量行通配（保持今天行为，零升级风险）。碰撞时存量行仍可被陌生人命中继承档位。**明确不修**——把未知伪造成具体通道正是 §2.3 论证 3 否决的做法 |
| **R13** | **fact 行层与 subject_id 层不受本设计保护** | 碰撞时 `qq:123456` 这个 participant subject 会被两个人共用 ⇒ persona section / scope 隔离域 / display_name 串。**这是一个独立于 trust、今天就存在的缺陷**，本设计既不制造也不修复。若探测器响了，这才是最该先修的地方（且必须走 §2.12） |
| **R14** | **人工 bind 绑错后，误合期间写下的 fact 行的 `speaker_trust` 戳不会被重写** | §2.11 已如实列出。曝光面 = 人点错的次数 |

### 2.14.2 上线前**阻塞项**（不能推理，只能实测）

1. **抓一次真实 `GROUP_AT_MESSAGE_CREATE` 载荷**，打印 `author.id`，判定字符集与长度。
   零成本取证：`qq_open_plat.py:599` / `:629` 已把完整 `d` 载荷原样挂在 `message["raw"]` 上并落进 backlog（`backlog_service.py:77` / `:119`），**零 wire 改动可读**。
2. **用同一个 QQ 账号在两个群各发一条 @bot 消息，比对两条的 `author.id`。**
   相等 ⇒ R11 解除；不等 ⇒ R11 兑现，需要单独决策（要么接受开放平台侧 trust 按群碎片化，要么在 account 层之上再加一层群无关聚合）。**这一条比跨通道链接更该先解决**，因为它动摇的是本体前提本身。

> 顺带说明一条**看似免费但无解**的线索：若平台在 `author` 里同时下发 `member_openid` / `user_openid` / `union_openid`，它们今天已经在进程内（挂在 `raw` 上）、零 wire 改动可读。但即便存在，`union_openid` 按公开语义只跨「同一开发者名下的多个应用」，**不跨到 uin**，对 napcat↔open 链接无解。**不要把它写进设计当退路。**（这条属协议层假设，非代码证据，单列标注。）

### 2.14.3 需要维护者拍板

**Q1（本次修订的核心拍板项）：接受「channel 做观测属性、不做键」吗？**

- **接受**：`account_id` 零字节变更、账本零损失、`areconcile_from_facts` 保持可用、无任何自动错合路径、无 mixed-provenance 污染。代价是：**wire 与磁盘上一个字节没变**，「拆」体现为「本来就是分开的 + 探测器证明它是分开的 + 本体能把它们连起来」。
- **不接受，坚持字节级拆分**：必须先付 §2.12 的 8 项，其中第 1 项（`_reconcile_existing_provenance` 实体化）与第 2 项（两处 D 类比较）是**不可跳过的阻塞项**，否则会持续、批量、不可逆地污染记忆语料。

> **我必须把落差说清楚**：维护者说「可以拆」，本设计交付的不是一个新的 id 前缀。如果期望的是「看到 `qq.napcat:` 出现在数据里」，那本设计不满足；如果期望的是「两条通道的账不串、能证明不串、同一个人能被连起来」，本设计满足且代价为零。**这是一个需要明确对齐的期望差，不是可以糊过去的实现细节。**

**Q2：R11 实测若证实碎裂，怎么处置？**
选项：(a) 接受开放平台侧 trust 按群碎片化；(b) 在 account 之上加一层群无关聚合；(c) 开放平台通道暂不接入 trust。**这是产品决定，不应塞进本次拆分。**

**Q3：`speaker_channel` 要不要做成平台声明式？**（池里维护「哪些平台有通道维度」，对「无通道维度的平台却发了 channel」直接 422）
- 做：拼错的通道名 fail loud。
- 不做：完全平台中立，靠 D3 的 `new_channel` 告警兜底。
- **我倾向不做**，与原 §1.2(4) 砍掉保留平台词表是同一条理由（给已被存量数据流经的路径加白名单，误伤面大于收益）。且本设计下 channel 不参与判定，拼错的最坏后果只是诊断数据脏，不影响任何账本值。

**Q4：`merged_accounts` 字段要不要在 PR7 加？**（成本一行，收益是 merge 的完整逆操作）**倾向加。**

**Q5：`_reconcile_existing_provenance` 的实体化要不要提前到 PR7 一起做**（即便不拆字节）？
- 提前做：`same_entity` 一旦有非恒等 alias，「同一个人的两个账号在 fact 行上互判为两人」这条今天就存在（跨平台场景：同一人的 QQ 号与未来 B 站号写同一条 fact），做了能修。
- 不做：维持原 §1.2(1) 的「D 类不实体化」决定。
- **倾向提前做，但降级为 PR7 的可选项**，因为它的失败方向（放宽 mixed 判定）落点仍是 mixed 吸收态，需要单独的穷举审计（§2.12 方法论要求）。

---

## 附：本次修订相对原文档的**结论级**变更清单

| 原文位置 | 原结论 | 修订后 |
|---|---|---|
| §9.2 第 3 问 | 「QQ 两通道 id 空间是否引入 channel 维度？**默认不引入**，需维护者确认」 | **已决议**：引入 channel，但作为**观测属性**而非键；字节级拆分保留为条件升级路径（§2.12），触发条件是碰撞探测器 |
| §5.2 | 「key 是**裸 QQ 号**字符串」 | **改**：「key 是**裸 actor** 字符串，通道未知且不可判」（跑过 open_platform 的部署里混着 openid） |
| §7 PR4 | 「三个 subject builder 与两处 `f"qq:{...}"` 改走 `speaker_account_id()`」 | **删除 subject builder 那一半**：改 subject_id 会让全部存量 scoped 记忆与 persona section 变孤儿 |
| §4.1 | `ActivityEvent.id` 的 `pattern` 「直接堵掉……名字带空格会 422 的坑」 | **该断言为假**（实测：pydantic v2 未锚定 search，`'participant:猫娘 A:12:34:56'` 与含换行的值都 PASS）。改为 `\A…\z`（**小写 z**；`\Z` 在 pydantic-core 的 Rust regex 下编译失败）或 `^…$` 锚定，并补测试。详见 §2.2.4 的实测矩阵 |
| §1.1 决策表 | —— | **新增一行**：「通道维度 = 池内观测属性 + 碰撞探测器；wire `speaker_id` 字节不变」 |
| §1.2 明确不做 | —— | **新增两条**：不把 channel 编进 platform 段或 subject_id；不做任何自动跨账号 bind（含存量领养） |
| §9.1 | R1–R9 | **新增 R10–R14** |
