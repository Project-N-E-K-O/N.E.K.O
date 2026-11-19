# 模型工坊本体集成与交付边界

模型工坊把模型市场下载的 `.neko-model.zip` 导入 N.E.K.O. 本地模型库。支持 Live2D、VRM、MMD（PMX/PMD），不创建角色，不自动切换当前预览，也不代替用户点击原有保存按钮。业务在 `services/model_workshop/`，HTTP 在 `main_routers/model_market_router.py`，页面复用原模型管理页，沿用小剧场的内置功能结构。

## 当前状态

包校验、安装登记、任务和恢复、未完成资源隔离、存储迁移协调、管理面板及模型保存适配已有实现。本轮补齐正式 Main 的路由、中间件和页面上下文。**新增接线尚未运行测试，也未启动服务或部署。** 既有隔离测试及真实模型结果只证明此前对应提交，不能当作本轮正式 Main 集成通过。

本轮新增接线和更新的 PR 材料只保存在本地；原 N3 草稿 #3758 已存在，本轮未更新远端，未合入或发行。当前整体进度继续按原方案的验收口径保留 89%，不把未验证接线计为验收完成。

## 正式入口

| 位置 | 职责 |
| --- | --- |
| `app/main_server/__init__.py` | 注册 `ModelImportBodyGuard`，保持在存储启动闸门内；复用既有关闭时排空与迁移保护 |
| `app/main_server/web_app.py` | 注册独立模型市场 Router，位置在页面兜底之前 |
| `main_routers/model_market_router.py` | 手动导入、查询任务、取消任务、查询已安装模型；不接受网络 URL 或任意服务器路径 |
| `main_routers/pages_router.py` | `/model_manager` 与兼容入口 `/l2d` 共享页面上下文，按同一个直接本地访问策略启用工坊 |
| `templates/model_manager.html` | 同时输出工坊面板和脚本；未启用上下文的渲染仍不输出二者 |
| `static/js/model_manager/model-workshop.js` | 只管理自己的导入任务；复用现有八语言大小、忙碌及未开放提示承接 Main 拒绝 |
| `static/js/model_manager/page-controller.js` | 初始角色恢复后挂载；提供保留当前选择的刷新和用户主动查看回调 |

前端为模板加原生静态脚本，以上接线不修改 `frontend/*/src`，不新增构建产物。静态脚本沿用现有模型管理资源版本机制。

## 请求顺序与安全边界

Starlette 后注册的用户中间件在外层。按本轮源码登记顺序，正常 Main HTTP 请求先经过既有 Host/Origin、实例访问、全局请求体和模型读取隔离，再经过存储启动闸门，最后才进入模型导入接收保护及 Router。ASGI 自带的错误处理层未在此简图重复列出。

```text
Host/Origin
  → InstanceAccess
  → 原全局 BodyLimit（Avatar Tool / 外部记忆保留原策略）
  → ModelWorkshopRequestGate（既有模型读取隔离）
  → main_storage_limited_mode_guard
  → ModelImportBodyGuard（仅 POST /api/model-market/imports）
  → Router
```

- 导入保护必须在存储启动闸门之后，避免尚未选择存储位置时取得工坊 writer、恢复日志或开始解析包。该顺序是代码设计与静态核对结果，真实 Main 行为仍待验证。
- 导入保护仅处理自己的 POST 路径；其它请求和 WebSocket 原样转交。不提高全局额度，不改 Avatar Tool、外部记忆或普通模型上传接口。
- 接收前继续使用直接回环、原 Origin/CSRF 校验、两个解析名额、实际接收字节计数和存储上传许可；最大包 500 MiB，加 1 MiB multipart 封装。请求只允许一个 `file`，不接远程下载地址，不另建配对或一键安装链路。
- 页面启用判断与 API 共享 `is_direct_loopback_request`。它只是呈现条件，后端每次请求仍独立检查；远端或被该策略拒绝的代理请求不因面板或脚本存在而获得导入权限。
- 路由和中间件登记不调用安装服务。writer/恢复继续惰性取得，安装/删除/迁移和关闭仍使用已有 N2/N3 协调，不在启动时扫描或创建模型事务。
- 受理后由后端任务持有输入；页面关闭或取消不表示服务器提交回滚。安装完成只刷新选项，用户主动查看和保存后才应用到角色。
- Steam 专属 Router、发布、订阅、退订及角色同步不在本轮范围；本体继续按原共同模型能力加载资源。

## 本轮不执行的工作

用户明确要求先推进部署和测试以外的剩余项。本轮没有运行单元、浏览器、集成测试、语法执行检查或构建，没有启动 Main/桌面/数据库/容器，没有导入实际模型或改动用户存储。差异阅读和 Git 范围核对不算运行时通过。

后续恢复验证时，应优先覆盖正式 Main 而非只用临时 FastAPI 容器：

1. 路由和页面同时可用；`/model_manager` 与 `/l2d` 的工坊上下文一致；本地、远端和代理访问策略一致。
2. 存储选择/迁移/恢复受限时，导入在读取正文、writer 和 spool 创建前被拒绝；不影响旧页面和原启动闸门。
3. Host、实例访问、Origin/CSRF、超额/分块/截断正文、解析名额与迁移互斥，且 Avatar Tool、外部记忆、普通上传/WebSocket 维持原策略。
4. Live2D/VRM/PMX/PMD 从市场下载后完成导入、预览、保存、重开、返回/关闭及 Main 模型刷新；不改变角色来源或其它角色。
5. 关闭排空、Windows/Linux/macOS、软件渲染、容量与掉电恢复，以及市场真实 Auth/代理、PostgreSQL、备份与并发验收。

PR 描述草稿保存在任务的外部工作目录，按真实模板填写回归风险，明确区分历史证据与本轮未测试内容。待恢复测试并满足接入门槛后，再推送相关依赖 PR；本轮不触发远端 CI，不把本地接线完成视为允许公开发行。
