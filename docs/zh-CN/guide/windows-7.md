---
title: Windows 7 支持说明（实验性）
description: 在 Windows 7 上以源码方式运行 N.E.K.O 的实验性指引：Python 3.11 运行时选择、setup_win7.bat 安装步骤、功能支持矩阵、已知限制与真机验证清单。
seoSchemaType: WebPage
---

# Windows 7 支持说明（实验性）

::: warning 状态
Windows 7 **不是上游支持的平台**。上游要求 Windows 8.1+（Python 3.11、uv、Node.js ≥ 20、Electron 41 均已放弃 Win7）。本页描述的 `win7` 分支只做**保守的 best-effort 适配**：保持 Python 3.11 与全部依赖锁定不变，补充引导提示与安装脚本。**未经官方测试，能不能跑、跑到什么程度，以你的真机验证为准。**
:::

## 支持矩阵

| 组件 / 功能 | Win7 状态 | 说明 |
| --- | --- | --- |
| 后端三服务器（源码运行 `launcher.py`） | ⚠️ 实验性 | 依赖非官方的 Python 3.11 Win7 构建 |
| Web 浏览器访问（`http://localhost:48911`） | ✅ 预期可用 | 请用 Chrome 109 或 Firefox 115 ESR |
| 依赖安装 `setup_win7.bat` | ✅ 设计目标 | 纯 `pip`，不依赖 uv / Node |
| 前端静态资源 | ✅ 无需构建 | 仓库已内置构建产物；Node 20 不支持 Win7，改前端请在其他系统构建后拷贝 `static/` |
| Electron 桌面端（`N.E.K.O.exe`） | ❌ 不可用 | Electron 41 基于 Chromium 14x，要求 Windows 10+ |
| `uv` 包管理 | ❌ 不可用 | uv 官方最低 Windows 10；请用 pip |
| Playwright 浏览器自动化 | ❌ 不可用 | 浏览器内核不再支持 Win7 |
| OCR 屏读 / 本地 embedding / 声纹（onnxruntime、pywin32 等原生轮子） | ⚠️ 待真机验证 | 官方轮子未以 Win7 为测试目标，见下方风险清单 |
| Steam 版 / 一键打包 exe（Nuitka / PyInstaller） | ⚠️ 未验证 | 本分支只保证源码路径 |

## 前置条件

- Windows 7 **SP1** x64（32 位理论可行但未验证）
- 补丁 [KB2533623](https://www.microsoft.com/zh-cn/download/details.aspx?id=26767)（提供 `os.add_dll_directory` 依赖的运行库；缺失时启动器会自动跳过该调用，不影响启动）
- [Microsoft Visual C++ 2015-2022 Redistributable (x64)](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist) （python / 原生扩展 DLL 需要）
- 磁盘空间约 3 GB，可访问 PyPI 与 GitHub 的网络

## 第一步：安装 Python 3.11（最关键）

官方 python.org 明确声明 **Python 3.11 仅支持 Windows 8.1+，Win7 请安装 3.8**——但本项目强制 `requires-python == 3.11.*`（代码用到 `tomllib`、`TaskGroup` 等 3.11 特性），而 Python 3.8 跑不起来。因此只能使用**社区维护的 Windows 7 版 Python 3.11 非官方构建**，例如 [Strangemother/PythonWin7-minimal](https://github.com/Strangemother/PythonWin7-minimal)（提供 3.11.x 的 Win7 安装器）。

::: danger 第三方构建，风险自担
非官方构建不由 Python 核心团队发布与测试。安装前请杀毒扫描，仅从可信来源下载；本项目不为其安全性背书。若你有更可信的 Win7 Python 3.11 来源，欢迎提 issue 更新本页。
:::

- ❌ 不要用官方 `python-3.11.x-embed-amd64.zip`（同为官方构建，DLL 在 Win7 上无法加载）
- ❌ 不要降级到 Python 3.8 绕过（项目不支持）
- ✅ 验证安装：`python -c "import sys; print(sys.version)"` 输出 `3.11.x` 且无 DLL 报错

## 第二步：安装项目依赖

克隆或下载本分支代码后，在仓库根目录任选一种方式：

**方式 A：一键脚本（推荐）**

```bat
setup_win7.bat
```

或指定解释器路径：

```bat
setup_win7.bat "C:\path\to\python-3.11.exe"
```

脚本会创建 `.venv` 并用 pip 安装 `requirements.txt` 中锁定的全部依赖。

**方式 B：手动**

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

::: tip
不要执行 `uv sync` / `uv run`——uv 不支持 Windows 7；也不要尝试构建前端，`static/` 下的构建产物已随仓库提供。
:::

## 第三步：启动

```bat
.venv\Scripts\python.exe launcher.py
```

浏览器访问 `http://localhost:48911` 完成 API Key 配置。启动时若检测到 Win7，控制台会打印一次引导横幅（设 `NEKO_WIN7_SILENT=1` 可关闭）。

## 浏览器要求

Win7 上最后一代可用浏览器：**Chrome 109**（最终版）、**Firefox 115 ESR**（最终版）。请避免使用 IE / 旧 Edge。语音输入、摄像头等浏览器权限在这些版本上的表现需真机确认。

## 已知限制与风险（真机重点验证项）

`requirements.txt` 由上游锁定，其中的原生二进制轮子**没有一个以 Win7 为测试目标**。若启动或某功能报 `DLL load failed`、`找不到指定的模块/过程`，大概率是下列包之一：

| 包 | 用途 | 建议 |
| --- | --- | --- |
| `pywin32==311` | Windows 系统集成 | 报错时尝试回退较旧的 build 并回报 issue |
| `cryptography==45.0.7` | TLS / 加密 | 同上 |
| `onnxruntime==1.25.0` | OCR 屏读、本地 embedding | 官方文档称「可能兼容 Win7+」，需实测；失败时对应功能不可用，启动器其余部分仍可运行（均为懒加载导入） |
| `playwright==1.63.0` | 浏览器自动化 | Win7 上确定不可用，属预期 |

回退版本示例：`.venv\Scripts\python.exe -m pip install pywin32==306`，验证通过后请把可用组合回报到 issue，我们会评估是否写进本分支。

## 排错

| 现象 | 处理 |
| --- | --- |
| PowerShell 下载报 `Could not create SSL/TLS secure channel` | 先执行 `[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12` 再重试 |
| pip 安装慢或超时 | `python -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple`（换回官方源同理去掉即可） |
| `python -m venv` 报错 | 确认用的是 Win7 构建的 3.11，且已装 VC++ 2015-2022 运行库 |
| 端口被占用 | 见 [安装渠道](./install-options) 与启动日志中的端口提示 |
| 中文路径乱码 / 崩溃 | 启动器已自动处理 UTF-8；如仍异常，手动设 `PYTHONUTF8=1` 后重试 |

## 真机验证清单

在 Windows 7 上逐项勾选，结果欢迎提 issue（附 Python 构建来源与版本）：

- [ ] `setup_win7.bat` 全程无报错，`.venv` 创建成功
- [ ] `launcher.py` 启动三服务器，控制台出现 Win7 引导横幅
- [ ] 浏览器（Chrome 109 / Firefox 115 ESR）打开配置页正常
- [ ] 配置 API Key 后文字对话可用
- [ ] 语音对话 / 麦克风可用
- [ ] 屏读（OCR）、记忆检索可用（onnxruntime 正常加载）
- [ ] Live2D / 桌宠模式渲染正常
- [ ] 长时间运行稳定（内存、断线重连）

> 本页对应 `win7` 分支，最后更新：2026-10-05。相关文档：[前置条件](./prerequisites) · [开发环境搭建](./dev-setup) · [快速开始](./quick-start)
