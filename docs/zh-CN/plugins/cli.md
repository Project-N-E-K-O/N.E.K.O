# 用命令创建和发布插件

想做一个新的 N.E.K.O 插件，不需要先手工准备目录、测试文件和 GitHub 配置。Plugin CLI 会先创建一个完整的插件项目；开发完成后，它还能检查插件并发布新版本。

这篇教程从创建项目开始，一直讲到把第一个版本发布到 N.E.K.O 插件市场。

::: info 当前使用方式
Plugin CLI 目前随 N.E.K.O 源码提供，还不能单独安装。下面的命令都需要在 N.E.K.O 源码目录中运行。
:::

## 开始前准备

你需要：

- 一份 N.E.K.O 源码；
- 已安装 [uv](https://docs.astral.sh/uv/) 和 Git；
- 一个用于保存插件的目录；
- 发布时使用的 GitHub 账号。

如果本地还没有 N.E.K.O 源码，先准备运行环境：

```bash
git clone --filter=blob:none \
  https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
uv sync
```

然后确认命令可以运行：

```bash
cd /path/to/N.E.K.O
uv run neko-plugin --help
```

## 创建插件项目

下面的命令会创建一个名为“天气助手”的普通插件：

```bash
uv run neko-plugin init weather_helper \
  --type plugin \
  --name "天气助手" \
  --output ../n.e.k.o_plugin_weather_helper
```

`--output` 指定最终目录。CLI 不会在这个目录后面再追加一层文件夹。

创建完成后，你会得到：

```text
n.e.k.o_plugin_weather_helper/
├── plugin.toml
├── config.example.toml
├── __init__.py
├── pyproject.toml
├── README.md
├── tests/test_smoke.py
├── ruff.toml
├── .gitignore
├── .vscode/
└── .github/workflows/
    ├── verify.yml
    └── release.yml
```

发布需要的 GitHub 配置已经准备好了，不需要自己编写 GitHub Actions 文件。

如果你要开发的是协议适配器，把类型改成 `adapter`：

```bash
uv run neko-plugin init my_adapter \
  --type adapter \
  --name "My Adapter" \
  --output ../n.e.k.o_plugin_my_adapter
```

## 开始开发

新项目中最常修改的是三个文件：

| 文件 | 用途 |
| --- | --- |
| `__init__.py` | 插件的 Python 代码 |
| `plugin.toml` | 插件名称、版本、入口和运行设置 |
| `pyproject.toml` | 插件使用的第三方 Python 库 |

先从生成的 `__init__.py` 示例开始修改。插件配置的完整写法见[插件配置说明](./plugin-toml)。

## 安装插件需要的第三方库

如果你在 `pyproject.toml` 中添加了 Python 库，需要把这些库复制到插件自己的 `vendor/` 目录：

```bash
uv run neko-plugin sync ../n.e.k.o_plugin_weather_helper --clean
```

没有第三方库时，这条命令会直接成功，不会创建多余内容。

每次修改依赖后，都应重新运行 `sync --clean`。不要使用 `requirements.txt`。

## 检查插件

开发过程中先运行普通检查：

```bash
uv run neko-plugin check ../n.e.k.o_plugin_weather_helper
```

它会检查插件配置、Python 语法、入口类、依赖和项目文件，并告诉你具体需要修改什么。

准备发布时，再运行完整检查：

```bash
uv run neko-plugin check -r ../n.e.k.o_plugin_weather_helper
```

完整检查会执行插件测试、生成安装包，并确认生成的包没有损坏。安装包默认保存在 N.E.K.O 源码中的：

```text
plugin/neko_plugin_cli/target/
```

只想生成本地安装包时，可以运行：

```bash
uv run neko-plugin build ../n.e.k.o_plugin_weather_helper \
  --target-dir ../plugin-builds
```

`build` 只生成文件，不会发布版本。

## 把项目推送到 GitHub

CLI 不会替你创建 GitHub 仓库，也不会替你提交代码。发布前需要完成下面的 Git 操作。

首先，在 GitHub 创建名为：

```text
n.e.k.o_plugin_weather_helper
```

的仓库。仓库名必须与插件 ID 对应。

然后提交并推送代码：

```bash
cd ../n.e.k.o_plugin_weather_helper
git add .
git commit -m "feat: first release"
git remote add origin \
  https://github.com/your-name/n.e.k.o_plugin_weather_helper
git push -u origin main
```

如果创建项目时已经使用 `--remote` 添加了 `origin`，这里不需要再次运行 `git remote add`。

## 提交插件审核

第一次发布前，先打开 [N.E.K.O 插件市场](https://market.project-neko.cn)，登录后提交刚才的 GitHub 仓库，并等待插件审核通过。

这一步只需要为一个插件做一次。CLI 不会替你注册插件，也不会替你提交审核。审核通过后，Market 才知道这个 GitHub 仓库属于哪个插件，之后的新版本才能由 `publish` 自动加入 Market。

## 发布新版本

确认 `plugin.toml` 中的版本号正确，例如：

```toml
[plugin]
version = "0.1.0"
```

工作区必须没有未提交的修改，而且当前提交已经推送到 GitHub。

回到 N.E.K.O 源码目录，运行：

```bash
cd /path/to/N.E.K.O
uv run neko-plugin publish ../n.e.k.o_plugin_weather_helper
```

接下来 CLI 会：

1. 再做一次发布前检查；
2. 为 `0.1.0` 创建并推送 `v0.1.0` 标签；
3. 等待 GitHub 生成 Release 和插件安装包；
4. 通知 N.E.K.O 插件市场读取这个版本。

通知插件市场不需要 Market 登录信息。Git 标签和代码仍通过你自己的 GitHub 凭据推送。

同一条命令可以安全重试。如果标签已经指向当前提交，CLI 会继续等待发布结果；如果同名标签指向另一份代码，CLI 会停止并要求你使用新的版本号。

## 已有插件项目

已有项目不需要重新创建。可以先查看标准 GitHub 配置需要怎样更新：

```bash
uv run neko-plugin setup-repo /path/to/existing-plugin \
  --upgrade-github-actions \
  --dry-run
```

确认没有冲突后执行更新：

```bash
uv run neko-plugin setup-repo /path/to/existing-plugin \
  --upgrade-github-actions
```

这个操作只管理：

```text
ruff.toml
.github/workflows/verify.yml
.github/workflows/release.yml
```

如果这些文件包含无法识别的自定义内容，CLI 会停止，不会覆盖它们。

## 发布中断后继续

通常只需要重新运行：

```bash
uv run neko-plugin publish /path/to/plugin
```

如果需要单独继续某一步，可以使用：

```bash
# 只创建并等待 GitHub Release
uv run neko-plugin publish github /path/to/plugin

# GitHub Release 已存在，只通知 Market
uv run neko-plugin publish market \
  https://github.com/owner/repository/releases/tag/v0.1.0
```

这两个模式用于恢复中断或排查问题。正常发布始终优先使用 `publish /path/to/plugin`。

## 命令速查

| 命令 | 什么时候使用 |
| --- | --- |
| `init` | 创建一个完整的新插件项目 |
| `setup-repo` | 更新已有项目的标准 GitHub 配置 |
| `sync` | 更新插件的 `vendor/` 第三方库 |
| `check` | 开发过程中检查插件 |
| `check -r` | 发布前执行测试、打包和完整检查 |
| `build` | 只生成本地安装包 |
| `publish` | 发布 GitHub Release 并通知插件市场 |
| `install` | 把本地安装包安装到指定目录，主要用于调试 |
| `analyze` | 比较多个插件的 SDK 和依赖，准备组合包 |

查看某个命令的全部参数：

```bash
uv run neko-plugin <命令> --help
```

## 常见问题

### 提示工作区有未提交的修改

先提交或暂存修改。CLI 不会猜测哪些文件应该进入发布版本。

### 提示 HEAD 尚未推送

先运行 `git push`。发布标签只能绑定到已经存在于 GitHub 的提交。

### 提示标准发布配置不是当前版本

运行：

```bash
uv run neko-plugin setup-repo /path/to/plugin \
  --upgrade-github-actions
```

### 提示外部依赖没有进入 vendor

运行：

```bash
uv run neko-plugin sync /path/to/plugin --clean
```

### GitHub 一直没有生成 Release

打开插件仓库的 **Actions** 页面，查看 Release 工作流失败在哪一步。修复问题后提交新版本；不要删除并重新使用已经发布过的版本号。
