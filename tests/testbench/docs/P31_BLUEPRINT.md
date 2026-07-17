# P31 角色一键导出 (Character One-Click Export) — 蓝图

> **Single Source of Truth**. 本文件是 P31 阶段的**唯一权威规格**. `PLAN.md` 条目仅作索引; `PROGRESS.md` / `AGENT_NOTES.md` / `CHANGELOG.md` 描述均以本文件为准.
>
> **定位**: 给 Setup → Import 子页的**每个本地真实角色**行加一个 **[导出]** 按钮, 与该行既有 **[导入]** (`import_from_real`) 构成一对**镜像操作**. 一键把该角色在主程序里的**完整记忆目录** (含 `characters.json` + 全套记忆文件) 忠实打成一个 `<角色名>.zip`, 供本地**备份 / 迁移 / 分享给其他相关人员**. 导出的 zip 可被现有 `POST /api/persona/import_from_archive` 原样回吃 → 导出/导入闭环.
>
> **阶段号 / smoke 前缀**: 沿用「阶段号与 smoke 计数器有意分叉」约定. 本阶段取**阶段号 P31**; 新增 smoke 接续单调计数器: 后端 `p43_`、前端 jsdom `p44_` (现有最高 `p42`).
>
> **动因 (2026-07-15, 用户下达)**: 引文 —— "其他相关人员反映角色导出流程比较麻烦, 希望能在人设层加一个快捷导出按钮, 导出人设格式参照此文件 (`悠怡.zip`)." + 追加决策 —— "考虑到用户多数情况下的需求其实是把自己本地的主程序角色导出, 所以放在导入页面的每个本地人设下面更为合适, 与现有的从本地文件导入相对应."
>
> **与 P30 的区别 (关键)**: P30「记忆分析导出」是**脱敏**的、给外部**分享分析结论**用的; P31「角色导出」是**完全不脱敏**的**忠实全量转储**, 给**本地备份 / 可信迁移 / 回吃主程序**用的. 二者目的相反, 互不复用脱敏管线.

---

## 1. 目标与边界

### 1.1 核心目标

在 Import 子页 "从真实角色导入" 区的每个角色行, 于 **[导入]** 旁加一个 **[导出]** 按钮, 一键得到 `<角色名>.zip`, 内部结构**与参考文件 `悠怡.zip` 完全一致**:

```
<角色名>/
<角色名>/characters.json          # 真实全量 characters.json (含主人 + 全部猫娘 + 当前猫娘)
<角色名>/persona.json
<角色名>/facts.json
<角色名>/reflections.json
<角色名>/recent.json
<角色名>/time_indexed.db          # 忠实含二进制向量库
<角色名>/events.ndjson
<角色名>/outbox.ndjson
<角色名>/... (memory_dir/<角色名>/ 下的其余所有文件, 递归)
<角色名>/reflection_archive/<...>.json
```

数据源是**真实主程序记忆目录** (`session.sandbox.real_paths()["memory_dir"] / <角色名>`) + 真实 `config_dir/characters.json`. 全程**纯读宿主文件系统、不写沙箱、不取会话锁、不触 LLM、不脱敏**.

### 1.2 范围内 (In-Scope)

- **单一打包纯函数** `persona_router._zip_character_memory(config_dir, memory_dir, character_name) -> bytes`: 用标准库 `zipfile` 在内存里组一个 zip; 顶层文件夹 = 角色名; 写入真实 `characters.json` (若存在) + 递归写入 `memory_dir/<角色名>/**` 全部文件. 带**总解压体积上限守卫** (复用 `_MAX_ARCHIVE_UNCOMPRESSED_BYTES` = 500 MiB) 防病态大目录 OOM.
- **单一导出端点** `GET /api/persona/export_real/{name}`: 返回 `application/zip` attachment. 语义与 `/real_characters` 一致 (需 active session 才知道真实路径). I/O + 压缩 → `asyncio.to_thread`. **纯读, 不取 `session_operation` 锁, 不写任何盘** (对齐 P30 `/memory/export` 的只读裁决).
- **`_content_disposition` 助手** (镜像 `memory_router`): 文件名 `<角色名>.zip`, CJK 名走 RFC 5987 `filename*=UTF-8''`, ASCII 兜底 `NEKO_character_export.zip`.
- **前端**: `page_import.js` 每个真实角色行加 [导出] 次要按钮 (`showSaveFilePicker` 先于 `fetch` 拿另存为句柄 → 回退 anchor 下载) + tooltip 明示"完整原始记忆、含隐私、仅供备份/迁移". 文案全部入 i18n.

### 1.3 范围外 (Out-of-Scope, 明文约束)

- **不脱敏**: 忠实全量转储 (真实姓名 / 完整对话 / 事实 / 向量库). 隐私提示只在 UI tooltip 明示, 不做任何内容改写 (P30 的脱敏管线**不复用**).
- **不改主程序**: 只**读**主程序持久化目录. 绝不写真实 (非沙箱) 文件系统 (延续本 router 既有约束).
- **不写沙箱 / 不落临时文件**: zip bytes 在内存组装, 直接经 HTTP 发回浏览器.
- **不取会话锁 / 不触 autosave / 不触 snapshot**: 导出是旁路只读, 不改任何会话状态 (对齐 §3 P30 L63 教训).
- **不做沙箱角色导出**: 本期只导**真实主程序角色**. "导出当前沙箱里编辑过的角色" 是另一诉求, 记为未来可选 phase (§7), 本期不做.
- **不引入新依赖**: 只用标准库 `zipfile` / `io` / `pathlib`.

### 1.4 与既有导入的闭环 (设计正确性论证)

参考文件 `悠怡.zip` 的布局 (顶层 = 角色名文件夹, 内含 `characters.json` + 记忆文件平铺) **已能被现有** `POST /api/persona/import_from_archive` 识别: `_resolve_archive_memory_dir` 的 rglob 兜底会把"名字等于角色名、且含任一 `_KNOWN_MEMORY_FILES` 的目录"判定为记忆目录 (persona_router.py §`_resolve_archive_memory_dir`). 故 P31 导出 → P31 zip → 现有导入 天然往返, 无需改导入侧.

---

## 2. 后端契约

### 2.1 打包纯函数

```
_zip_character_memory(config_dir: Path, memory_dir: Path, name: str) -> bytes
```

- 顶层 arcname 前缀 = `f"{name}/"`.
- 若 `config_dir / "characters.json"` 存在 → 写 `<name>/characters.json` (原始字节, 不解析不改写).
- 遍历 `memory_dir / name` 下所有文件 (`rglob("*")`, 仅 `is_file()`), 以相对路径写 `<name>/<relpath>` (POSIX 分隔, 保留 `reflection_archive/` 等子目录结构).
- 累加已写入未压缩字节; 超过 `_MAX_ARCHIVE_UNCOMPRESSED_BYTES` → 抛 `HTTPException(413, ArchiveTooLarge)`.
- 用 `zipfile.ZIP_DEFLATED`. 二进制文件 (`time_indexed.db`) 原样写入.
- **忠实**: 不跳过任何文件, 不脱敏, 不重命名.
- **arcname 去重 (2026-07-15 往返自测发现)**: 用 `written: set[str]` 记录已写成员; 若 `memory_dir/<name>/` 下**也**有一个 `characters.json` (例如导入"config+memory 平铺在一个文件夹"的样例后, 沙箱记忆目录里被拷进了一份), **config_dir 的副本优先**, 跳过记忆目录里的同名副本. 否则会写出重名 zip 成员 (`zipfile` 只告警不报错, 但产物畸形、解压后者覆盖前者). 真实主程序目录 `memory_dir/<角色>/` 通常不含 characters.json, 故正常导出不触发; 此守卫覆盖往返 / 异常目录场景.

### 2.2 端点

```
GET /api/persona/export_real/{name}
```

流程:
1. `_require_session()` → 无会话 404 `NoActiveSession` (镜像 `/real_characters`).
2. `_assert_safe_character_name(name)` → 路径不安全 (含 `/`/`..`/NUL/绝对路径) 422 `UnsafeCharacterName` (复用既有守卫, 防路径逃逸).
3. `paths = session.sandbox.real_paths()`; 为空 (沙箱未 apply) → 500 `SandboxNotApplied`.
4. 读真实 `characters.json`; `name` 不在 `raw["猫娘"]` → 404 `NoSuchRealCharacter` (与 `import_from_real` 一致).
5. `zip_bytes = await asyncio.to_thread(_zip_character_memory, config_dir, memory_dir, name)`.
6. `Response(zip_bytes, media_type="application/zip", headers={"Content-Disposition": _content_disposition(f"{name}.zip")})`.

错误映射: 404 (无会话 / 无该角色) · 422 (角色名不安全) · 413 (超体积上限) · 500 (沙箱未 apply / 读盘异常).

> **纯读裁决**: 不用 `store.session_operation(...)` (那会触发 autosave 副作用, 见 L63). 直接 `_require_session()` + `real_paths()` + `asyncio.to_thread` 打包, 与 `/real_characters` 同构.

---

## 3. 前端契约

`page_import.js` → `renderRow(ch, source)`:

- 在既有 [导入] 按钮旁加 [导出] 次要按钮 (`class="small"`), `title` = i18n `setup.import.export_hint` (明示: 完整原始记忆目录, 含隐私, 仅供本地备份/迁移).
- `onExportReal(name, button)`:
  1. **先**在点击处理器里同步 `window.showSaveFilePicker({ suggestedName: `${name}.zip`, types:[zip] })` 拿句柄 (transient user activation 必须在任何 `await` 之前; 见 L66). `AbortError` → 静默返回 (用户取消); 其它异常 → `saveHandle=null` 走 anchor 回退.
  2. `fetch('/api/persona/export_real/<name>')`.
  3. `!ok` → toast 错误 (404 无会话/无角色 · 413 太大 · 其它).
  4. `deliverZip`: 有句柄 → `createWritable().write(blob)`; 否则 anchor `download=<name>.zip`.
  5. toast 成功 (含文件名).
- 复用 P30 modal 的下载 idioms (`parseFilename` / picker-先于-fetch / anchor 回退), 但本期**内联**于 `page_import.js` (每行一个按钮, 无模态), 不改动 P30 modal (避免回归).

按钮仅在 "从真实角色导入" 列表渲染 → 该列表本身需要 active session, 故导出按钮天然只在有会话时出现.

---

## 4. i18n (core/i18n.js, 单文件 zh-CN)

新增键 (归 `setup.import.*`):

- `button_export` = "导出"
- `button_exporting` = "导出中…"
- `export_hint` (tooltip) = "把该角色的完整记忆目录 (含隐私原始数据) 打包为 zip, 仅供本地备份 / 迁移 / 可信分享"
- `export_ok` (fmt name) = "已导出 {0}"
- `export_failed` = "导出失败"
- `export_no_session` = "没有活动会话, 无法读取真实角色路径"
- `export_too_large` = "该角色记忆目录过大, 超过导出上限"

---

## 5. 测试

### 5.1 后端 `smoke/p43_persona_export_smoke.py`

纯函数 (直接调 `_zip_character_memory`):
- **X1 结构**: 顶层文件夹 = 角色名; 含 `<name>/characters.json` + 各记忆文件; 嵌套 `reflection_archive/x.json` 保留路径.
- **X2 忠实字节**: 解出的 `facts.json` / 二进制文件字节与源文件逐字节相等 (无脱敏 / 无改写).
- **X3 缺 characters.json**: 仍出 zip (只含记忆文件), 不崩.
- **X4 体积上限**: 临时调小 `_MAX_ARCHIVE_UNCOMPRESSED_BYTES` → 抛 `ArchiveTooLarge`.

端点 (TestClient + monkeypatch `session.sandbox.real_paths`):
- **X5 happy**: 200 / `application/zip` / `Content-Disposition` 含 RFC 5987 的 `<角色名>.zip`; 解出结构正确.
- **X6 无会话**: 404 `NoActiveSession`.
- **X7 未知角色**: 404 `NoSuchRealCharacter`.
- **X8 往返**: 导出 zip → `POST /api/persona/import_from_archive` → 200, 角色/文件被识别 (闭环验证).

### 5.2 前端 `smoke/p44_persona_export_ui_smoke.mjs` (jsdom)

- **U1**: 渲染真实角色行含 [导出] 按钮 + tooltip.
- **U2**: stub `fetch` 返回 zip blob + Content-Disposition; stub `showSaveFilePicker` 成功 → 点击 → 断言 picker 以 `<角色名>.zip` 调用 + blob 写入 + 成功 toast.
- **U3**: `showSaveFilePicker` 抛 `AbortError` → UI 不报错、不下载.
- **U4**: 无 `showSaveFilePicker` (undefined) → anchor 回退 (`a.download == <角色名>.zip`).

---

## 6. 安全 / 隐私

- **路径安全**: `_assert_safe_character_name` 复用既有守卫, 角色名仅作单路径段; rglob 只在 `memory_dir/<name>` 子树内, 不越界.
- **只读宿主**: 只 `open("rb")` 读真实文件, 绝不写真实盘 (router 既有硬约束).
- **隐私诚实**: 导出**完全不脱敏** —— 这是刻意设计 (备份/迁移语义). UI tooltip 明示"含隐私原始数据". 不做正则涂抹 (与 redact 冻结原则无关, 因为本期根本不脱敏). `characters.json` 是**全量** (含主人名 + 其他角色), 与参考文件一致; UI 提示已涵盖.

---

## 7. 未来可选 (本期不做)

- **沙箱角色导出**: 在 Persona 子页导出"当前会话沙箱里编辑后"的角色 (数据源 = `cm.memory_dir` 而非 real_paths). 用相同 `_zip_character_memory`, 换数据源即可. 记为 `pXX_sandbox_character_export` 占位.
- **可选脱敏开关**: 若未来需要"脱敏分享一个角色", 可让本端点复用 P30 `redact_export_bundle`. 本期不加 (会与"忠实备份"语义冲突, 且 P30 已覆盖分享分析场景).

---

## 附: 关键决策摘要 (给未来 Agent)

- 放置在 **Import 页每个本地角色行** (非 Persona 页), 因为真实诉求是"导出本地主程序角色", 与"从本地导入"镜像.
- 忠实**全量、不脱敏**; 文件名 `<角色名>.zip`; 内层顶层文件夹 = 角色名, 与参考 `悠怡.zip` 逐项对齐.
- 纯读, 不取锁, `asyncio.to_thread` 打包; 复用 `_assert_safe_character_name` / `real_paths` / `_MAX_ARCHIVE_UNCOMPRESSED_BYTES`.
- 导出 zip 可被现有 `import_from_archive` 回吃 → 往返闭环 (p43 X8 守).
