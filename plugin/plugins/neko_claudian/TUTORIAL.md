# 🐱 猫娘 Claudian 使用教程

> 30 分钟保姆级教程，从零开始掌握猫娘 Claudian 的所有功能

---

## 目录

1. [安装与启动](#1-安装与启动)
2. [界面介绍](#2-界面介绍)
3. [发送消息](#3-发送消息)
4. [使用斜杠命令](#4-使用斜杠命令)
5. [多 Tab 管理](#5-多-tab-管理)
6. [工具调用](#6-工具调用)
7. [MCP 服务器](#7-mcp-服务器)
8. [计划模式](#8-计划模式)
9. [对话回退](#9-对话回退)
10. [猫娘 LLM 注入](#10-猫娘-llm-注入)
11. [设置与配置](#11-设置与配置)
12. [常见问题](#12-常见问题)

---

## 1. 安装与启动

### 前置条件

- N.E.K.O 已安装并运行
- Claude CLI 已安装（`claude --version`）
- Python 3.10+

### 安装步骤

1. 打开 N.E.K.O 插件市场
2. 搜索 "猫娘 Claudian"
3. 点击 "安装" 按钮
4. 等待安装完成

### 验证安装

访问以下地址，看到 JSON 响应表示安装成功：

```
http://127.0.0.1:48930/neko_claudian/api/health
```

预期响应：
```json
{
    "ok": true,
    "plugin": "neko_claudian",
    "version": "1.0.0"
}
```

---

## 2. 界面介绍

### 主界面布局

```
┌─────────────────────────────────────────┐
│ 🐱 猫娘 Claudian                    ⚙️ │  ← 标题栏
├─────────────────────────────────────────┤
│ [Tab 1] [Tab 2] [+]                    │  ← Tab 栏
├─────────────────────────────────────────┤
│                                         │
│  💬 消息区域                            │  ← 对话消息
│                                         │
│  🤖 Claude 的回复                       │
│                                         │
├─────────────────────────────────────────┤
│ [输入消息...                    ] [➤]  │  ← 输入框
└─────────────────────────────────────────┘
```

### 界面元素

| 元素 | 说明 |
|------|------|
| 🐱 标题栏 | 显示插件名称和设置按钮 |
| Tab 栏 | 管理多个对话会话 |
| 消息区域 | 显示对话历史 |
| 输入框 | 输入消息的地方 |
| 发送按钮 | 发送消息（或按 Enter） |

---

## 3. 发送消息

### 基本发送

1. 点击输入框
2. 输入你的消息
3. 按 `Enter` 或点击 `➤` 按钮

### 换行

按 `Shift + Enter` 可以在输入框中换行。

### 示例对话

```
你: 帮我写一个 Python 函数，计算斐波那契数列

Claude: 好的，这是一个计算斐波那契数列的 Python 函数：

def fibonacci(n):
    if n <= 1:
        return n
    return fibonacci(n-1) + fibonacci(n-2)

你: 能优化一下吗？用动态规划

Claude: 当然！这是使用动态规划的优化版本：

def fibonacci(n):
    if n <= 1:
        return n
    dp = [0] * (n + 1)
    dp[1] = 1
    for i in range(2, n + 1):
        dp[i] = dp[i-1] + dp[i-2]
    return dp[n]
```

---

## 4. 使用斜杠命令

斜杠命令以 `/` 开头，用于快速执行操作。

### 可用命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/clear` | 开始新对话 | `/clear` |
| `/new` | 同 `/clear` | `/new` |
| `/help` | 查看帮助 | `/help` |
| `/resume` | 恢复之前的对话 | `/resume` |
| `/fork` | 分叉当前对话 | `/fork` |
| `/add-dir` | 添加外部目录 | `/add-dir ./src` |

### 使用示例

```
你: /clear
[系统] 已开始新对话

你: /add-dir ./my-project
[系统] 已添加外部上下文: ./my-project

你: 帮我分析这个项目的代码结构
Claude: 好的，让我分析一下...
```

---

## 5. 多 Tab 管理

### 创建新 Tab

点击 Tab 栏右侧的 `+` 按钮。

### 切换 Tab

点击对应的 Tab 标签。

### 关闭 Tab

将鼠标悬停在 Tab 上，点击 `×` 按钮。

### Tab 快捷键

| 快捷键 | 说明 |
|--------|------|
| `Ctrl+T` | 新建 Tab |
| `Ctrl+W` | 关闭当前 Tab |
| `Ctrl+Tab` | 切换到下一个 Tab |
| `Ctrl+Shift+Tab` | 切换到上一个 Tab |

---

## 6. 工具调用

Claude 可以使用多种工具来完成任务。

### 支持的工具

| 工具 | 说明 |
|------|------|
| `Bash` | 执行命令行命令 |
| `Read` | 读取文件 |
| `Write` | 写入文件 |
| `Edit` | 编辑文件 |
| `Glob` | 搜索文件 |
| `Grep` | 搜索内容 |
| `WebFetch` | 获取网页 |
| `WebSearch` | 搜索网络 |
| `Agent` | 启动子代理 |

### 工具调用示例

```
你: 帮我创建一个 hello.py 文件

Claude: 我来创建这个文件。

[工具调用: Write]
  文件路径: hello.py
  内容:
  print("Hello, World!")

[工具结果: Write]
  ✅ 文件已创建

Claude: 已创建 hello.py 文件。
```

### 权限控制

当 Claude 请求执行危险操作时，你会看到权限请求：

```
[权限请求]
工具: Bash
命令: rm -rf /tmp/test
描述: 删除临时目录

[允许] [拒绝] [始终允许]
```

---

## 7. MCP 服务器

MCP (Model Context Protocol) 让 Claude 可以使用外部工具。

### 查看 MCP 服务器

```
你: /mcp
[系统] 已配置的 MCP 服务器:
  - filesystem: 已启用
  - github: 已启用
```

### 添加 MCP 服务器

在设置中配置 `mcpServers`：

```json
{
    "mcpServers": {
        "my-server": {
            "command": "node",
            "args": ["server.js"]
        }
    }
}
```

### 使用 MCP 工具

```
你: 帮我读取 GitHub 上的 issue 列表

Claude: 我来使用 GitHub MCP 工具查询。

[工具调用: Mcp__github__list_issues]
  仓库: owner/repo

[工具结果]
  #1 Bug: ...
  #2 Feature: ...
```

---

## 8. 计划模式

计划模式让 Claude 先制定计划，再执行。

### 进入计划模式

```
你: 帮我重构这个项目

Claude: 我先进入计划模式，制定重构方案。

[计划模式]
1. 分析现有代码结构
2. 识别需要重构的模块
3. 制定重构步骤
4. 逐步执行重构

[实施计划] [修订计划] [取消]
```

### 计划模式的优势

- ✅ 先看计划，再执行
- ✅ 可以在执行前修改计划
- ✅ 避免意外操作

---

## 9. 对话回退

对话回退让你回到之前的对话状态。

### 使用回退

1. 在消息区域找到要回退到的位置
2. 点击消息旁的 "回退" 按钮
3. 确认回退

### 回退类型

| 类型 | 说明 |
|------|------|
| 对话回退 | 回退对话历史 |
| 代码回退 | 回退文件修改 |

### 示例

```
你: 帮我修改 main.py

Claude: [修改了 main.py]

你: [回退到修改前]

[系统] 已回退到之前的对话状态
[系统] main.py 已恢复
```

---

## 10. 猫娘 LLM 注入

猫娘 LLM 可以通过专用入口控制 Claude。

### 5 个注入入口

| 入口 | 说明 |
|------|------|
| `neko_inject_text` | 注入文本到输入框 |
| `neko_send_silently` | 静默发送消息 |
| `neko_click_send` | 模拟点击发送 |
| `push_claude_reply` | 推送 Claude 回复 |
| `neko_observe_stream` | 旁听流式事件 |

### 使用示例

```python
# 在猫娘 LLM 中调用
await plugin.call("neko_claudian", "neko_inject_text", {
    "text": "帮我写一个爬虫"
})

await plugin.call("neko_claudian", "neko_send_silently", {
    "text": "分析这段代码"
})

# 旁听 Claude 的工作过程
events = await plugin.call("neko_claudian", "neko_observe_stream", {
    "max_events": 10
})
```

---

## 11. 设置与配置

### 访问设置

点击标题栏的 ⚙️ 按钮。

### 主要设置项

| 设置 | 说明 | 默认值 |
|------|------|--------|
| 模型 | 使用的 Claude 模型 | claude-sonnet-4 |
| 权限模式 | 工具执行权限 | 正常 |
| 最大 Tab 数 | 同时打开的 Tab 数量 | 5 |
| 自动滚动 | 新消息自动滚动 | 开启 |
| 自动生成标题 | 自动生成对话标题 | 开启 |

### 配置文件

配置文件位于 `data/settings.json`：

```json
{
    "model": "claude-sonnet-4-20250514",
    "permissionMode": "normal",
    "maxTabs": 5,
    "enableAutoScroll": true,
    "enableAutoTitleGeneration": true,
    "locale": "zh-CN"
}
```

---

## 12. 常见问题

### Q: Claude 没有响应？

**A:** 检查以下几点：
1. Claude CLI 是否安装：`claude --version`
2. API 密钥是否配置：`echo $ANTHROPIC_API_KEY`
3. 网络是否正常

### Q: 如何更换模型？

**A:** 在设置中修改 `model` 配置项：
```json
{
    "model": "claude-opus-4-20250514"
}
```

### Q: 如何添加外部目录？

**A:** 使用斜杠命令：
```
/add-dir ./my-project
```

### Q: 如何查看对话历史？

**A:** 使用 `/resume` 命令：
```
/resume
```

### Q: MCP 服务器无法连接？

**A:** 检查：
1. MCP 服务器是否正在运行
2. 配置是否正确
3. 端口是否被占用

---

## 🎉 恭喜！

你已经掌握了猫娘 Claudian 的所有核心功能！

### 下一步

- 📚 阅读 [架构文档](docs/ARCHITECTURE.md) 了解内部原理
- 🔧 查看 [移植笔记](docs/CLAUDIAN_PORT_NOTES.md) 了解技术细节
- 🐱 探索猫娘 LLM 注入功能

### 获取帮助

- 提交 Issue: [GitHub Issues](https://github.com/neko/neko-claudian/issues)
- 查看文档: [在线文档](https://neko.dev/docs/claudian)

---

Made with 🐱 by Neko Team
