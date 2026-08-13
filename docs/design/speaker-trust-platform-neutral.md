# N.E.K.O. speaker_trust 平台中立化 —— 最终设计（实施依据）

> 本文以「B 方案 · 服务端权威 trust 池（server-authority）」为骨架，嫁接 minimal-seam 与 ontology-first 两份方案被评审确认的亮点，并逐条给出三份评审所列硬伤的解法。文中所有代码位置均已在 `origin/main`（worktree `trust-identity-neutral`，HEAD `e8a97d0a0`）核实。

---

## 1. 决策摘要

### 1.1 做什么

| 项 | 决定 |
|---|---|
| trust 池归属 | 上移到 memory_server 进程，落 `<memory_dir>/speaker_trust.json`（根级平铺**单文件**、非角色态） |
| 唯一写者 | memory_server 进程内的 handler。插件 / dashboard / 迁移脚本一律走 HTTP，禁止直写该文件 |
| 插件职责 | 退化成「上报权限档位 + 上报幂等活跃度事件 id」。不再持有池、不再演化、不再接收回传、不再有写者锁 |
| 实体本体 | 两层 `entity ← account`。**账本按 account 分区**，entity 层只做聚合读 |
| 账本载体 | `adjustment` / `message_count` / 两个事件环全部挂在 **account 子记录**上 |
| 跨角色 | 保持共享（路径不含 `lanlan_name`），并写成显式决定 |
| 跨群 | 保持共享（不变） |
| 迁移方向 | 插件**每次启动**分块 POST 推送 legacy 快照；服务端按 account 打幂等 marker、**加法合并** |
| 迁移闸门 | 池里维护 `legacy_barriers`。闸门未开时该平台的 trust 全线停摆（不评分、不盖戳、不结算、不重建） |
| 取消安全 | `threading.Lock` + 同步 mutator + 整段一次 `asyncio.to_thread`。消灭 `settings_service.py:740-778` 的 `ensure_future`/`shield`/二次取消循环/before-after 回滚 |
| 失败契约 | 池写发生在 fact 提交之后，**永不 5xx / 永不 409**；逐段回传 `trust.persisted=false`，调用方保留桶重试 |

### 1.2 明确不做

1. **不做 D 类 11 处等值比较的实体化**（`fact_dedup.py`、`scoped_refine.py`、`persona/corrections.py`、`facts.py` 的若干 `speaker_id` 字符串比较）。唯一实体化的是 `memory/facts.py:1365-1367` 的自证禁令，且推迟到 PR7。理由：放宽这些点的失败方向是 `speaker_provenance_mixed`，而 mixed 是全仓无清除路径的吸收态。
2. **不动 `signal_identity` 里的 `lanlan_name`**（`memory/facts.py:1390-1397`）。同一句 owner 纠错在角色 A/B 下各产生一个 event_id、各扣一次分，写成显式语义。
3. **不引入 `signal_scope_key`**（ontology-first 的跨角色去重方案）。已核实 fact id 形如 `fact_{YYYYMMDDHHMMSS}_{content_hash[:8]}`（`memory/facts.py:3729`，带秒级时间戳、每角色独立 store），跨角色 scope_key 几乎必然不相等 → 收益为零；而它漏掉 `relation` 维度会把「同一源 fact 先 confirmation 后 correction」的第二条永久吞掉 → 净负收益。
4. **不给 `stable_speaker_id` 加保留平台词表**。entity_id 不含冒号已经在语法层保证两个命名空间不相交（`stable_speaker_id` 在 `partition(":")` 无分隔符时返回 None，`speaker_trust.py:178-180`），黑名单只会给一个已被存量数据流经的归一函数增加误伤面，而误伤的落点是 `speaker_provenance_mixed` 吸收态。**两位评审一致要求砍掉，采纳。**
5. **不做自动 `reconcile_from_facts`**。只提供手动端点（灾备用）。
6. **不做实体级 forget 的完整级联**（PR7 只给最小 `forget`，残留物显式列出）。
7. **不做账本分片/容量治理**（留逃生口：分片只能是 `speaker_trust.<n>.json` 平铺文件名，绝不开子目录）。

### 1.3 为什么（三条核心论证）

**(a) 为什么这能消灭 `settings_service.py:703-792`。**
那段 `ensure_future` + `shield` + 二次 `CancelledError` 循环 + before/after 回滚存在的唯一原因是：它必须「持锁跨 await」（trust 与 dashboard 配置挤在同一个 `business_config.json`，写盘要走 `_persist_business_config_locked`），且必须在取消后知道盘落没落（`cancelled.speaker_trust_persisted`）。服务端版本把整段临界区塞进**一个** `asyncio.to_thread`：`to_thread` 交出去取消不掉，锁的 acquire/release 全在 worker 线程内，既不漏落盘也不泄漏锁 —— 结构性地没有「持锁跨 await」。这与 `app/memory_server/gates.py:98-113` 已论证过的口径逐字一致。

**(b) 为什么落点必须是 memory_dir 根级的「文件」。**
`utils/cloudsave_runtime/operations.py` 的 import 清理逻辑：`delete_dir_targets` 只收 `child.is_dir() and child.name not in imported_character_names` 的子项并 `shutil.rmtree`；`delete_file_targets` 只由 `memory/<角色>/<MANAGED_MEMORY_FILENAMES>` 三段构成，manifest 键非三段或叶名不在白名单直接 `raise ValueError`。因此 `memory/_trust/` 这类**子目录**会在一次云恢复后被静默清空，而**根级平铺文件**两条都躲开。先例：`app/memory_server/gates.py:128-129` 的 `idle_maintenance_state.json`（docstring 明写「本包唯一一个不分角色的进程级文件」）、`main_logic/quota/ux_state.py`（「global rather than per-character because quotas are scoped to the user」）。

**(c) 关于「trust 不进云存档」这条 tradeoff —— 三份方案都写错了。**
已核实 `utils/cloudsave_runtime/_shared.py:133-141` 的 `MANAGED_CLOUDSAVE_PREFIXES = ("characters/","catalog/","profiles/","bindings/","memory/","overrides/","meta/")`，`plugins/` 只出现在 `LEGACY_RUNTIME_DIR_NAMES`（仅用于 `legacy_migration.py` 的本地目录搬迁，与云上传无关）。**即今天的 trust 池（插件 `business_config.json`）本来就不进云存档。** 所以本方案不是行为倒退，而是同档平移。云备份是一块独立的、今天不存在的能力（见 §9）。

---

## 2. 实体本体

### 2.1 两层：`entity`（人） ← `account`（平台账号）

**account_id 完全沿用 `stable_speaker_id`（`memory/speaker_trust.py:173-185`），一个字节不改**：形状 `platform:actor`，platform 小写化、actor 原样保留、总长 ≤96、无空白/不可打印字符、platform 匹配 `[A-Za-z0-9_.-]+`、actor 匹配 `[A-Za-z0-9_.:@-]+`。

**fact 行上的 `speaker_id` 永远继续存 account_id，绝不存 entity_id。** 这是整份设计里最重要的一条兼容性决定（三份评审一致点名），三条支撑逐条核实：

- `trust_event_id(kind, signal_key, target_speaker_id)`（`speaker_trust.py:518-520`）把 speaker_id 烘进 SHA256；
- `signal_identity`（`facts.py:1390-1397`）同样把 `source_id` 烘进 SHA256；
- `memory/scopes.py` 对 `group_participant` 的 subject_id 硬校验为**恰好 3 段**，而 `facts.py:1304` 用 `subject_id.rsplit(':', 1)[0]` 做同群前缀判定。

任何改变 speaker_id 字符串表示的做法都会让全部存量 `processed_signal_events` 失配 → 历史纠错全量重放。

### 2.2 entity_id：确定性派生，无分配器

```python
# memory/identity.py —— 纯函数，零 I/O，只 import hashlib/re 与 stable_speaker_id
ENTITY_ID_PREFIX = "ent_"
_ENTITY_ID_RE = re.compile(r"ent_[0-9a-f]{24}")     # 总长 28，**不含冒号**

def normalize_account_id(value) -> str | None:
    """唯一归一入口。就是 stable_speaker_id，不加任何额外拒收。"""
    from memory.speaker_trust import stable_speaker_id
    return stable_speaker_id(value)

def derive_entity_id(account_id: str, generation: int = 0) -> str:
    raw = f"neko.entity.v1|{account_id}|{generation}"
    return ENTITY_ID_PREFIX + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

def is_entity_id(value) -> bool:
    return _ENTITY_ID_RE.fullmatch(str(value or "")) is not None

def account_platform(account_id: str) -> str:
    return str(account_id or "").partition(":")[0]
```

**命名空间不相交是可证的，不是约定**（graft from ontology-first）：
- `is_entity_id(x)` 为真 ⇒ x 无冒号 ⇒ `stable_speaker_id(x)` 走到 `if not sep: return None`；
- `stable_speaker_id(y)` 非 None ⇒ y 至少含一个冒号 ⇒ 必不匹配 `_ENTITY_ID_RE`。

因此不需要（也**不允许**）给 `stable_speaker_id` 加保留平台词表。

**为什么确定性派生而不是 uuid4**：并发首见同一 account 自动收敛到同一 id，无需锁内分配序号；池文件丢失后可复现；迁移可离线重算；没有「先生成再持久化」的写丢失窗口。

**归一规则唯一来源**：先 `normalize_account_id(raw)` 得到 canonical，再 hash。**绝不另做大小写折叠** —— 否则 `qq:ABC` / `qq:abc` 绑成一个实体而 fact 行上仍是两个 speaker_id，D 类等值比较全线失配。`qq:ABC` 与 `qq:abc` 是两个 account、两个 entity，与 fact 行的比较规则逐字一致。

`generation` 只在 `unbind` 时递增：解绑时候选 id = `derive_entity_id(account_id, 0)`，若已被占用（原实体仍活着）或已在 `forgotten` 墓碑里，则 `generation+1` 重试直到落空位；最终 generation 存进 account 子记录。

### 2.3 存储形状（`<memory_dir>/speaker_trust.json`）

```jsonc
{
  "version": 1,
  "updated_at": "2026-08-04T11:32:11+00:00",

  // ── 迁移闸门。见 §5.1，是本设计解决「导入窗口双算」的唯一机制 ──
  "legacy_barriers": {
    "qq": {
      "source": "qq_auto_reply.business_config.speaker_trust_profiles.v1",
      "status": "pending",              // "pending" | "cleared"
      "seeded_at": "2026-08-04T10:00:00+00:00",
      "cleared_at": null,
      "chunks": 0, "accounts": 0, "skipped": 0
    }
  },

  // ── 落盘但不被信任：_load 时从 entities[*].accounts 整体重建后覆盖 ──
  "account_index": { "qq:123456": "ent_3f9a1c8b2d4e6f0a1b2c3d4e" },

  "entities": {
    "ent_3f9a1c8b2d4e6f0a1b2c3d4e": {
      "entity_id": "ent_3f9a1c8b2d4e6f0a1b2c3d4e",
      "status": "active",                       // "active" | "merged"
      "created_at": "...", "updated_at": "...",
      "accounts": {
        "qq:123456": {
          "account_id": "qq:123456",
          "generation": 0,
          "bound_at": "...",
          "adjustment": -0.08,                  // 无损求和，读取处才夹
          "message_count": 17,                  // 写入即夹到 cap(=20)
          "processed_activity_events": ["activity_ab12…"],   // 可截断环
          "processed_signal_events": ["7c9d…"],              // append-only，永不截断
          "legacy_import": {"source": "…", "at": "…"}        // 每 account 一次性幂等哨兵
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

### 2.4 关键结构决定：**账本按 account 分区，不按 entity 汇总**

这是从 ontology-first 嫁接过来的最有价值的一条（两位评审都点名要求任何方案继承），它一次性消解了三个问题：

1. **合并两份 append-only 账本怎么并** —— 不需要并。`merge` 退化成 account 子字典的**不相交搬迁**。
2. **不需要给账本行加 kind/符号** —— 因此迁移可以逐字搬运只含 id 的旧列表，`unbind` 也无损。
3. **不可能重复计分** —— `trust_event_id` 把 **target account_id** 烘进哈希，且 `account_index` 是一个函数（一个 account 同时只属于一个 entity），所以针对 account A 的 event_id 结构上不可能出现在 account B 的账本里。

> 这直接修掉了 minimal-seam 的两条硬伤：`entities[eid].legacy` 单块结构在 PR6 一开链接就整块覆盖毁账本（两位评审都判定 fatal）。本设计里 legacy 数据被 merge 进 **account 子记录**，account 是 1:1 的，永不冲突。
>
> `_merge_entities_locked` 里那句「`acc_id` 不可能已在 survivor 里」必须写成 `assert` 而不是注释，并且 `_normalize_payload` 必须**显式检测并合并**同一 account 出现在两个实体下的情形（`facts.json` 级别的手改是这个仓库承认的现实）。合并规则：`adjustment` 相加、`message_count` 取 `min(cap, 和)`、两个环各自 `dedup_keep_order` 并集，然后 log 一条 warning。

### 2.5 base / owner 的归属（显式拍板）

| 量 | 归属 | 理由 |
|---|---|---|
| `base`（权限档派生） | **account × 请求局部**，从不落盘、从不跨平台继承 | 同一实体在 QQ 是 admin、在 B 站是陌生人时，B 站段的 base 仍是 0.3 |
| `adjustment` / `activity`（挣来的） | **entity 全局**，跨平台跨群跨角色共享 | 「这个人靠不靠谱」是人身属性 |
| `speaker_is_owner` | **account 局部**的授权位，**绝不由 entity 推导** | 封死「最弱的一个平台接入变成整个 owner 授权面的攻击面」 |
| 自证禁令的判定 | **entity 级**（PR7 起用 `same_entity`） | 堵死「主人用 A 号给自己 B 号说过的话背书」 |

### 2.6 解析 API

```python
# memory/trust_store.py
class TrustSnapshot:                    # 冻结的只读视图，写者从不原地改已发布对象
    def entity_of(self, account_id: str) -> str | None: ...
    def same_entity(self, a: str, b: str) -> bool: ...
    #   契约：**未加载 ⇒ 一律 False（保守）**。两个 account 都已注册且解析到
    #   同一 entity 才 True；任一未知 → False。绝不 auto-vivify。
    def barrier_pending(self, platform: str) -> bool: ...
    def trust_inputs(self, account_id: str) -> tuple[float, int]:   # (adjustment_sum, activity_count_sum)
        ...  # entity 下所有 account 子记录的求和；未注册 → (0.0, 0)
    def resolve_trust(self, account_id: str | None, *,
                      tier: str | None = None,
                      base: float | None = None) -> float | None: ...

def trust_snapshot() -> TrustSnapshot: ...   # 无锁：一次原子属性读
```

**`resolve_trust` 的返回值语义（必须写死，否则会静默改变仲裁行为）：**

```
account_id is None                       → None
barrier_pending(platform(account_id))    → None
tier is None and base is None            → None          ← 关键
tier is not None   → base_score = SPEAKER_TRUST_BY_PERMISSION_LEVEL[tier]   (Literal 已保证合法)
base is not None   → base_score = clamp(base, 0.0, SPEAKER_TRUST_MAX_REPORTED_BASE)
                   → clamp01(base_score
                             + clamp(adjustment_sum, ±SPEAKER_TRUST_ADJUSTMENT_LIMIT)
                             + min(SPEAKER_TRUST_ACTIVITY_MAX_BONUS,
                                   min(cap, activity_count_sum) * SPEAKER_TRUST_ACTIVITY_WEIGHT))
```

返回 `None` ⇒ handler **不写 `segment["speaker_trust"]` 键**。这保住今天的弃权语义：`preferred_by_trust`（`speaker_trust.py:156-170`）在任一侧非有限时返回 `None`（弃权）；今天群 digest 走单发形状不带 `speaker_trust`，一大批 fact 行没有这个字段。如果无来源时回落 0.5，等于给这些行盖上有限值 → 仲裁从弃权变成生效。**这是 concurrency 评审第 2 条，采纳。**

`resolve_trust` **只读，绝不 auto-vivify**。auto-vivify 只发生在写路径的临界区内。

### 2.7 生命周期操作（PR7）

全部是 `TrustMutator`，一次 `to_thread + 锁` = 一次原子文件写。

```python
def _resolve_entity_locked(state, entity_id, *, depth=8) -> str | None
    # 沿 merged_into 跳转 + 路径压缩；超深返回 None（拒绝，不猜）
def _resolve_or_create_account_locked(state, account_id, *, now) -> tuple[str, bool]
    # generation 从 0 起找空 seed 位（跳过 entities 与 forgotten 里已占用的）
def _bind_locked(state, account_id, entity_id)
    # account 已属另一实体 ⇒ 等价于 _merge_entities_locked（账本自动跟着走）
def _unbind_locked(state, account_id) -> str
    # account 子记录**整条**移出，generation+1 建新实体，两个环原样带走
def _merge_entities_locked(state, a, b, *, now) -> str
    # 存活者 = sorted 按 (created_at, entity_id) → 两个方向的并发请求收敛到同一结果
    # 幂等（a==b 直接返回）、可交换、可结合
def _forget_entity_locked(state, entity_id) -> dict
```

合并的性质（可证）：**幂等 + 可交换 + 可结合 + 账本零损**。`adjustment` 的聚合是纯加法且只在读取处夹，所以合并前后 `effective_trust` 的变化只来自「求和范围变大」，不来自任何截断顺序。

---

## 3. 服务端 trust 池

### 3.1 落点与模块

- **文件**：`<memory_dir>/speaker_trust.json`（常量 `SPEAKER_TRUST_POOL_FILENAME`）。理由见 §1.3(b)。
- **模块**：`memory/trust_store.py` + `memory/identity.py`。放 `memory/` 而非 `app/memory_server/`：已核实 `memory/` 从不 `from app.` import（单向依赖），这样 `memory/facts.py` 的自证禁令、以及未来三个仲裁消费点都能直接 import。
- **隐式契约（必须在文件头留注释钉住）**：`main_logic/facts_sync/sync_worker.py` 的角色目录枚举、`app/memory_server/outbox_infra.py` 的启动扫描、`memory/__init__.py` 的 `migrate_to_character_dirs` 都在遍历 memory_dir 子项并把每个 entry 当角色名试探。它们都靠 `is_dir()` / 固定文件名模式过滤才不会误伤根级文件 —— 这层保护是隐式的。
- **将来分片只能是 `speaker_trust.<n>.json` 这种平铺文件名，绝不能开子目录。**

### 3.2 内存结构与单写者

**模块级单例**（gates.py 形状），不挂 runtime 全局 ⇒ `reload_memory_components()`（`runtime.py:247+`）天然不碰它，不需要写 `_share_trust_write_state`（对照 `runtime.py:211-217` 的 `_share_fact_store_write_state`）。

```python
# memory/trust_store.py
_POOL: dict = _empty_pool()          # 已发布的只读快照；写者只 rebind，绝不原地改
_SNAPSHOT: TrustSnapshot = TrustSnapshot(_POOL)
_pool_lock = threading.Lock()        # 不是 asyncio.Lock
_load_failed: bool = False

TrustMutator = Callable[[dict], tuple[bool, Any]]   # **同步**！锁内写不出 await
```

**为什么必须是 `threading.Lock`**（`gates.py:98-113` 的三条论证逐条成立，原样继承）：
1. 这是本仓库写盘的既有正确范式（`memory/event_log.py` 的 `record_and_save`）；
2. 模块级 `asyncio.Lock` 一旦真争用就绑定当时的 loop，而 `pytest.ini` 是 `asyncio_mode=auto` + function-scope loop，第二个有争用的用例直接 RuntimeError 且锁残留成已持有；
3. `threading.Lock` 能把 `json.dumps` 一起关进临界区（`atomic_write_json` 是先 dumps 再写）。

**mutator 强制同步**：语法层杜绝「锁内 await / 锁内取别的锁」。**故意不用 RLock**（嵌套 RMW 的内层落盘会把外层改了一半的状态写进磁盘）。

**读**：`snap = trust_snapshot()` 一次原子属性读，之后随便遍历，**不加锁**。比 gates 的 `_maint_view` 更强：写者从不原地 mutate 已发布对象，所以连 defensive copy 都不需要。

### 3.3 唯一写入口

```python
@dataclass(frozen=True)
class ActivityEvent:
    id: str
    count: int

@dataclass(frozen=True)
class TrustMutation:
    speaker_account_id: str | None        # 段发言人 → **活跃度轴**
    activity_events: tuple[ActivityEvent, ...]
    signal_events: tuple[dict, ...]       # 每条自带 speaker_id = **被纠错者**

@dataclass(frozen=True)
class TrustApplyResult:
    persisted: bool
    activity_applied: int
    signals_applied: int
    signals_deferred: int                 # 目标平台闸门未开而未结算的条数

async def aapply_trust_mutations(muts: Sequence[TrustMutation]) -> TrustApplyResult:
    return await asyncio.to_thread(_apply_trust_mutations_locked, tuple(muts))
```

> **信号路由（修 minimal-seam 的规格级硬伤）**：`signal_events` 里每条事件的 `speaker_id` 是 **target_id（被纠错者）**，不是段发言人 —— 见 `memory/facts.py:1407-1417` 的事件体 `'speaker_id': target_id`。今天插件正是靠 `permission.py:315-323` 逐条 partition `item["speaker_id"]` 路由到目标 QQ 的 profile。服务端必须做同一件事：**活跃度按 `speaker_account_id` 记，信号按每条事件自己的 target 记。两条独立的轴，绝不共用一个账号。** 这条要写成 `_apply_trust_mutations_locked` 的首行注释，并配一条专门的回归测试（见 §8）。

```python
def _apply_trust_mutations_locked(muts) -> TrustApplyResult:
    with _pool_lock:
        if _load_failed:
            return TrustApplyResult(False, 0, 0, 0)     # 读失败绝不覆盖磁盘
        new_pool = _copy_on_write(_POOL, muts)          # 见 §3.5 的容器清单
        a = s = deferred = 0
        for m in muts:
            # ① 活跃度轴：段发言人
            if m.speaker_account_id and m.activity_events:
                if not _barrier_pending(new_pool, account_platform(m.speaker_account_id)):
                    rec = _account_record_locked(new_pool, m.speaker_account_id, create=True)
                    for ev in m.activity_events:
                        a += int(record_activity(rec, ev.count, ev.id))
            # ② 信号轴：逐条事件按 target 路由
            for ev in m.signal_events:
                target = normalize_account_id(ev.get("speaker_id"))
                if target is None:
                    continue
                if _barrier_pending(new_pool, account_platform(target)):
                    deferred += 1
                    continue
                # auto-vivify：target 来自服务端自己写过的 durable fact 行，
                # 不是攻击者可控输入。与今天 permission.py:323 的 setdefault 同语义。
                rec = _account_record_locked(new_pool, target, create=True)
                s += int(apply_signal_event(rec, ev))
        if not (a or s):
            return TrustApplyResult(True, 0, 0, deferred)
        try:
            atomic_write_json(pool_path(), new_pool, indent=2, ensure_ascii=False)
        except BaseException as exc:
            logger.warning(f"[Trust] 池写盘失败，本次演化未落盘: {exc}")
            return TrustApplyResult(False, 0, 0, deferred)   # **绝不抛**：facts 已 durable
        _publish(new_pool)                                    # 落盘成功后才 rebind
        return TrustApplyResult(True, a, s, deferred)
```

三条必须写进注释的规则：

1. **不调 `assert_cloudsave_writable`**（修 concurrency 评审第 5 条）。`fence.py` 的那个函数是纯断言（TOCTOU），而 `cloudsave_writable_transaction` 才是跨最终文件 mutation 保持云闸关闭的形态。但根级池文件既不在 manifest 也不被 import 删除，**这里根本不需要这道闸**。留着会让后人以为有保护。在这里放一行注释说明为什么故意不加。
2. **绝不 5xx / 绝不 409**（修 minimal-seam concurrency 评审第 4 条）。池写发生在 fact 已提交之后；抛 `MaintenanceModeError` 会被 `runtime.py:137-139` 翻成 409、把整个响应体（每段 status/fact_ids/reconciled）打掉，调用方只能整批重试、重跑一次 LLM 抽取。post-commit 的任何失败都降级成 `persisted=False` 逐段回传。
3. **`_load_failed` 一票否决**。读盘失败时后续所有写直接返回 `persisted=False`，让调用方保留重试 —— 否则一次读失败会把整份演化覆盖成空。

### 3.4 加载

```python
async def aload_pool() -> None:
    path = pool_path()
    if not await asyncio.to_thread(os.path.exists, path):
        await asyncio.to_thread(_rebind_locked, _seed_pool())    # 含 legacy_barriers 种子
        return
    try:
        # 必须是 tolerating 版：read_json_async 是裸 read_json（file_utils.py:809-814），
        # Windows 上撞并发 os.replace 会 PermissionError(WinError 5/32)，被上层吞成
        # 「读不出来→回落默认 trust」= 静默丢演化。
        # 必须在 worker 线程里调：running_on_event_loop() 时退避不完整（file_utils.py:727-756）。
        data = await asyncio.to_thread(read_json_tolerating_replace, path)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        # UnicodeDecodeError 必须单列（gates.py:213 的教训）：它是 ValueError 子类，
        # 既不是 JSONDecodeError 也不是 OSError。
        logger.error(f"[Trust] 池加载失败，本进程进入只读降级: {exc}")
        _set_load_failed(True)                                    # 后续写一律 persisted=False
        return
    await asyncio.to_thread(_rebind_locked, _normalize_pool(data))
```

`_seed_pool()` 把 `SPEAKER_TRUST_LEGACY_BARRIERS`（见 §5.1）写成 `status="pending"` 的闸门。**池文件缺失/新建时闸门必然是 pending**，这是「池丢失 → 插件重推 → 无损恢复」链条的第一环。

挂载点：`app/memory_server/runtime.py:687` 的 `await gates._aload_maint_state()` 之后加一行 `await trust_store.aload_pool()`。位置必须在 cloudsave bootstrap 与 `ensure_memory_directory` 之后。

### 3.5 Copy-on-write 的容器清单（修 concurrency 评审第 8 条）

「落盘失败 ⇒ 内存与磁盘不分叉」只在**每一个被改动的容器都新建对象**时才成立。`_copy_on_write` 必须显式新建（不是共享引用）以下每一层：

1. 根 dict
2. `entities` dict
3. `account_index` dict
4. `legacy_barriers` dict（以及被改动的那个 platform 子 dict）
5. `forgotten` dict
6. **每个被碰到的 entity 记录 dict**
7. 该 entity 的 `accounts` dict
8. **每个被碰到的 account 记录 dict**
9. 该 account 的 `processed_activity_events` / `processed_signal_events` **两个 list**

未被碰到的 entity 记录可以共享引用（写者从不原地改它们）。这条必须配一条测试：auto-vivify 一个新 account 后让写盘失败，断言已发布快照的 `account_index` 里**没有**那条 alias。

### 3.6 锁序与失败回滚

- **trust 池锁是叶子锁**：持有它时绝不取任何别的锁、绝不 await、**绝不调用 FactStore 的任何方法**。
- FactStore 的既有锁序 `persist → fact`（`memory/facts.py`）完全不受影响：`aapply_trust_mutations` 在 handler 里是所有 FactStore 调用**之后**的最后一步，两把锁不重叠。
- `atomic_write_json`（`utils/file_utils.py:708+`）：同目录 mkstemp → write → flush → fsync → `_replace_with_busy_retry`；任何 `BaseException` 都删 tmp 后重抛，目标文件保持旧内容。**要么全落要么全不落。**
- 落盘失败 ⇒ `new_pool` 直接丢弃、`_POOL` 不 rebind ⇒ 内存与磁盘不分叉，无需任何补偿逻辑。

### 3.7 角色态问题的正面回答

- 存储：路径无 `lanlan_name` ⇒ 天然跨角色，与 `memory/__init__.py:64` 的 `ensure_character_dir` 体系正交。
- 写入点在角色路由 `/internal/memory/{lanlan_name}/scoped_history` 的 handler 里，但落盘目标不带角色。这是**有意的跨角色副作用**，必须在两处写死注释（handler 内 + `trust_store` 模块 docstring）：

  > 本次写入的目标文件不含 `lanlan_name`。信赖度是「这个人靠不靠谱」的人身属性，不是「他跟哪个角色的关系」—— 跨角色共享是产品拍板的决定，不是 bug。

- **`signal_identity` 里的 `name`（lanlan_name）保留不动**（`facts.py:1391`）。显式语义：**owner 对角色 A 说的话和对角色 B 说的同一句话是两次独立观察，各产生一个 event_id、各扣一次分。** 这既是今天的既有行为（插件的单一池已经如此），也避免动哈希公式导致存量账本全线失配。

### 3.8 修复：`areconcile_from_facts` 的定位与临界区形状

**定位降级为「灾备工具」，不再是正确性依赖。** 正确性由 §4.5 的 `trust.persisted` 回传 + 插件保留重试保证。

**为什么它曾经是不安全的（concurrency 硬伤 2）**：`routes.py:1630-1678` 的注释明写 owner 信号对**抽取失败的段也照样 apersist 落到 fact 行上**，只在响应里被 `if result.get('status')=='ok' else []` 藏起来（:1825-1828）。所以 fact 行上的 `_speaker_trust_signal_events` 天然混着两类：(a) 崩溃/丢响应导致「该折没折」的，(b) 段失败导致「故意还不该折」的。而事件记录里没有「已折叠」位，两类不可区分。

**解法：立不变量 P1，从源头消灭这个二义性。**

> **不变量 P1**：任何一条 durable 挂到 fact 行上的 owner signal，都会在**同一次请求内**（或该请求的重试中）被折叠进池，且按 event_id 幂等。服务端**不复现**插件的 `chronological_predecessor_failed` 扣留语义，也**不按段 status 扣留 signal**。

三条理由（写进代码注释）：
1. `adjustment` 是**可交换求和**（`permission.py:338-344` 明写逐次夹是非交换的，所以从不逐次夹），结算顺序不影响最终值。
2. 活跃度改成**逐条消息 id** 后（§4.2），扣留的唯一动机（「重试时桶变大 → token 变 → 重复计数」）结构性消失。
3. `speaker_trust` stamp 一律取**请求开始时的池快照**（§4.4），与结算顺序无关。

有了 P1，`areconcile_from_facts` 的判据变成无歧义的：**fact 行上有、池里没有 ⇒ 那次折叠丢了**。

**临界区形状必须写死（修 concurrency 评审第 1 条）：**

```python
async def areconcile_from_facts(fact_store, character_names) -> dict:
    """灾备用：从 fact 行的 durable 事件账本重建缺失的折叠。按 event_id 幂等。

    锁纪律：扫描与 delta 构造**全程在锁外**（要调 FactStore，而池锁是叶子锁，
    持锁时绝不碰 FactStore）；只把「按 event_id 判重 + 折叠 + 落盘」关进
    to_thread + _pool_lock，且**锁内重读 _POOL**，绝不用扫描前抓的旧快照
    —— 否则会整片覆盖掉扫描期间 handler 的演化（教科书级丢更新）。
    分块提交：每 chunk 一次临界区一次落盘。
    """
```

**门禁**：`legacy_barriers` 里还有 `status == "pending"` 的 platform 时，属于该平台的 account 一律**跳过并计数**（与写路径同一把闸门）—— 防止「reconcile 先把事件 E 折进 adjustment，随后 legacy 导入又把已含 E 的 adjustment 加上去」。

**触发**：v1 **只有手动端点** `POST /internal/trust/reconcile_from_facts`。不做自动触发（理由：扫描成本未知；`ascoped_forget` 会把携带 `_speaker_trust_signal_events` 的 fact 行一起删掉，所以它本来就不是完备的自愈器）。启动时若检测到「池文件缺失」则只 log 一行提示，不自动跑。

---

## 4. wire 协议变更

### 4.1 请求字段（`ScopedHistorySegment` 与 `ScopedHistoryRequest` 同形改动）

| 字段 | 类型 / 约束 | 变更 | 说明 |
|---|---|---|---|
| `input_history` | `str` | 不变 | |
| `subject` | `MemorySubjectRequest` | 不变 | |
| `speaker_label` | `str`（段内必填 ≤64） | 不变 | |
| `speaker_id` | `str \| None`，过 `stable_speaker_id`，非法 → 422 | 不变 | 仍是 `platform:actor`，仍原样盖到 fact 上 |
| `trust_signal_excluded_fact_identities` | `list[tuple[str,str,str,str]]` | 不变 | 服务端排除逻辑权威化，插件仍必须发 |
| `display_name` | `str \| None` | 不变 | |
| `speaker_trust` | `float \| None, ge=0, le=1` | **PR2–PR5 保留（legacy 通道）；PR6 起「出现即 422」** | 见 §4.6 |
| **`speaker_tier`**（新） | `Literal["admin","trusted","normal","none"] \| None` | 新增 | Pydantic Literal ⇒ 拼错的 `"Admin"` 直接 422，**不会**走 `permission.py:264` 那种 `.get(level, DEFAULT)` 的 silent-default |
| **`speaker_base_trust`**（新） | `float \| None, Field(ge=0.0, le=1.0)`，服务端再夹到 `SPEAKER_TRUST_MAX_REPORTED_BASE=0.8` | 新增 | 给没有四档阶梯的平台（弹幕 guard_level 之类）。上界 0.8 < admin 的 1.0，封死「把 guard_level 映射成 owner 级仲裁权」 |
| **`speaker_activity_events`**（新） | `list[ActivityEvent]`，`0..SCOPED_HISTORY_BATCH_MAX_MESSAGES`(=200) | 新增 | 逐条消息的幂等活跃度事件 |
| `speaker_is_owner` | `bool = False` | **语义收紧** | `True` 时必须 `speaker_tier == "admin"`，否则 422 |

```python
class ActivityEvent(BaseModel):
    id: str = Field(min_length=8, max_length=96, pattern=r"[A-Za-z0-9_.:-]+")
    count: int = Field(default=1, ge=1, le=1000)
```

> `pattern` 不含 `|`、不含空白、限长 96 —— 这直接堵掉 minimal-seam 评审第 3 条指出的坑：participant 路径若发未哈希的 `participant:{her_name}:{epoch}:{last}:{next}`，一个名字带空格的角色会让**整条 scoped_history 请求 422**，卡死的不是 trust 而是整个私聊记忆写入。插件两条路径都必须发已哈希的 `activity_<24hex>`（§6.2）。

### 4.2 为什么活跃度改成逐条消息（graft from minimal-seam）

今天的活跃度事件是**批级**的（`session_memory_service.py:2033-2050` 的 `_group_activity_identity`）。批重试时消息变多 → identity 变 → 已回执的前缀被重复计数，插件为此发明了跨三层的 `cancelled.speaker_trust_persisted` 协议（产出点 `settings_service.py:774`，消费点 `session_memory_service.py:1010` / `:1644-1657` 的精确前缀剔除）。

改成逐条消息后，服务端按 id 去重、只对未见过的 id 加 count，**放大重试天然无害**，整套前缀消费协议直接删除。

- **群路径**：每条消息已有 `_speaker_activity_id`（`session_memory_service.py:645-647`，`sha256(f"{sender_id}|{time_ns}|{len}|{text}")[:24]`），且 bucket 内消息全是 `role=="user"`（:648-657），所以 `count=1/条` 与今天的 `len(observation_texts(messages))` 逐字等价，不产生活跃度膨胀。
- **私聊 participant 路径**：无逐条戳，仍发**一条**批级条目，`count = len(observation_texts(msgs))`，epoch 轮换（`:1000`、`:2357-2371`）原样保留。

**诚实记录一处升级代价（三份方案都描述得不准确）**：今天的 event_id 是 `"activity_" + sha256(f"qq:{sender_id}|{stable_activity}")[:24]`，其中 `stable_activity` 是**批级 identity**（`group:{gid}:{level}:{joined ids}`）。改成逐条后 `stable_activity` 变成单条 `_speaker_activity_id` ⇒ **新 id 与存量 128 环里的旧 id 不同命名空间，升级后第一批消息会各计一次活跃度**。上界受 `message_count` cap（= `ceil(0.02/0.001)` = 20）与 `SPEAKER_TRUST_ACTIVITY_MAX_BONUS = 0.02` 双重约束，远小于仲裁 margin 0.15；且已到 cap 的老用户完全无感（见 §4.7 的 no-op 优化）。显式接受并写进 PR 描述。

### 4.3 新增校验（全部 422，逐条要测试）

1. `segments` 与 `speaker_tier` / `speaker_base_trust` / `speaker_activity_events` **也互斥** —— 必须补进 `routes.py:1443-1455` 那个 `if`，否则 `segments + tier` 会漏过去。
2. `speaker_tier` 与 `speaker_base_trust` 同时给 → `422 "speaker_tier and speaker_base_trust are exclusive"`。
3. `speaker_trust`（legacy）与 `speaker_tier`/`speaker_base_trust` 同时给 → `422 "speaker_trust is exclusive with the server-derived trust source"`。
4. 给了 trust 来源但 `speaker_id` 缺失/非法 → `422 "trust source requires a valid speaker_id"`（对齐既有口径「没有发言人就没有可信赖的对象」，`routes.py:1237-1241`）。
5. `speaker_activity_events` 非空但两个 trust 来源都缺 → `422 "speaker_activity_events requires a trust source"`。
6. `speaker_activity_events` 内 id 重复 → **去重后处理，不报错**（同批同文本合法）。
7. `speaker_is_owner=True` 且 `speaker_tier != "admin"` → `422 "speaker_is_owner requires the admin tier"`。**新硬化**：今天服务端完全不校验，写进 wire 后任何平台都不能靠昵称匹配伪造 owner 通道。
8. 既有的段数 / 每段消息数 / 总消息数 / label 长度与中和规则**全部不动**。

### 4.4 服务端取值时机（时序拍板）

**规则：本请求盖到 fact 上的 `speaker_trust` = 本请求事件生效之前的池快照。**

handler 起始处、**任何 FactStore 调用之前**，取**一次** `trust_snapshot()`，为每段解析：

```python
snap = trust_store.trust_snapshot()          # 整个请求只取一次
for seg in parsed:
    resolved = snap.resolve_trust(seg["speaker_id"],
                                  tier=seg.get("speaker_tier"),
                                  base=seg.get("speaker_base_trust"))
    if resolved is not None:
        seg["speaker_trust"] = resolved      # ← 唯一注入点；key 名故意不变
```

`seg["speaker_trust"]` 的 key 名**故意保持不变** ⇒ `FactStore._speaker_provenance_of`（`memory/facts.py:3095-3116`）、`extract_facts` / `extract_facts_batch` 的签名与内部逻辑**一行都不用改**。

三条理由（写进注释）：
1. 与今天等价（插件在 POST 前算 trust、POST 后应用事件）；
2. 否则同一批内「先扣 owner 对 X 的纠错、再给 X 自己的 fact 盖分」会让结果依赖段序；
3. 让 stamp 成为「请求开始时池状态」的纯函数，使整条 handler retry-safe。

`test_member_flush_refreshes_trust_after_owner_request_boundary` / `test_participant_digest_refreshes_trust_between_batches` 的语义完好保留 —— 刷新发生在**请求之间**，只是改由服务端在每次 handler 入口刷新。**必须补一条显式回归：同一批内 owner 的纠错不得影响同批被纠错者的 stamp。**

### 4.5 响应

删除 `trust_events`（单发 `routes.py:1414`、批 `:1825-1828`），新增 `trust` 块（单发在顶层，批在每段对象内）：

```jsonc
"trust": {
  "resolved": 0.86,          // float|null：本段实际盖到 fact 上的分。null = 未盖键
  "persisted": true,         // bool|null：池落盘结果。null = 本段没有 trust 来源
  "signals_applied": 1,
  "activity_applied": 3,
  "gated": null              // null | "legacy_import_pending"
}
```

**调用方消费规则（取消协议的替代品）：**

| 段 status | `trust.persisted` | 动作 |
|---|---|---|
| `ok` | `true` / `null` / `gated` 非空 | **pop 该段消息**（正常推进） |
| `ok` | `false` | **保留该段重试**（等价于今天 `_apply_speaker_trust_update` 抛 `RuntimeError`） |
| `ok` 但前序段失败 | — | 保留（今天行为，`session_memory_service.py:1563-1582` 不动） |
| `failed` | — | 保留（今天行为） |
| 响应丢失 / `CancelledError` / 超时 | — | 保留重试（活跃度按逐条 id 幂等、信号按 durable event id 幂等） |

> **为什么必须把 `persisted:false` 回传给插件（修 concurrency 评审第 4 条 + ontology-first 的两条硬伤）**：已核实 `facts.py:1327-1359` 的重放环由 `recorded.get('observation_id') == observation_id` 门控，而 `observation_id = trust_observation_id(text)` 是**当前请求里某条主人原文**的哈希（`speaker_trust.py:523-526`）。它是**重试语义，不是时间语义** —— 只在插件重发同一批原文时触发。今天的 at-least-once 完全依赖「持久化失败 → 插件 raise → 保留桶 → 重发同一批 → 重放命中」。如果服务端一律返回 200 让插件 pop，这条链就断了，一次磁盘抖动 = 一条 ±0.04/0.08 的主人纠错**静默永久丢失**。所以 `trust.persisted` 必须回传、必须驱动保留重试。这也是把 reconcile 从「正确性依赖」降级成「灾备工具」的前提。

`cancelled.speaker_trust_persisted` 这条跨三层的隐式协议随之整体消失。取而代之的契约一句话：**HTTP 200 且 `trust.persisted != false` ⇒ 本段的 facts 与 trust 都已 durable。**

### 4.6 兼容窗口与 fail-loud 的折中

- **PR2–PR5**：`speaker_trust`（legacy）与 `speaker_tier`/`speaker_base_trust`（新）并存，靠**互斥 422** 实现逐请求自描述的协议切换。这是 minimal-seam 的核心结构性优点，**采纳** —— 它是「每个 PR 独立可合并可回滚」这条硬要求的唯一实现方式（服务端一个二进制同时支持翻转前/翻转后的插件）。
- **PR6**：删掉 legacy 分支，`speaker_trust` 出现即 422。彻底 fail loud。已 grep 确认 QQ 插件是 `scoped_history` 的唯一调用方，且 `routes.py:1029-1031` 的注释已声明「同一 deployment 出货」这个前提。

### 4.7 handler 内的写序（保住「事实写入失败时不推进 trust」）

1. `extract_facts_batch` 之后，为每段收集 `TrustMutation`：
   - `activity_events` 只对 `result["status"] == "ok"` 的段收集（对齐今天的「只对 ok 段调 `_apply_speaker_trust_update`」）；
   - `signal_events` 收集**所有已 durable 的事件**，不按段 status 扣留（不变量 P1，§3.8）。
2. owner 信号：`aevaluate` → `apersist_speaker_trust_events`（挂到 fact 行上，durable）→ **才**进 mutation 列表。`apersist` 抛异常时走既有的 `arollback_speaker_trust_reconciliations` + 重试一次（`routes.py:1750-1777` 不变），最终仍失败则该段不产生 signal mutation。
3. `aapply_trust_mutations(...)` 是整个 handler 的**最后一次 durable 写**，且只有一次（全批合并成一次文件写）。它之前的任何失败都表现为「trust 一动不动」。

**写放大优化（修 concurrency 评审第 6 条）**：`record_activity` 在 `message_count` 已达 cap（=20）时**直接返回 False（不 dirty）**，连 event id 都不记。语义上完全等价（超过 cap 的活跃度零效果），但让绝大多数老用户的每次 flush 都不再重写整份 JSON。活跃度环用新常量 `SPEAKER_TRUST_ACTIVITY_EVENT_HISTORY_LIMIT = 256`（必须 > 单批最大消息数 200），signal 环永不截断。

### 4.8 新增端点（全部非角色路由，先例：`/internal/memory/import_external_markdown`）

| 方法 | 路径 | 用途 | PR |
|---|---|---|---|
| GET | `/internal/trust/profile?account_id=qq:123456` | 只读诊断（不返回完整账本） | PR1 |
| POST | `/internal/trust/import_legacy_profiles` | 存量迁移（分块） | PR3 |
| POST | `/internal/trust/waive_legacy_barrier` | 人工放弃某平台闸门（支持逃生阀） | PR3 |
| POST | `/internal/trust/reconcile_from_facts` | 灾备重建（手动） | PR3 |
| POST | `/internal/identity/accounts/bind` / `unbind` | 账号绑定/解绑 | PR7 |
| POST | `/internal/identity/entities/merge` / `forget` | 实体合并/遗忘 | PR7 |

**全部不进 `_STORAGE_LIMITED_MODE_ALLOWED_PATHS`**（`runtime.py:77-82`）⇒ 运行态就绪前一律 409 `storage_startup_blocked`。调用方必须能重试，且**绝不能把 409 当成「这个用户没有 trust」**。

---

## 5. 存量数据迁移

### 5.1 迁移闸门（本节是整套设计的支点）

**问题**（migration 评审对第 1 名的硬伤 1）：门禁只上在 reconcile 上、没上在写路径上。在「服务端已就绪 / 池尚未导入」的窗口里，owner 复述同一句话触发 `facts.py:1327-1359` 的重放环，重发**早已在 legacy 账本里的** event_id；服务端对空 ledger 判定为新事件 → 扣一次；随后导入把已含这一次的 legacy adjustment 加上去 → 同一 event 计两次。`SPEAKER_TRUST_CORRECTION_DELTA = 0.08`，双记 0.16 **单笔就越过 `SPEAKER_TRUST_ARBITRATION_MARGIN = 0.15`**。而环里只存 id 不存 kind，事后无法反算 —— 不可修复。

**解法：一把闸门，管住全部三条路径。**

```python
# config/memory_settings.py
SPEAKER_TRUST_LEGACY_BARRIERS = {
    "qq": "qq_auto_reply.business_config.speaker_trust_profiles.v1",
}
```

池首次创建时把它们种成 `status="pending"`。**pending 期间，属于该 platform 的 account：**

| 路径 | 行为 |
|---|---|
| `resolve_trust` | 返回 `None` ⇒ **不盖 `speaker_trust` 键** ⇒ 仲裁弃权（= PR #2639 之前的行为） |
| `aapply_trust_mutations` 活跃度轴 | 跳过 |
| `aapply_trust_mutations` 信号轴 | 跳过并计入 `signals_deferred` |
| `areconcile_from_facts` | 跳过该平台账号并计数 |

**双重门禁（纵深防御）**：
- **插件侧**：`_trust_ready: asyncio.Event`。迁移推送全部成功前，插件**根本不发** `speaker_tier` / `speaker_activity_events`。
- **服务端侧**：闸门本身。若插件违反自己的门（bug）而在 pending 期发了 tier，响应回 `trust.gated = "legacy_import_pending"`，插件 log 一条 warning。

**这一把闸门同时修掉了**：导入窗口双算、reconcile 与导入的双算、以及「非 QQ 触发的 scoped_history 打开窗口」。

**窗口内的代价（显式接受）**：这段时间写入的 fact 行不带 trust 戳，那些行的仲裁永久弃权；activity 不计；owner 信号已 durable 但未折叠（闸门开后需 owner 复述或手动 reconcile 才补上）。窗口长度 = 插件启动后到导入成功的一个 HTTP 往返，实践中是秒级。这是「有界、可恢复的弃权」换掉「无界、不可修复的双算」。

**逃生阀**：`POST /internal/trust/waive_legacy_barrier {"platform": "qq"}`（人工放弃）。若用户根本没装 QQ 插件，闸门永久 pending 也无害（没有 qq 流量）。

### 5.2 数据源与目标

- 源：`business_config.json` 的 `speaker_trust_profiles`，key 是**裸 QQ 号**字符串（`permission.py:69` 的 `_normalize_qq` 只做 `strip()`）。
- 目标：池的 `entities[eid].accounts["qq:<裸号>"]`。
- 归一唯一入口：`normalize_account_id(f"qq:{str(key).strip()}")`。与运行期请求路径用同一个函数 ⇒ 不可能出现「同一人在两个 key 下各攒一份」。

**绝不让迁移脚本直接写目标文件**（单写者纪律的前提）。迁移走 HTTP，由 memory_server 的 handler 在同一把 `_pool_lock` 里完成。

### 5.3 端点

```
POST /internal/trust/import_legacy_profiles
{
  "source": "qq_auto_reply.business_config.speaker_trust_profiles.v1",
  "platform": "qq",
  "chunk_index": 0,
  "final": true,
  "profiles": { "123456": {"adjustment": -0.08, "message_count": 12,
                           "processed_activity_events": [...],
                           "processed_signal_events": [...]} }
}
```

**关键：`profiles` 是 `dict[str, Any]`，不是严格 Pydantic 子模型（修 migration 评审硬伤 2 的同源问题）。** legacy `_normalize_speaker_profile`（`permission.py:176-221`）是**逐条容错**的；把它降级成全有全无会让一条脏 profile 让整个请求 422，而 422 会让迁移永久卡死。服务端逐条归一，非法条目进 `skipped` 数组，**永不 422 整批**。

约束：
- `platform`：`re.fullmatch(r"[A-Za-z0-9_.-]+")`；
- `chunk` 上限 `SPEAKER_TRUST_LEGACY_IMPORT_CHUNK_MAX = 500` 条，越界 422（分块由插件控制，是契约 bug）；
- 单条 profile 的 `processed_signal_events` 不设上限（append-only 硬约束）。

**服务端语义（一个临界区、一次原子写）：**

```python
def _import_locked(pool):
    now, imported, skipped = _now_iso(), [], []
    for bare_key, raw in profiles.items():
        account_id = normalize_account_id(f"{platform}:{str(bare_key).strip()}")
        if account_id is None or not isinstance(raw, dict):
            skipped.append({"key": str(bare_key)[:64], "reason": "invalid_account_id"}); continue
        rec = _account_record_locked(pool, account_id, create=True)
        if (rec.get("legacy_import") or {}).get("source") == source:
            continue                                    # 每 account 一次性哨兵，幂等
        legacy = normalize_legacy_profile(raw)          # 逐条容错，与 permission.py:176-221 同口径
        # ── 加法合并，绝不覆盖 ──
        rec["adjustment"] += legacy["adjustment"]                       # 不 clamp（交换律）
        rec["message_count"] = min(cap, rec["message_count"] + legacy["message_count"])
        # 两个环用**两段独立代码**处理，绝不合并成一个循环
        rec["processed_signal_events"] = dedup_keep_order(
            rec["processed_signal_events"] + legacy["processed_signal_events"])      # 不排序不截断
        rec["processed_activity_events"] = dedup_keep_order(
            rec["processed_activity_events"] + legacy["processed_activity_events"]
        )[-SPEAKER_TRUST_ACTIVITY_EVENT_HISTORY_LIMIT:]
        rec["legacy_import"] = {"source": source, "at": now}
        imported.append(account_id)
    barrier = pool["legacy_barriers"].setdefault(platform, {})
    barrier["accounts"] = barrier.get("accounts", 0) + len(imported)
    barrier["skipped"] = barrier.get("skipped", 0) + len(skipped)
    barrier["chunks"] = barrier.get("chunks", 0) + 1
    if final:
        barrier["status"] = "cleared"; barrier["cleared_at"] = now      # ← 闸门在这里开
    return True, {"imported": imported, "skipped": skipped,
                  "barrier": barrier["status"]}
```

**为什么加法合并是安全的**：因为闸门保证了「导入之前该平台从未有过服务端演化」。这是 §5.1 那把闸门存在的全部理由。

**原子性**：`adjustment` 与两个环在同一次 `atomic_write_json` 里落地，不存在「分两步写导致 adjustment 翻倍且不可事后区分」。

### 5.4 插件侧触发：每次启动、后台、无限重试、**永不删源键**

**这是修 migration 评审硬伤 2（池丢失 = 永久静默死锁）的核心。** 原方案把「迁移完成标记」放在插件的 `business_config.json`、把「已导入标记」放在池文件里，二者无原子关系；池一旦丢失，插件看到自己的标记直接 return、永不重发，而新池的闸门永久 pending ⇒ 全员 trust 静默归零、不可自愈。

**解法（graft from minimal-seam 的 `push_..._forever`，但配服务端 marker 做幂等）：**

```python
# plugin/plugins/qq_auto_reply/settings_service.py
_MIGRATION_BACKOFF = (0, 5, 30, 120, 600)      # 秒；之后固定 1800

async def push_legacy_speaker_trust_forever(self) -> None:
    """把 business_config 里的存量 trust 池推给 memory_server（分块、幂等）。

    **永久保留**，每次 startup 都跑。翻转后插件不再演化这份快照，所以每次
    只是把同一份冻结数据重推一遍（服务端按 account 的 legacy_import 哨兵
    命中 → 跳过 → 不写盘）。这样池文件一旦丢失/损坏，下次启动自动恢复到
    迁移时刻的状态 —— 不存在跨文件双 marker 的死锁。
    """
    delays = list(_MIGRATION_BACKOFF)
    profiles = self.plugin._qq_settings.get("speaker_trust_profiles") or {}
    if not isinstance(profiles, dict):
        profiles = {}
    chunks = _chunk(profiles, SPEAKER_TRUST_LEGACY_IMPORT_CHUNK_MAX) or [{}]
    #  ↑ 全新安装（profiles 为空）也要发一个空 chunk：闸门必须被 final=true 打开
    while True:
        try:
            for i, chunk in enumerate(chunks):
                result = await self.plugin.memory_bridge.post_legacy_speaker_trust(
                    platform="qq", source=_LEGACY_SOURCE, profiles=chunk,
                    chunk_index=i, final=(i == len(chunks) - 1), timeout=30.0,
                )
                if result.get("skipped"):
                    self.plugin.logger.warning(
                        f"speaker trust 迁移跳过 {len(result['skipped'])} 个非法 key: "
                        f"{result['skipped'][:5]}"
                    )
        except Exception as exc:
            self.plugin.logger.debug(f"speaker trust 迁移待重试: {exc}")
        else:
            self.plugin.trust_ready.set()          # ← 从此才开始发 speaker_tier
            self.plugin.logger.info("speaker trust 已迁移到服务端，trust 上报已启用")
            return
        await asyncio.sleep(delays.pop(0) if delays else 1800)
```

触发点：`plugin/plugins/qq_auto_reply/__init__.py` 的 `startup`（`:284` 的 `rebuild_permission_managers` 之后），`asyncio.create_task(...)` **后台跑，不阻塞 startup**（修 migration 评审的「~2 分钟重试循环 await 在 startup 里既阻塞插件启动、又赌 memory_server 已经起来」）。`shutdown` 里 cancel 它。

**永不改名、永不删键。** 存量键 `speaker_trust_profiles` 在磁盘上原样保留；PR5 只删掉**读它做 trust 决策**的代码。`config_store.load()`（`:162`）与 `save()`（`:187-192`）改成**只读透传**（保留原值，不归一、不截断、不重建）。已核实 `load()` 是 `merged = default_config(); merged.update(payload)`、`save()` 是 `normalized = default_config(); normalized.update(dict(config))` —— 未知键天然透传，只需删掉那两处显式归一分支即可。

**「dashboard reload 复活旧池」的风险从根上消失**：不是靠删键，而是靠 `PermissionManager` 从此不再持有 trust（`rebuild_permission_managers` 想复活也无处可放）。

### 5.5 失败与回滚语义

| 情况 | 后果 | 恢复 |
|---|---|---|
| memory_server 未就绪 / 409 / 网络失败 / 5xx / 422 | 闸门保持 pending；插件不发 tier；本轮 trust 全线弃权 | 后台无限退避重试 |
| 部分 key 非法（如 uname 兜底 key） | 合法的照常导入，非法的进 `skipped` 并 warning | 人工修 key 后重推 |
| 服务端解析后落盘失败 | `persisted:false`，**内存态不 rebind**，闸门不开 | 插件重试；按 account 哨兵幂等 |
| 池文件后来丢失/损坏 | 下次启动新建空池（闸门 pending）→ 插件重推 → 恢复到迁移时刻 | **自愈**。迁移后的服务端演化丢失，需手动 `reconcile_from_facts` |
| 需要回滚整个特性 | 磁盘上 `speaker_trust_profiles` 原封未动，回滚插件代码即回到旧语义 | 会丢失迁移后的服务端演化（写进 PR 描述） |

**「导入成功但闸门未 final」**：下次启动重发，全部 chunk 命中 account 哨兵跳过、最后一个 chunk 带 `final=true` 开闸。零损失。

---

## 6. QQ 插件侧最终形态 + 新平台接入

### 6.1 删除清单（PR4/PR5，这就是维护者要的降本）

| 位置 | 删什么 |
|---|---|
| `permission.py:20-31` | `_speaker_activity_count_cap` |
| `permission.py:36, 45-53` | `__init__` 的 `speaker_trust_profiles` 形参与 hydrate 循环 |
| `permission.py:176-221` | `_normalize_speaker_profile`（含双环归一） |
| `permission.py:223-245` | `speaker_trust_profiles` / `replace_speaker_trust_profiles` |
| `permission.py:247-279` | `get_speaker_trust`（分数公式整体上移） |
| `permission.py:281-302` | `record_speaker_activity` |
| `permission.py:304-346` | `apply_speaker_trust_events`（**含 :319 那句 `if platform != "qq"` —— 平台绑定的核心**） |
| `settings_service.py:703-792` | `apply_speaker_trust_update` **整段**（双锁 + staged manager + `ensure_future` + `shield` + 二次取消循环 + before/after 回滚 + `cancelled.speaker_trust_persisted`） |
| `settings_service.py:614-618 / 619-628 / 635-638` | `_persist_business_config_locked` 里的 live/staged trust 快照体操 |
| `settings_service.py` | `_speaker_trust_write_lock` 属性与 `_staged_speaker_trust_profiles` |
| `settings_service.py:584-596, 696-701` | 各只剩 `_consent_transaction_lock` 一把锁 |
| `settings_service.py:691` | `rebuild_permission_managers` 的第二个实参 |
| `session_memory_service.py:1940-1959` | `_speaker_trust_for` |
| `session_memory_service.py:1998-2031` | `_apply_speaker_trust_update` |
| `session_memory_service.py:1004-1012` / `:1638-1661` | 两处 `cancelled.speaker_trust_persisted` 消费（含精确前缀剔除） |
| `session_memory_service.py:1595-1628` | 本地 `trust_events` 过滤（服务端已用 `trust_signal_excluded_fact_identities` 做同一件事） |
| `config_store.py:88 / :187-192` | `default_config` 的键与 `save()` 的归一分支（`load()` 的透传改成原样透传） |

净删除量：`permission.py` −170 行、`settings_service.py` −110 行、`session_memory_service.py` −70 行。**且删掉的正好是全项目并发推理最难的一段。**

### 6.2 保留 / 改造清单

**必须保留（不能误删）：**
- `permission.py` 的 QQ 名册全套，特别是 `get_permission_level`（`:138-150`）—— B 方案下插件唯一要上报的东西。
- `message_dispatcher.py` 的开放平台首用户 bootstrap 提权与收信时刻权限快照。
- `session_memory_service.py:645-657` 的三个逐消息内部戳 —— `_speaker_activity_id` 是活跃度幂等的唯一稳定来源。
- `session_memory_service.py:1563-1582` 的 `chronological_predecessor_failed` 分支（它护的是 fact 层的按序重试，与 trust 无关）。
- `:1662-1668` 的 `except BaseException: _remember_later_fact_exclusions(...)` —— **必须显式保留**，它承担的是「后序段事实排除」这个与 trust 持久化无关的第二职责（minimal-seam 评审第 6 条点名）。
- `_speaker_trust_activity_epoch` 轮换（`:1000`、`:2357-2371`）—— 服务 participant activity token 的稳定性。

**改造：**

1. **`(tier, is_owner)` 单一真相源**（修 concurrency 评审第 9 条）。今天 `_speaker_permission_level_for`（`:1961-1981`，做 `"user"→"normal"` 别名 + 四档白名单 + 大小写归一）与 `_speaker_is_owner_for`（`:1983-1996`，裸 `level == "admin"`）归一路径不对称。既然 `speaker_is_owner=True` 要变成 wire 上的硬校验（tier 必须 admin），就把两者合并：

```python
def _speaker_identity_for(self, sender_id, permission_level=None) -> tuple[str, bool]:
    """唯一真相源：返回 (canonical_tier, is_owner)。is_owner ≡ tier == 'admin'。"""
    tier = self._normalize_tier(permission_level, sender_id)   # 原 _speaker_permission_level_for 的逻辑
    return tier, tier == "admin"
```
所有调用点改走它。补一条测试枚举全部权限值，断言不存在 `owner=True and tier!="admin"`。

2. **活跃度事件产出**（两条路径都必须哈希）：
```python
_ACTIVITY_PREFIX = "activity_"
def _activity_event_id(account_id: str, stable: str) -> str:
    return _ACTIVITY_PREFIX + hashlib.sha256(
        f"{account_id}|{stable}".encode("utf-8")).hexdigest()[:24]

# 群：逐条 → [{"id": _activity_event_id(acc, msg["_speaker_activity_id"]), "count": 1}, ...]
# 私聊 participant：单条批级 →
#   [{"id": _activity_event_id(acc, f"participant:{her_name}:{epoch}:{last}:{next}"),
#     "count": len(observation_texts(msgs))}]
```
产出必然匹配 wire 的 `^[A-Za-z0-9_.:-]{8,96}$`，不受角色名含空格/CJK 影响。

3. **平台字面量收敛到 1 处**：`memory_bridge.py` 新增
```python
PLATFORM = "qq"
@staticmethod
def speaker_account_id(sender_id: object) -> str:
    return f"{QQMemoryBridge.PLATFORM}:{str(sender_id or '').strip()}"
```
三个 subject builder、`session_memory_service.py:985` 与 `:1420` 的 `f"qq:{sender_id}"`、活跃度 id 拼接全部改走它 —— 平台字面量从 6 处收敛到 1 处。

4. **`post_scoped_memory_history` / `..._batch`** 的 `speaker_trust=` 参数换成 `speaker_tier=` + `speaker_activity_events=`；「缺省字段一律不放键」的约定不变。新增 `post_legacy_speaker_trust(...)`。

5. **trust 就绪门**：所有会带 tier 的组装点前加 `if self.plugin.trust_ready.is_set():`，未就绪则整组字段都不放键。

### 6.3 新平台接入的最小接口（写进 `memory/trust_store.py` 模块 docstring）

一个平台插件要接入 trust，**只需要**在每次 `scoped_history` 请求里提供：

1. **`platform` token** —— 匹配 `[A-Za-z0-9_.-]+`，会被小写化。
2. **`speaker_id = f"{platform}:{actor}"`** —— actor 匹配 `[A-Za-z0-9_.:@-]+`，总长 ≤96。actor 必须是**平台侧稳定 id**，绝不能是昵称。
   - 警告：`bilibili_danmaku/user_profile.py` 那种 `key = str(uid) if uid > 0 else uname` 的昵称兜底会被 `stable_speaker_id` 判 None 静默丢弃，接入前必须在插件侧禁掉。
   - 警告：`neko_live` 的 bilibili uid 是裸数字无前缀，接入时统一补 `bilibili:`。
3. **base 来源，二选一**：`speaker_tier`（四档之一，有权限阶梯的平台）**或** `speaker_base_trust`（0..1，服务端夹到 0.8，无阶梯的平台如弹幕 guard_level / medal_level）。
4. **`speaker_is_owner`** —— 只能由「基于稳定 id 的显式绑定 + tier == admin」派生；服务端会 422 掉不满足的组合。
5. **`speaker_label` / `display_name`** —— 装饰性。
6. **可选 `speaker_activity_events`** —— `[{"id","count"}]`，id 在重试/重启后必须稳定；不给就不计活跃度。

**不需要**：任何存储、任何账本、任何幂等环、任何写者锁、任何事务锁、任何事件应用逻辑、任何回传处理、任何迁移。

**现实提醒**：今天没有任何现成插件能真的零成本接入 —— `bilibili_danmaku` / `neko_live` 根本没走 scoped memory；`bilibili_dm` 最接近（权限词表与 QQ 同构、uid 强制纯数字）但今天只在 admin 私信时写记忆；`wechat_integration` 把任意 `from_user_id` 无条件写进主人主记忆且无权限模型。「接入成本接近零」指的是 **trust 这一层**，不包括「先给那个平台补上 scoped memory 和权限模型」。

---

## 7. 逐文件改动计划（分 PR）

> 切分原则：PR1–PR3 全程 dormant（不改变任何可观察行为）；PR4 是唯一的行为翻转 PR，且双向可逆（回滚它，插件恢复本地池与本地演化，服务端已攒的顶层演化留在磁盘上；再次前滚时 legacy 重推会被 account 哨兵拦下，不会双算 —— 因为哨兵是按 source 记的，回滚期间插件本地新增的演化**不会**被重推进池，这是显式接受的有界损失，见 §9）。

### PR1 —— 服务端骨架（dormant）

| 文件 | 改动 |
|---|---|
| `memory/identity.py` | **新增**。纯函数零 I/O：`normalize_account_id` / `derive_entity_id` / `is_entity_id` / `account_platform` / `activity_count_cap` / `effective_trust` / `normalize_account_record` / `normalize_entity_record` / `record_activity` / `apply_signal_event` / `merge_order_key`。`permission.py:20-31 / 176-221 / 247-346` 的算术与账本纪律整体移植（**删掉 `if platform != "qq"`**） |
| `memory/trust_store.py` | **新增**。模块级 `_POOL` / `_SNAPSHOT` / `threading.Lock` / `_load_failed`；`pool_path()` / `aload_pool()` / `_publish()` / `_copy_on_write()` / `TrustSnapshot` / `trust_snapshot()` / `TrustMutation` / `TrustApplyResult` / `aapply_trust_mutations()`。模块 docstring 写死四条：唯一写者是 memory_server handler；必须是根级平铺文件（子目录会被云存档 rmtree）；非角色态 = 跨角色共享是产品决定；新平台接入六项最小接口 |
| `memory/speaker_trust.py` | **不改**（明确否决保留平台词表） |
| `config/memory_settings.py` | 新增 `SPEAKER_TRUST_POOL_FILENAME` / `SPEAKER_TRUST_MAX_REPORTED_BASE=0.8` / `SPEAKER_TRUST_PERMISSION_TIERS` / `SPEAKER_TRUST_ACTIVITY_EVENT_HISTORY_LIMIT=256` / `SPEAKER_TRUST_LEGACY_IMPORT_CHUNK_MAX=500` / `SPEAKER_TRUST_LEGACY_BARRIERS`。修正 `:256-258` 那段已过期的注释 |
| `config/__init__.py` | `:200-208` 与 `:593-601` 两处补 import 与 `__all__` |
| `app/memory_server/runtime.py` | `:687` 的 `await gates._aload_maint_state()` 之后加 `await trust_store.aload_pool()`；旁注「模块级单例，`reload_memory_components` 故意不碰它，因此无需 `_share_trust_write_state`」 |
| `app/memory_server/routes.py` | 新增 `GET /internal/trust/profile`（只读诊断，非角色路由，不进 limited-mode 白名单） |
| `tests/unit/test_speaker_identity.py` | **新增** |
| `tests/unit/test_trust_store.py` | **新增** |

### PR2 —— wire 扩展 + 服务端评分（新旧协议并存，dormant 直到 PR4）

| 文件 | 改动 |
|---|---|
| `app/memory_server/routes.py` | `ScopedHistorySegment`(:975-999) 与 `ScopedHistoryRequest`(:1002-1033) 各加 `speaker_tier` / `speaker_base_trust` / `speaker_activity_events`；新增 `ActivityEvent` 模型；`:1443-1455` 的互斥检查补进新字段；`:1467-1543` 逐段解析加 §4.3 的 8 条校验；批路径在 `extract_facts_batch`(:1594) 之前一次 `trust_snapshot()` 注入 `seg["speaker_trust"]`，在 `:1777` 之后收集 `TrustMutation` 并**一次** `aapply_trust_mutations`；单发路径同构（注入 `:1240`、应用 `:1408` 之后）；响应体 `:1409-1415` 与 `:1780-1831` 加 `trust` 块，`trust_events` **保留**（legacy 段仍返回，新协议段恒为 `[]`） |
| `tests/unit/test_speaker_trust.py` | 新增「服务端评分」一节 |
| `tests/unit/test_group_memory_scopes.py` | `:3623-3627` 旁补 `speaker_base_trust` 的越界断言；补三条互斥 422 |

### PR3 —— 迁移 + 闸门 + 灾备（dormant）

| 文件 | 改动 |
|---|---|
| `memory/trust_store.py` | 新增 `aimport_legacy_profiles()` / `awaive_legacy_barrier()` / `areconcile_from_facts()`；把闸门检查接进 `resolve_trust` 与 `_apply_trust_mutations_locked` |
| `app/memory_server/routes.py` | 新增 `POST /internal/trust/import_legacy_profiles` / `waive_legacy_barrier` / `reconcile_from_facts`（均非角色路由，均不进 limited-mode 白名单） |
| `plugin/.../memory_bridge.py` | 新增 `post_legacy_speaker_trust(*, platform, source, profiles, chunk_index, final, timeout)` |
| `plugin/.../settings_service.py` | 新增 `push_legacy_speaker_trust_forever()` |
| `plugin/.../__init__.py` | `startup`(:284 之后) `create_task` 起 pusher；新增 `self.trust_ready = asyncio.Event()`；`shutdown` cancel |
| `tests/unit/test_speaker_trust_migration.py` | **新增** |

### PR4 —— 插件翻转（唯一行为变更 PR，双向可逆）

| 文件 | 改动 |
|---|---|
| `plugin/.../session_memory_service.py` | 新增 `_speaker_identity_for` / `_activity_event_id` / `_speaker_activity_events_for`；`:976-988` 与 `:1405-1421` 改发 `speaker_tier` + `speaker_activity_events`（门控在 `trust_ready`）；`:993-1012` 与 `:1595-1661` 改成读响应 `trust.persisted`；**删除**两处 `cancelled.speaker_trust_persisted` 消费与本地 `trust_events` 过滤；`_remember_later_fact_exclusions` 与 `chronological_predecessor_failed` 分支**保留** |
| `plugin/.../memory_bridge.py` | 新增 `PLATFORM` / `speaker_account_id()`；两个 post 方法改发新字段；三个 subject builder 与两处 `f"qq:{...}"` 改走它 |
| `tests/unit/test_group_memory_scopes.py` | L11700-12620 的 15 个 flush/trust 交互用例按新契约重写 |
| `tests/unit/test_participant_memory_and_display_name.py` | `:529-600` / `:940-1170` / `:1370-1410` 改成新协议 |

### PR5 —— 插件侧删除

按 §6.1 表格删除。`config_store.py` 的 `load()`/`save()` 改成对 `speaker_trust_profiles` 只读透传（**保留键**）。`dashboard_service.py` 的 `PermissionManager` 构造去掉 trust 参数。测试：`test_speaker_trust.py` L30-219 迁到 `test_trust_store.py`；L2464-2964 删除（`test_all_settings_writers_acquire_both_transaction_locks` 改成单锁）；L2966-3074 改成断言 wire 上的 activity id 稳定性。

### PR6 —— wire 清理（fail loud）

`speaker_trust` 出现即 422；删掉服务端 legacy stamping 分支；响应删 `trust_events`；`test_group_memory_scopes.py:3623-3627` 改成打端点期望 422。

### PR7 —— 实体链接（可选，但它是实体本体的正确性闸门）

| 文件 | 改动 |
|---|---|
| `memory/trust_store.py` | `_resolve_entity_locked` / `_bind_locked` / `_unbind_locked` / `_merge_entities_locked` / `_forget_entity_locked` + 对应的 `a*` 包装 |
| `app/memory_server/routes.py` | 4 个 identity 生命周期端点 |
| `memory/facts.py` | `aevaluate_speaker_trust_events` 新增 `identity: TrustSnapshot \| None = None` 关键字参数；`:1365-1367` 改成 `if target_id is None or target_id == source_id or (identity is not None and identity.same_entity(source_id, target_id)): continue`。**默认 None 时逐字退化成今天的字符串相等**，所有既有调用点/测试不受影响。`signal_identity`(:1390-1403)、`apersist`、`arollback` 全部不动 |
| `app/memory_server/routes.py` | 两个 `aevaluate` 调用点（:1373、:1729）传入 `identity=trust_store.trust_snapshot()` |

**硬序约束（必须写进 PR7 描述）：PR7 之前绝不能存在任何非恒等 alias。** 由于 bind/merge 端点只在 PR7 存在，这条自动成立。

**其余 D 类等值比较全部不动**，每处加一行 `# entity-resolution hook` 注释（显式推迟，不是遗漏）。

---

## 8. 测试计划

### 8.1 新增：`tests/unit/test_trust_store.py`

必须覆盖的不变量（每条一个用例）：

1. **双环隔离** —— 刷 200 条活跃度不驱逐任何 signal id。
2. **signal 环 append-only** —— 不排序、不截断、不转 set；导入 5000 条后逐字保留。
3. **adjustment 求和交换律** —— 两个 mutation 的应用顺序不影响最终 `effective_trust`；且累加过程不夹、只在读取处夹。
4. **`message_count` cap no-op** —— 达到 cap 后 `record_activity` 返回 False 且**不写盘**（断言文件 mtime/写次数不变）。
5. **信号路由轴分离** —— 一个 owner 段带 3 条 signal（target 是别人），断言 owner 自己的 account 记录 `adjustment` 未变、三个 target 各变一次。**这是 minimal-seam 规格级硬伤的守卫测试。**
6. **auto-vivify** —— 对完全没在池里出现过的 target account 应用 signal，断言开户成功（对齐 `permission.py:323` 的 `setdefault`）。
7. **闸门（三条路径）** —— `barrier_pending` 时：`resolve_trust` 返回 None；活跃度不计；signal 计入 `signals_deferred` 且账本不变；`areconcile_from_facts` 跳过。
8. **`resolve_trust` 的 None 语义** —— tier 与 base 都缺 ⇒ 返回 None ⇒ handler 不写 `speaker_trust` 键。
9. **entity_id / account_id 命名空间双向不相交**（property 测试）。
10. **`same_entity` 未加载 ⇒ False**。
11. **取消安全** —— `aapply_trust_mutations` 的 task 被 cancel 后，断言文件已落盘且 `_pool_lock` 未被持有。
12. **写盘失败不分叉** —— mock `atomic_write_json` 抛，断言 `persisted=False` 且已发布快照（含 `account_index`、`legacy_barriers`、各层 dict/list）**逐层**未被污染。
13. **`_load_failed` 一票否决** —— 读盘抛 `UnicodeDecodeError` 后，任何写返回 `persisted=False` 且不写文件。
14. **Windows tolerating 读** —— 并发 `os.replace` 下 `read_json_tolerating_replace` 生效。
15. **merge 性质** —— 幂等 / 可交换（两个方向收敛到同一存活者）/ 可结合 / 账本逐位不变 / merged 链路径压缩 / 深度上限 8 时拒绝而不猜。
16. **`_normalize_pool` 检出重复 account** —— 同一 account 出现在两个 entity 下时合并而非丢弃，并 log warning。

### 8.2 新增：`tests/unit/test_speaker_trust_migration.py`

- 重复导入按 account 哨兵幂等（第二遍 `dirty=False`，不写盘）。
- **加法合并**：先导入、后有服务端演化、再重导入 ⇒ 第二次跳过，值不变。
- **闸门开启前后**：pending 期间的流量不产生任何演化；导入 `final=true` 后闸门开、后续流量正常。
- **导入窗口双算的守卫测试**（对应 migration 硬伤 1）：构造「legacy 账本里已有 event E」+「pending 期 owner 复述触发 E 的重放」+「随后导入」，断言最终 `adjustment` 只含 E 一次。
- 分块导入：3 个 chunk，只有最后一个 `final=true` 才开闸；中途失败重跑无损。
- 单条脏 profile **不 422 整批**，进 `skipped`。
- `adjustment` 不 clamp、signal 环不截断不排序、activity 环 256 截断、两环分别处理。
- 池文件删除后重启 → 空池 + 闸门 pending → 插件重推 → 恢复到迁移时刻（**migration 硬伤 2 的守卫测试**）。
- `config_store` 透传后一次 dashboard reload/save 不改变 `speaker_trust_profiles` 的磁盘内容。

### 8.3 新增：`tests/unit/test_speaker_identity.py`（PR1）与实体生命周期用例（PR7）

### 8.4 既有测试的改动

| 文件 | 改动 |
|---|---|
| `tests/unit/test_speaker_trust.py` | L30-219（PermissionManager 算术/账本）整段迁到 `test_trust_store.py`（改成打 `memory.identity` / `trust_store`）；L2464-2964（QQSettingsService 写者/锁/回滚/取消）整段删除，其中 `test_all_settings_writers_acquire_both_transaction_locks`(:2942) 改成单锁；L2966-3074（activity id 稳定性）改成断言 wire 上发出的 activity event id 稳定；L222-966 / L969-1283 / L3076-3435 原则上不动，但路由级用例的响应断言要去掉 `trust_events`（PR6） |
| `tests/unit/test_group_memory_scopes.py` | `:3623-3627` 越界断言（PR2 补新字段版本，PR6 改成打端点期望 422）；`:11772-11780` 段级 trust 断言改成断言服务端派生值；`:12577` 的 `service._speaker_trust_for` monkeypatch 删除；L11700-12620 的 15 个 flush/trust 交互用例按新契约重写（无回传、无 cancelled 属性、按 `trust.persisted` 决定保留/pop） |
| `tests/unit/test_participant_memory_and_display_name.py` | `:529-600` 单发形状 trust 断言改成 `speaker_tier`；`:940-1170` 四个权限快照/刷新用例改成断言 wire 上的 tier 与服务端解析结果（`:1120` 的 `patch plugin.permission_mgr.get_speaker_trust` 删除）；`:1370-1410` epoch 轮换用例改成断言 activity event id 轮换 |
| `tests/unit/test_fact_dedup.py` / `test_scoped_refine.py` / `test_embedding_schema.py` / `test_evidence_promote_merge.py` / `test_group_memory_consent_and_locking.py` | **不动**（它们只把 `speaker_trust` 当 fact 字段喂数据） |

### 8.5 必须新补的语义守卫测试

1. **同一批内 owner 的纠错不得影响同批被纠错者的 stamp**（时序不变量，§4.4）。
2. **`trust.persisted == False` ⇒ 插件保留该段并在下一轮重投同一批消息**（at-least-once 链条，§4.5）。
3. **逐条 activity id 在放大重试下不重复计数**（`concurrency` 硬伤 1 的守卫）。
4. **`speaker_is_owner=True` 且 tier 非 admin ⇒ 422**，且插件侧枚举全部权限值证明该组合不可能产出（避免上线首日 422 风暴）。
5. **participant 路径的 activity id 在角色名含空格/CJK/超长时仍匹配 wire 正则**（minimal-seam 评审第 3 条的守卫）。
6. **不变量 P1**：一次请求中，failed 段上 durable 的 owner signal 也被折叠；断言重试同一批不二次扣分。

---

## 9. 需要维护者拍板的遗留问题

### 9.1 已降级为「遗留风险」的项（有缓解，需知情接受）

| # | 风险 | 缓解 |
|---|---|---|
| R1 | **迁移窗口内写入的 fact 行不带 trust 戳，仲裁永久弃权** | 窗口 = 插件启动后一个 HTTP 往返（秒级）；弃权 = PR #2639 之前的行为，不是错误值。这是「有界弃权」换「不可修复的双算」的显式取舍 |
| R2 | **迁移后的服务端演化不跟云走；换机后只能恢复到迁移时刻** | 插件 legacy 键永久保留 + 每次启动重推 ⇒ 迁移时刻状态可恢复。若插件数据也不进云存档（已核实 `MANAGED_CLOUDSAVE_PREFIXES` 不含 `plugins/`），则**今天也是同样情况，不是倒退**。灾备可手动 `reconcile_from_facts` |
| R3 | **`ascoped_forget` 会删掉携带 `_speaker_trust_signal_events` 的 fact 行**，所以 `reconcile_from_facts` 在发生过 forget 之后不完备 | 写进端点 docstring 与 PR 描述。不宣称它是完备自愈器 |
| R4 | **实体身份记录（`account_index` / `accounts` / `created_at`）是一份永久孤儿数据**：不进云存档、不被任何删除路径覆盖（tombstone 擦除、角色目录 rmtree、`delete_file_targets` 都够不到） | PR7 提供最小 `forget`；v1 显式写进 PR 描述。**注意 forget 的语义弱点**：事件本体还在别人的 fact 行和 archive 里，同一 account 回来后主人再说一次同样的话会重新应用被「遗忘」的纠错 |
| R5 | **升级后第一批消息活跃度重计一次**（activity id 从批级改逐条，新旧不同命名空间） | 上界 `SPEAKER_TRUST_ACTIVITY_MAX_BONUS = 0.02` ≪ margin 0.15；已到 cap 的老用户完全无感 |
| R6 | **PR4 回滚期间插件本地新增的演化不会被前滚重推**（account 哨兵按 source 记，第二次跳过） | 回滚窗口应当很短；写进 PR4 描述。若需要，人工删掉池里对应 account 的 `legacy_import` 哨兵即可强制重导 |
| R7 | **`speaker_base_trust` 通道无强鉴权** | 夹到 0.8（< admin 1.0）防自封 owner；今天所有插件与服务端同一部署，这只是纵深防御第一层 |
| R8 | **`processed_signal_events` 无界增长 + 整份 JSON 重写** | cap no-op 优化已砍掉绝大部分活跃度写；signal 写频率是每条 owner 纠错一次。容量判据：单条 24 字符，1 万条 ≈ 300KB。留逃生口：分片只能用 `speaker_trust.<n>.json` 平铺文件名 |
| R9 | **`json.dumps` 全程持 `_pool_lock` 且跑在 memory_server 的默认 threadpool 里**，与 `extract_facts` / `locale_state.reserve_*` 抢 worker | 与 R8 同源，靠 cap no-op 大幅缓解。若实测有压力，先做的应是分片而不是换锁 |

### 9.2 需要明确拍板的问题

1. **`persisted:false` 要不要上 dashboard？** 现在它只在日志里可见 + 通过插件重试自愈。是否需要一个计数器/告警面板？（原方案把这条留作 open question，本设计倾向「先落一个进程内计数器 + `GET /internal/trust/profile` 暴露，dashboard 接线留给后续」。）

2. **`speaker_base_trust` 通道现在零调用者（QQ 走 tier）。留着当扩展点，还是等第二个平台真正接入时再加（YAGNI）？** 留着的成本是一条互斥 422 + 一个 clamp 常量；不留的成本是接弹幕平台时再改一次 wire。**倾向留着**（PR2 一次做完，避免二次 wire 变更）。

3. **QQ 两条通道的 id 空间**：NapCat 给裸 QQ 号、开放平台给 OpenID（`qq_open_plat.py`），今天共用 `qq:` 前缀与同一个池。是否现在就引入 channel 维度（`qq.napcat:` / `qq.open:`）?
   - 引入的代价：迁移要能判断存量裸号 key 属于哪个通道 —— **通常无法判断**；且所有存量 `speaker_id` 变形 ⇒ event_id 全变 ⇒ 账本全线失配。
   - **本设计默认不引入**，显式记录「同一 platform 下两个 id 空间混用，同一真人的两个通道账号不会自动合并」，并把 PR7 的 `bind` 端点作为手工合并出口。**需要维护者确认接受。**

4. **`SPEAKER_TRUST_EVENT_HISTORY_LIMIT = 128` 的去留**。PR5 之后它的唯一使用者（`permission.py`）被删，活跃度环改用新常量 256。是保留为已废弃常量，还是同 PR 删掉并清理 `config/__init__.py` 的 re-export？（删除是公开常量的破坏性变更，倾向保留 + 注释标记 deprecated。）

5. **是否要给 `areconcile_from_facts` 加自动触发**（例如仅在「池文件启动时缺失」时跑一次）？本设计默认**不加**（扫描成本未知 + forget 之后不完备）。若维护者认为灾备自动化更重要，可在 PR3 加一个「仅当池文件缺失且全部闸门 cleared」的一次性后台触发。

6. **PR6 的执行时机**：legacy `speaker_trust` 字段在 PR5 之后第 N 个版本删除。N 定多少？删除时机也决定 `test_group_memory_scopes.py:3623-3627` 那条断言什么时候能改成只测新字段。

7. **第二个平台选谁做「接入成本接近零」的验证**？`bilibili_dm` 最便宜（权限词表与 QQ 完全同构、uid 强制纯数字、已有 `bili_dm:` 前缀习惯），但接 trust 的前提是**先让它走 scoped memory**。要不要在 PR5 之后立刻做一个最小接入来验证这个论断？

8. **trust 上云是否列为紧随其后的独立工作项**？需要动 `utils/cloudsave_runtime/_shared.py` 的 `MANAGED_CLOUDSAVE_PREFIXES`（`overrides/` / `meta/` 前缀已声明但零实现）与 `operations.py` 的三段路径校验。鉴于 §1.3(c) 的核实结论（今天也不上云），这不是本 PR 的回归修复，而是新能力。
