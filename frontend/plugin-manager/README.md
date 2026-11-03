# N.E.K.O 插件管理系统前端

## 项目结构

```bash
src/
├── api/              # API 服务层
├── assets/           # 静态资源
├── components/       # 组件
│   ├── common/      # 通用组件
│   ├── plugin/      # 插件相关组件
│   ├── metrics/     # 性能监控组件
│   ├── logs/        # 日志组件
│   └── layout/      # 布局组件
├── composables/     # 组合式函数
├── stores/          # Pinia 状态管理
├── router/          # 路由配置
├── views/           # 页面视图
├── utils/           # 工具函数
└── types/           # TypeScript 类型定义
```

## 技术栈

- Vue 3 (Composition API)
- TypeScript
- Pinia (状态管理)
- Vue Router (路由)
- Element Plus (UI 组件库)
- Axios (HTTP 请求)
- Day.js (日期时间处理)

## 开发

### 安装依赖

```bash
npm install
```

### 启动开发服务器

默认 Vite 页面来源是 `http://localhost:5173`。启动插件后端前，在后端进程环境中设置
`NEKO_PLUGIN_MUTATION_ALLOWED_ORIGINS=http://localhost:5173`，才能获取生命周期与插件包上传/安装操作的 CSRF token。
如使用 `127.0.0.1` 或其他开发端口，填写实际完整来源；多个来源以逗号分隔。
后端不会默认信任所有运行在 5173 的网页。此配置仅用于开发，官方 NAS/Docker 部署无需设置。
允许列表中的网页可以读取主服务与插件服务共享的实例 token，只应添加受信任的开发页面。

```bash
npm run dev
```

### 构建生产版本

```bash
npm run build
```

### 类型检查

```bash
npm run type-check
```

### 代码格式化

```bash
npm run format
```

## 配置

### API 配置

在 `.env` 文件中配置 API 基础 URL：

```env
VITE_API_BASE_URL=http://localhost:48916
```

`VITE_API_BASE_URL` controls the API base used by the browser application. To
change the backend target used by Vite's development proxy, set
`VITE_BACKEND_URL` instead; it defaults to `http://localhost:48916`.

### 代理配置

开发环境只代理插件管理器实际使用的后端路由，包括 `/plugin/`、受限的
Market 路由、`/plugins`、`/server`、`/health`、`/available`、`/ws`，以及
Hosted UI 文档解析所需的 `/api/documents`。不会把整个 `/api` 命名空间暴露给
开发服务器；精确列表以 `vite.config.ts` 为准。

## 功能模块

### 1. 插件管理
- 插件列表展示
- 插件详情查看
- 插件启动/停止/重载
- 入口点执行

### 2. 性能监控
- 实时性能指标
- 历史数据查看
- CPU/内存监控

### 3. 日志查看
- 日志实时查看
- 日志过滤（级别、关键词）
- 日志文件列表

### 4. 配置管理
- 配置查看
- 配置更新

### 5. 插件制作工作台
- `/development/workbench` 展示服务端登记的项目列表，登记文件为运行数据根下的 `plugin-authoring.json`，只保存项目 ID 与源码目录；名称、版本、入口等每次从项目的 `plugin.toml` 实时读取，页面提供手动刷新。
- 创建前向服务端预览标准项目文件，确认后在本机父目录写入源码、初始化 Git 仓库并登记；登记失败时项目保留在磁盘上并返回错误。
- 支持将已有插件目录（含 `plugin.toml`）添加到工作台；目录缺失或无法解析时标记状态，不会自动移除。
- 项目详情可打开目录、挂载到开发模式；挂载状态（未挂载 / 已挂载 / 已登记但开发模式关闭）实时取自开发模式登记。新建成功后直接进入该项目详情。
- “从工作台移除”只删除登记，不删除磁盘文件，也不影响开发模式挂载。
- 工作台不模拟运行、检查、构建或发布；运行调试使用开发模式，检查与发布使用帮助中说明的 CLI 工作流。

## 目录说明

- `api/`: 所有 API 调用封装
- `components/`: 可复用组件
- `stores/`: Pinia 状态管理
- `views/`: 页面组件
- `utils/`: 工具函数
- `types/`: TypeScript 类型定义

## 注意事项

1. 所有 API 调用都通过 `api/` 目录下的服务层
2. 状态管理使用 Pinia，按功能模块划分 Store
3. 组件遵循单一职责原则
4. 使用 TypeScript 确保类型安全
