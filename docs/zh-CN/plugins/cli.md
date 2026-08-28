# 把插件发布到 N.E.K.O 插件市场

这篇教程接着[快速开始](/zh-CN/plugins/quick-start)往下走：把已经能在源码版 N.E.K.O 中运行的插件上传到 GitHub，提交第一次审核，发布第一个可安装版本，然后继续发布后续版本。

下面使用快速开始中创建的 `hello_world` 作为示例。它的源码一直放在：

```text
N.E.K.O/plugin/plugins/hello_world/
```

这个目录就是插件的 Git 仓库，也是开发时实际运行的源码。整个流程不需要复制插件、创建符号链接，也不需要把安装包导入自己的开发环境。

完成后，整个流程会是：

```text
源码开发
  → 推送到 GitHub
  → 提交 Market 首次审核
  → 审核通过
  → 发布 GitHub Release
  → Market 出现可安装版本
  → 修改源码并继续发布新版本
```

::: info 开始前
除非步骤中明确写了 `cd`，下面的 `uv run neko-plugin ...` 命令都在 N.E.K.O 源码根目录运行。
:::

## 1. 确认插件已经可以发布

先确认插件目录中至少有这些文件：

```text
plugin/plugins/hello_world/
├── .git/
├── .github/workflows/
│   ├── verify.yml
│   └── release.yml
├── plugin.toml
├── config.example.toml
├── __init__.py
└── tests/
```

`.git/` 表示这个插件目录本身就是一个 Git 仓库。快速开始中的 `neko-plugin init` 已经创建了它；虽然插件位于 N.E.K.O 源码目录中，但插件的提交、远程仓库和版本标签都属于这个插件自己的仓库。

运行检查：

```bash
uv run neko-plugin check hello_world
```

如果插件添加了第三方 Python 库，先更新插件自己的 `vendor/`：

```bash
uv run --with pip neko-plugin sync hello_world --clean
uv run neko-plugin check hello_world
```

有 error 或 `[FAIL]` 时先修复，不要继续发布。没有使用第三方库时，不需要运行 `sync`。

最后打开 `plugin/plugins/hello_world/plugin.toml`，确认插件 ID 和第一个版本号：

```toml
[plugin]
id = "hello_world"
version = "0.1.0"
```

插件 ID 发布后应保持不变。每一个已经发布过的版本号也不能再次使用。

## 2. 创建 GitHub 仓库并推送源码

在 GitHub 创建一个公开的空仓库，仓库名必须是：

```text
n.e.k.o_plugin_hello_world
```

命名规则是 `n.e.k.o_plugin_<插件 ID>`。创建仓库时不要让 GitHub 自动添加 README、`.gitignore` 或 License，因为插件目录中已经有需要提交的文件。

然后进入插件目录，提交并推送源码：

```bash
cd plugin/plugins/hello_world
git add .
git commit -m "feat: first release"
git remote add origin https://github.com/your-name/n.e.k.o_plugin_hello_world.git
git push -u origin main
cd ../../..
```

如果 `origin` 已经存在，不要再次运行 `git remote add`。先在插件目录中运行下面的命令，确认它指向正确的 GitHub 仓库：

```bash
git remote -v
```

推送完成后，打开 GitHub 仓库的 **Actions** 页面，等待 **Verify N.E.K.O Plugin** 工作流通过。它会检查代码、依赖、插件配置、测试和构建结果。

::: warning 提交的是插件仓库
在 `plugin/plugins/hello_world/` 中运行 Git 命令。不要把整个 N.E.K.O 仓库当作你的插件仓库推送。
:::

## 3. 提交第一次审核

打开 [N.E.K.O 插件市场投稿页](https://market.project-neko.cn/#/upload)并登录，然后依次完成：

1. 填写 GitHub 仓库地址，例如 `https://github.com/your-name/n.e.k.o_plugin_hello_world`。
2. 点击“读取仓库信息”。
3. 确认插件名称，选择分区和 1–5 个标签；介绍可以按需要填写。
4. 点击“提交审核申请”。
5. 在“我的插件”中打开这次申请，查看检查结果和审核意见。

这里提交的是 GitHub 仓库中的一个确定版本。Market 会把当前分支解析成完整的 commit，并审核这份不会移动的代码快照。此时还没有提交 `.neko-plugin` 安装包，也没有发布可下载版本。

一个插件只提交一次首次申请。在申请没有关闭时，不要为同一个仓库反复创建新申请。

### 审核中需要修改怎么办

如果审核员要求修改：

1. 继续修改 `plugin/plugins/hello_world/` 中的源码。
2. 重新运行 `neko-plugin check hello_world`。
3. 在插件自己的仓库中 commit 并 push。
4. 运行 `git rev-parse HEAD`，复制新的完整 commit SHA。
5. 回到原申请的“版本更新”区域，填写修复说明和新的 commit。
6. 点击“确认提交新 Revision”。

新 Revision 会替换原来等待审核的代码快照。它不是插件的新版本，也不会创建 Market Version。

如果申请已经被关闭，作者不能自行提交 Revision。被拒绝的申请需要审核员先重新打开，才能继续提交修改。

## 4. 等待插件审核通过

审核通过后，插件会进入 Market 的插件目录，但这时仍然可能没有下载按钮。

请把下面两个动作分清：

| 动作 | 得到什么 |
| --- | --- |
| 首次审核通过 | Market 中的插件条目 |
| 发布第一个版本 | 用户可以安装的插件版本 |

所以审核通过后还要继续下一步，发布第一个 GitHub Release 和 Market Version。

## 5. 发布第一个可安装版本

先确认准备发布的代码已经全部提交并推送：

```bash
cd plugin/plugins/hello_world
git status --short
git push
cd ../../..
```

`git status --short` 应当没有任何输出。然后在 N.E.K.O 源码根目录运行：

```bash
uv run neko-plugin publish hello_world
```

这条命令会连续完成：

1. 检查插件目录包含自己的 `.git/`，而且没有未提交修改。
2. 检查标准 `release.yml` 是当前版本。
3. 运行发布检查、测试、构建和安装包校验。
4. 确认当前 commit 已经推送到 `origin`。
5. 根据 `plugin.toml` 的 `0.1.0` 创建并推送 `v0.1.0` 标签。
6. 等待 GitHub Actions 创建 GitHub Release，并确认 `hello_world.neko-plugin`、`hello_world.market-release-check.txt` 和 `market-evidence.json` 三个资产都已上传完成且可以下载。
7. 通知 Market 读取这个 Release，并把它发布到 `stable` 渠道。

正常完成时，终端会先显示 GitHub Release 已就绪，再显示 Market 已发布对应版本。

`publish` 推送版本标签时使用你的 GitHub 凭据，它不会推送插件代码；通知 Market 的这一步不需要填写 Market 密码或令牌。Market 只接受已经审核通过、仓库匹配并且标准发布验证成功的插件。

::: warning 只推送标签还没有完成发布
`release.yml` 负责创建 GitHub Release，但它不会自己在 Market 中创建版本。正常情况下应当让 `neko-plugin publish` 完整运行到“Market 发布成功”。
:::

## 6. 确认用户已经可以安装

发布成功后检查两个位置：

1. 打开 GitHub 仓库的 **Releases** 页面，确认 `v0.1.0` 下存在 `hello_world.neko-plugin`、检查报告和发布证据。
2. 打开 Market 中的插件详情，确认版本列表中出现 `0.1.0`，并且 stable 最新版本不再为空。

Market 保存的是 GitHub Release 中安装包的地址和校验值。开发者不需要再把 `.neko-plugin` 文件手动上传到 Market。

到这里，第一个版本才算完整发布。

## 7. 发布后续版本

首次审核通过后，普通功能更新不需要重新提交审核申请。以后每一版都按下面的顺序进行：

1. 修改 `plugin/plugins/hello_world/` 中的源码。
2. 在 `plugin.toml` 中换成一个从未发布过的新版本号。
3. 如果依赖有变化，运行 `uv run --with pip neko-plugin sync hello_world --clean`。
4. 运行 `neko-plugin check hello_world`。
5. 在插件仓库中 commit 并 push。
6. 运行 `neko-plugin publish hello_world`。

例如把版本从 `0.1.0` 更新到 `0.1.1` 后：

```bash
uv run neko-plugin check hello_world

cd plugin/plugins/hello_world
git add .
git commit -m "fix: improve greeting"
git push
cd ../../..

uv run neko-plugin publish hello_world
```

Market 会把新发布的 stable 版本设为最新版本。它按照版本在 Market 中的发布时间切换 latest，不会比较版本号大小，所以不要在发布新版本后再补发一个较旧的版本。

## 8. 发布 beta 或填写更新说明

默认的 `neko-plugin publish hello_world` 只发布 stable，并且不填写 Changelog。

如果需要选择 beta 渠道或填写更新说明，先只创建 GitHub Release：

```bash
uv run neko-plugin publish github hello_world
```

GitHub Release 就绪后：

1. 登录 Market，打开这个插件的版本页面。
2. 点击“发布新版本”。
3. 选择刚生成的 GitHub Release。
4. 选择 `stable` 或 `beta`。
5. 按需要填写 Changelog，然后确认发布。

同一插件中的版本号全局唯一，不按 stable 和 beta 分开计算。已经作为 beta 发布的版本不能再使用同一个版本号发布为 stable；正式发布时必须使用新的版本号和新的 Release。

## 9. 发布中断后继续

如果命令中断，先看它停在哪一步。

### GitHub Release 已经成功

直接重新运行完整命令：

```bash
uv run neko-plugin publish hello_world
```

如果远程标签仍然指向当前 commit，CLI 会继续等待或通知 Market，不会创建第二个版本。

也可以复制 GitHub Release 页面地址，只重试 Market 通知：

```bash
uv run neko-plugin publish market \
  https://github.com/your-name/n.e.k.o_plugin_hello_world/releases/tag/v0.1.0
```

这个模式仍然只会发布经过标准验证的 stable 版本。

### GitHub Actions 失败

打开插件仓库的 **Actions** 页面查看失败步骤。

- 如果只是临时网络问题，没有修改代码，可以在 GitHub 重新运行失败的工作流，再重新执行 `publish`。
- 如果必须修改代码或发布配置，修复后使用新的版本号，commit、push，再发布新的 tag。不要让同一个版本号指向另一份代码。

### 标准发布配置不是当前版本

先预览 CLI 准备修改什么：

```bash
uv run neko-plugin setup-repo hello_world \
  --upgrade-github-actions \
  --dry-run
```

确认没有冲突后执行更新：

```bash
uv run neko-plugin setup-repo hello_world \
  --upgrade-github-actions
```

然后在插件仓库中提交并推送 `.github/workflows/` 和 `ruff.toml` 的修改，再重新发布。CLI 发现无法识别的自定义工作流内容时会停止，不会直接覆盖。

### Market 提示找不到可发布插件

确认以下三件事：

- 首次审核已经通过，不只是已经提交申请。
- Market 中绑定的仓库就是当前 `origin` 指向的仓库。
- GitHub Release 已经由标准 `release.yml` 成功生成并通过验证。

`publish` 不能替代首次投稿，也不能把尚未审核通过的仓库直接发布到 Market。

## 10. 撤回错误版本

如果已经发布的版本有严重问题，可以在 Market 的版本管理页面填写原因并撤回。

撤回是单向操作：版本不能恢复，也不能使用相同版本号或同一个 GitHub Release 重新发布。正确做法是修复源码，使用新版本号创建新的 Release，再发布新版本。

## 一页流程速查

### 第一次发布

```text
check
  → Git commit
  → push GitHub main
  → 等待 Verify 通过
  → Market 提交首次审核
  → 按审核意见提交 Revision
  → 审核通过
  → neko-plugin publish
  → GitHub Release
  → Market stable Version
```

### 后续迭代

```text
修改源码
  → 更新 plugin.toml 版本号
  → check
  → Git commit / push
  → neko-plugin publish
```

## 现有插件需要满足什么

这篇教程假定插件目录本身包含 `.git/`，并且有标准 `verify.yml` 和 `release.yml`。使用当前快速开始中的 `neko-plugin init` 创建的插件已经满足这些条件。

如果现有插件只由外层 N.E.K.O 仓库跟踪，插件目录自身没有 `.git/`，当前 `publish` 会停止；CLI 目前没有把这种目录自动转换成可发布插件仓库的命令。不要用复制目录、安装包导入或符号链接伪装成发布流程。
