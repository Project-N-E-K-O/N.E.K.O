# 插件工坊「发布」第三期实施计划：Market 投稿接入

状态：第 0 步已验证通过，走代理复用方案（非降级）。
验证日期：2026-11-05。线上环境 `https://market.project-neko.cn`。

---

## 一、第三期要做什么

把「发布」页里写死 `state: 'later'` 的两步做成真的：

| 步骤 | 现在 | 第三期后 |
|---|---|---|
| `submission`（Market 投稿） | 写死「以后做」 | 登录引导 → 预检 → 建草稿 → 提交审核 |
| `market`（Market 版本） | 写死「以后做」 | 靠 `plugin_id` 反查，显示上架版本 |

一句话：复用本体 Market 登录态，加一组投稿写代理，前端把两步接上。

---

## 二、第 0 步验证结果（已通过，不再是闸门）

用真实 `neko-desktop` 登录 token 打线上，结果：

| 项 | 结果 |
|---|---|
| 登录文件 client_id | `neko-desktop` |
| subject 已验证 | 是 |
| `GET /review/submissions/mine` 带 token | **200** |
| `POST /review/submissions/repository-preview` 带 token | **200** |
| 无 token 时 | 401（接口存在，鉴权生效） |

结论：
1. `neko-desktop` 在服务器 `ORY_ALLOWED_CLIENT_IDS` 允许名单里 → **复用方案成立，降级方案丢弃**。
2. `/mine` 返回里含历史投稿，带 `plugin_id`（approved 后的真实 Market 插件 ID）→ 「能否反查插件」确认为**能**，`market` 步骤可做实。

---

## 三、已核实的基础设施（决定方案形状）

来源：本仓库 `plugin/server/routes/market_bridge.py`（181KB）。

- **OAuth 登录态完整**：`/market/oauth/start`、`callback`、`complete`、`status`，`_ensure_valid_oauth_token`（自动刷新 + CAS 防并发）。access_token 只存服务端文件，从不进浏览器。
- **client**：`neko-desktop`（`plugin/settings.py`，`NEKO_AUTH_CLIENT_ID` / `_OAUTH_CLIENT_ID`）。scope `openid email profile offline`。
- **只读代理样板**：`_proxy_market_catalog`（固定路径 + 响应头白名单 `_CATALOG_RESPONSE_HEADERS` + 结构化日志，只记 request-id 和失败类别）。
- **token 助手均为 `market_bridge.py` 模块私有**：`_ensure_valid_oauth_token`、`_oauth_subject_is_verified`、`_verify_token`。→ 投稿代理放同一文件，不跨模块 import 下划线名。

**OAuth 部分 = 零新代码。** 复用现成助手即可。

---

## 四、Market 侧真实接口（来自私有仓库 `Project-N-E-K-O/N.E.K.O.-PluginMarket`）

### 4.1 鉴权真相（`app/core/security.py`）

投稿写接口用 `require_verified_user` → `get_current_user`。这条链只检查：
1. token 是有效 Ory Hydra access token；
2. client_id ∈ `_allowed_ory_client_ids()`（= `ORY_ALLOWED_CLIENT_IDS` + `DESKTOP_OAUTH_CLIENT_ID`）；
3. 邮箱已验证。

**关键**：接口**没用** `get_current_desktop_user`（那个才限制只有桌面 client），用的是更宽的 `get_current_user`。所以 `neko-desktop` 不会被 client 类型拦——第 0 步已证实它过了。

### 4.2 接口清单（前缀 `/api/v1/review/submissions`）

| 方法 | 路径 | 鉴权 | 代理必要性 |
|---|---|---|---|
| POST | `/preflight` | verified | 建草稿前完整预检（**比 repository-preview 更全**） |
| POST | `/repository-preview` | verified | 仓库校验预览 |
| POST | `/drafts` | verified | 建草稿 |
| GET | `/mine` | current_user | 列我的投稿 |
| GET | `/{id}` | current_user（本人/reviewer） | 查单条 |
| GET | `/{id}/timeline` | 本人，limit≤200 | 审核时间线 |
| PUT | `/{id}/draft` | 仅作者 | 改草稿 |
| POST | `/{id}/submit` | 仅作者 | 提交审核（对外动作） |
| POST | `/{id}/withdraw` | 仅作者 | 撤回（对外动作） |
| POST | `/{id}/revision` | 仅作者 | 补充修订（本期不做） |

### 4.3 请求 / 响应形状（`app/schemas/plugin_submission.py`）

**建草稿 `SubmissionDraftCreate`**（写模型 `extra="forbid"`，多传字段直接报错）：
- 必填：`repo_url`、`plugin_name`、`plugin_slug`
- 选填：`plugin_directory`、`description`、`short_description`、`icon_url`、`zone_slug`、`tags`、`submitted_ref`、`resolved_commit`、`commit_url`、`license_name`、`metadata`

**`RepositoryPreview` 响应**（驱动「命名冲突」提示）：
- `identity_status`：`matched` / `mismatched` / `unknown`
- `repository_plugin_id`、`plugin_id`、`expected_repository_name`
- `workflow_status`：`available` / `missing` / `disabled` / `unknown`
- `warnings: list[str]`、`plugin_name`、`description`、`short_description`、`license_name`、`resolved_commit`、`submitted_ref`、`commit_url`

**状态流**（`SubmissionStatus` + `ReviewProgress`，比 draft→submitted→in_review→closed 更细）：
- `review_progress.phase`、`next_actor`、`terminal_kind`（approved / rejected / withdrawn…）

**反查插件**：`PluginSubmission.plugin_publication`（approved 后带 `PluginStatus`），`/mine` 返回里直接有 `plugin_id`。

---

## 五、后端：投稿代理（约 300–400 行 + 测试）

位置：`plugin/server/routes/market_bridge.py`，紧挨 catalog 代理。
理由：token 助手是该模块私有；跨模块 import 下划线名是坏味道，抽共享模块是对 181KB 文件的大重构，超出本期范围。

### 本地路由（前缀 `/market/submissions`，复用 catalog 代理的响应头白名单与日志格式）

| 本地路由 | 上游 | 类型 |
|---|---|---|
| POST `/market/submissions/preflight` | `/review/submissions/preflight` | 只读语义 |
| POST `/market/submissions/repository-preview` | 同名 | 只读语义 |
| GET `/market/submissions/mine` | `/review/submissions/mine` | 只读 |
| GET `/market/submissions/{id}/timeline` | 同名 | 只读 |
| POST `/market/submissions/drafts` | 同名 | 写 |
| PUT `/market/submissions/{id}/draft` | 同名 | 写 |
| POST `/market/submissions/{id}/submit` | 同名 | 写（对外动作） |
| POST `/market/submissions/{id}/withdraw` | 同名 | 写（对外动作） |

### 代理规则

1. 每个请求先 `_ensure_valid_oauth_token()`；无 token → 本地 409 `market_auth_required`（不转发）；`_oauth_subject_is_verified` 为假 → 409 `market_email_unverified`。
2. access_token 只在服务端附加 `Authorization: Bearer`；上游 401 带 `rejected_access_token` 刷新重试一次（复用现有模式），仍失败返回 401 + 本地错误码，**不透传上游响应体**。
3. 写路由用与 `market_bridge` 其他写端点相同的守卫（bridge token / `_require_local_bridge_token_access`）。
4. `{id}` 做路径注入校验（数字或 uuid 白名单）。
5. **不做 `/revision`**：等真有拒稿需求再加，避免猜测请求体。
6. market-evidence / release 联动不在本期（第二期已覆盖 `publish market`）。

### 错误码（`plugin/server/routes/plugin_project_errors.py` 或对应错误码模块新增）

`market_auth_required`、`market_email_unverified`，以及透传上游 `code+detail`（catalog 代理已有先例）。

---

## 六、前端：两个 later 步骤变实（约 250 行 + 测试）

建议拆出 `AuthoringProjectSubmission.vue`——投稿步骤逻辑比其它步骤复杂得多。

### 6.1 `submission` 步骤状态机

- **未登录** → 状态 tag「需要登录」+ 登录按钮（复用 `useMarketAuth`，见下方注意点）。
- **已登录** → 打开标签页时随 publish-status 一起懒加载 `/mine`，按 `repo_url` / `repo_name` 匹配本项目：
  - 无投稿 → 「去创建草稿」：从 `plugin.toml` 元数据（名称/slug/描述/作者/版本）+ origin URL 预填，先调 `preflight`/`repository-preview` 预检，不通过就显示上游报错，不盲发；确认框后 `drafts` 创建。
  - `draft` → 「提交审核」按钮，二次确认（明示：进入人工审核、公开仓库地址会被展示）。
  - `submitted` / `in_review` → 状态 tag + timeline 摘要，禁用写操作。
  - `approved` → done，显示 `plugin_directory` / `plugin_id`。
  - `closed` / `rejected` → 状态 + 原因（timeline）。

### 6.2 `market` 步骤

靠 `/mine` 里 approved 投稿的 `plugin_id`，经现有 `/market/catalog/api/v1/plugins/{id}` 代理读已发布版本，与本地 `version` 对比，给「已上架 vX / 落后 / 未上架」。

### 6.3 注意点（必须处理）

- **`authedFetch` 没有 export**，只在 `useMarketAuth.ts` 模块内部用。export 的是 `startMarketLogin`、`loadMarketAuthStatus`、`marketAuth`。
  → 要么把 `authedFetch` export 出来，要么在发布页自己包一层 bridge token 调用。**不能直接复用。**
- i18n 8 语言补键（预计 15–20 个），含新错误码文案。

---

## 七、测试

- **后端**：仿 `plugin/tests/unit/server/test_plugin_publish_status_remote.py` 的 MockTransport 模式。约 10–12 个用例：转发路径/方法/请求体正确；Bearer 只在服务端附加；未登录不转发；401 刷新重试一次；上游错误不透传 body；`{id}` 注入被拒。
- **前端**：`PluginAuthoring.test.ts` 增投稿步骤各状态渲染与动作（含二次确认弹窗拦截）、未登录登录引导；`pluginProjects.test.ts` 的错误码-文案映射一致性测试锁住新错误码。

---

## 八、顺序与体量

1. ~~第 0 步验证~~ **已完成**。
2. 后端 `/market/submissions/*` 代理 + 测试。
3. 前端 `submission` + `market` 步骤 + i18n + 测试。
4. 浏览器对照真实 Market 验证只读路径（登录态 + mine + 状态显示；**不实际提交投稿**，提交是对外动作，留人工点）。

总量约 600–800 行（不含测试）。不改 Market 仓库，不引入新依赖。

---

## 九、与另一 agent 方案的差异（已核实）

| 另一 agent 说的 | 本计划修正 |
|---|---|
| 复用 `authedFetch` | 错，未 export，需 export 或自包一层 |
| 接口形状是记忆/推测 | 已从 Market 私有仓库读到权威 schema，不再是假设 |
| 漏了 `/preflight` | 已补入，建草稿前首选预检端点 |
| 「能否反查插件」悬而未决 | 已确认能，`/mine` 带 `plugin_id` |
| 可能需降级到深链 | 第 0 步 200，降级分支丢弃 |
