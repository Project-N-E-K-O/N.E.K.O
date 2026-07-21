# 源码手动部署

```bash
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
uv sync
```

Python 固定为 3.11；所有 Python 模块、脚本、测试和临时命令都通过 `uv run`。

## 构建前端

```powershell
.\build_frontend.bat
```

```bash
./build_frontend.sh
```

脚本校验/解压 Yui Origin，运行 `npm ci`，生成 plugin manager 与共享 React chat bundle。

## 正常启动

```bash
uv run python launcher.py
```

launcher 规划端口、启动 memory/main/agent、协调关闭，并在服务前应用暂存的 cloud-save snapshot。使用它报告的 URL；48911 只是首选值。

## 诊断拆分模式

```bash
uv run python -m app.memory_server
uv run python -m app.main_server
uv run python -m app.agent_server
```

只有 memory + main 时主 UI 可加载，但 Agent、托管插件、浏览器/电脑控制等需要 agent/tool。拆分模式不复现 launcher 的端口回退与生命周期。

Cloud Save 的 Steam RemoteStorage 路径应通过 Steam/桌面 launcher 验证。拆分 main server 可执行后备 snapshot 导入并通知 memory reload；退出不会自动把运行时变化暂存，需由 Cloud Save Manager 准备上传 snapshot。

在主 URL 的 `/api_key` 选择当前 Core/Assist Provider、填写密钥并运行连通性检查。源码模式不消费 Docker API 初始化变量。`/health` 用于启动诊断。
