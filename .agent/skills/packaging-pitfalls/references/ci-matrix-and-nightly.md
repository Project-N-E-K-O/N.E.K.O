# 构建矩阵与 nightly 发布

涉及的文件：`.github/workflows/build-desktop.yml`（跨平台，定时跑并发布 nightly）、`build-desktop-windows.yml`（手动触发，以 `windows_only: true` 调用前者）、`build-desktop-linux.yml`（手动触发的 Linux 单平台镜像，只上传 artifact，不发布）。

workflow 里很多步骤带着长注释，写的是当时为什么这么做。改之前先读注释；下面是跨步骤的、注释里拼不出全貌的部分。

## 架构陷阱

### runner label 不等于你以为的架构（#2898）

- `macos-15`、`macos-latest` 是 Apple Silicon 镜像；x64 的 label 是 `macos-15-intel`。权威来源是 `actions/runner-images` 仓库 README 里的 label 表。
- `actions/setup-python` 的 `architecture: x64` 只让 Python 解释器走 Rosetta。Nuitka 照样生成 arm64 的二进制。mac-x64 腿曾经在 Apple Silicon 镜像上跑了几个月，Intel Mac 上启动就报 `Bad CPU type in executable`，流水线全程是绿的。

现在有两道闸：

1. `tests/unit/test_desktop_build_arch_contract.py`：维护一张「runner label → 原生架构」表 `_RUNNER_NATIVE_ARCH`。矩阵里出现表里没有的 label 会失败；每条腿声明的 `expect_arch` 必须等于 runner 的原生架构；同一平台的 Electron 腿和后端腿架构必须一致。
2. `scripts/check_dist_arch.py`：在上传 artifact 之前直接解析 Mach-O / ELF / PE 头，检查主程序和包内每个原生二进制的架构和容器格式。不依赖 `lipo` 或 `file`，三个平台行为一致。

**规则**：动任何 `runs-on` 之前先查 label 表；新 label 先查清原生架构再加进 `_RUNNER_NATIVE_ARCH`。

另外，Windows 后端腿实际跑在 `windows-2022`（唤醒词运行时要用固定版本的 Visual Studio 生成器），Linux x64 腿锁在 `ubuntu-24.04`（更新的镜像上 C 编译阶段 runner 会失联）。这两处不要顺手改成 `-latest`。

### linux-arm64 试验腿（#3040）

这条腿上有三个互不相干的问题，注释分散在三个地方：

1. **链接溢出**：AArch64 的跳转指令半径只有 128MB 左右，Nuitka 把几千个目标文件链成一个可执行文件时代码段超过了这个半径，GNU ld 不会自动补跳转 thunk，链接报 `R_AARCH64_CALL26 relocation truncated`。做法是该腿改用 lld，通过矩阵字段 `nuitka_ldflags` 注入到 Nuitka 步骤的 `LDFLAGS`。还压不住时的下一步是加 `nuitka_ccflags`，不用改步骤本身。
2. **绿着出 x64 产物**：桌面壳的打包配置把 Linux 三个 target 的架构写死成 x64。electron-builder 只有在命令行显式给出 target 名时才会无视配置里的架构；只传 `--arm64` 的结果是在 arm64 机器上打出一整套 x64 产物，文件名和真正的 x64 腿逐字相同，到了 nightly 扁平的 `release/` 目录里互相覆盖。做法是矩阵字段 `builder_target_args` 里重复传 `-c.linux.target=...`，把 target 列表整个换掉。x64 腿这个字段为空。
3. **不进 Portable 增量更新通道**：桌面壳的 Portable 更新目前不认 linux + arm64 这个目标。矩阵字段 `skip_portable_channel` 把相关的三个步骤整体跳过，arm64 只出 AppImage / deb / tar.gz。要把 arm64 接进更新通道，得先改桌面壳，是单独的产品决定。

Steam 的原生库在仓库里只有 x86-64 一份，非 x86_64 的 Linux 上构建前会整块剥掉；Steam 也没有 arm64 的 Linux 客户端。

**规则**：评审里有人对 arm64 或 target 解析提意见时，不能只凭 workflow 文件推断，要在桌面壳的检出里实际跑一遍相关函数再回答。`continue-on-error` 只能挡住「红」，挡不住「绿着出错产物」。

### 各平台都入库的原生库

`steamworks/` 下三个平台的原生库都在仓库里，`--include-package=steamworks` 会把它们全当成包数据收进去。所以每个平台的 Nuitka 步骤开头都先 `rm -f` 掉别的平台的那几份，否则 Nuitka 会因为架构不匹配直接 FATAL。往 `steamworks/` 加文件，或者再加一个「各平台原生库都入库」的包时，照这个做法处理，并确认 `check_dist_arch.py` 的容器格式检查仍然通过。

### macOS 的 bash 是 3.2

三个 Portable 更新步骤用一个可能为空的数组传可选参数，展开必须写成 `${ARR[@]+"${ARR[@]}"}`。直接写 `"${ARR[@]}"` 在 Linux 和 Git Bash 上是绿的，只有 macOS 两条腿会在 `set -u` 下报 `unbound variable`。给 workflow 写 bash 时，凡是 macOS 也会跑的步骤，都按 3.2 的能力写。

## 产物传递

- **Unix 腿的后端先打 tar 再上传**（`Pack Python backend (Unix)`），`build-electron` 和 `nightly` 任务里各有对偶的解包步骤。原因是 `actions/upload-artifact` 会把符号链接展开成实体文件并丢掉可执行位。macOS 打 tar 时带 `COPYFILE_DISABLE=1`，避免 bsdtar 把扩展属性写成一堆 `._*` 文件混进包里。
- `nightly` 任务把后端重新打成 zip 时用 `zip -y`，符号链接照原样存成链接。
- Windows 腿没有符号链接问题，直接上传目录。
- `build-desktop-linux.yml` 目前仍是直接上传目录，再在下游 `chmod +x`。给 Unix 平台新增上传步骤时照跨平台 workflow 的 tar 做法写。
- macOS 后端在 Nuitka 生成 `.app` 时已经签过一次；之后往包里装插件载荷会破坏封印，所以有一步 ad-hoc 重签。往 `.app` 里追加文件的新步骤必须排在这一步之前。

## nightly 发布

### 发布条件

`nightly` 任务的条件是：不是 push 触发、没被取消、`build-python` 整体成功、`build-electron` 不是 failure。试验腿带 `continue-on-error`，红了不影响这两个结果，但它的 artifact 不会上传，所以发布说明里的 arm64 两行是按 `artifacts/` 下对应目录是否存在动态拼出来的。

定时触发的构建一律不签名：`CSC_IDENTITY_AUTO_DISCOVERY` 设为 `false`，macOS 腿还会把壳的签名身份、hardened runtime、entitlements 和公证钩子从打包配置里去掉。

Portable 的每个 `*_manifest.json` 都要有对应的 `.sig`；签名密钥没配或者一个 manifest 都没找到时，任务直接失败，不会发布没签名的 manifest。

### 2 GiB 上限

GitHub release 单个附件不能超过 2 GiB，而且要等上传到那个文件时才报 422。`Check release asset sizes` 在碰旧 nightly 之前先检查：有文件超限就直接失败，旧 nightly 原样保留；超过上限的九成给警告。

包体积逼近上限时，先查是不是有东西被重复打包（符号链接展开、同一个框架存多份、上一轮构建残留），再考虑砍内容。

排查手法（实测观察）：想知道某个 artifact 里什么最占地方，不用把整个包下载下来。用 HTTP Range 只取 zip 末尾的中央目录就能列出条目和大小。`gh api` 会跟随重定向把整个文件下完，要先拿到重定向后的下载地址，再用 `curl` 带 Range 头取。

### 四步发布（#3564）

以前是「先删旧 nightly，再建新的」。新建或上传一旦失败（权限错误、附件超限都出现过），仓库里就没有 nightly 了。现在的顺序：

1. 在临时 tag `nightly-staging-<run id>-<attempt>` 上建草稿，把附件全部传完。草稿不会真的创建 tag。
2. 以临时 tag 正式发布。这一步等于提前验证当前令牌能在这个提交上建 tag。
3. 删旧 nightly，并且单独再删一次 `nightly` tag（release 没了但 tag 还在的话，下一步改 tag 会沿用旧 tag 指向的旧提交）。
4. 把这份 release 的 tag 改成 `nightly`，删掉临时 tag。这一步失败会重试三次。

失败时的状态：第 1、2 步失败，旧 nightly 不动，本次草稿被清掉；第 4 步失败，新 release 仍以临时 tag 公开挂着，可以手工改 tag，后续运行在自己第 4 步成功之前不会删它。

清理残留的 staging release 时有两个细节不要改掉：列表接口可能返回旧数据，所以删除前逐条读回来确认 tag 仍以 `nightly-staging-` 开头；刚改完 tag 的那份 release 要按 id 显式排除。

**规则**：以后改发布逻辑，保持「新的传完、验完，再删旧的」这个顺序。任何会在删除之后才暴露的失败（体积、权限、配额），都要挪到删除之前检查。

`windows_only` 这条路径不走四步：它在已有的 `nightly` 上逐个删掉 Windows 的旧附件再上传新的，其他平台的附件保留。

## 契约测试依赖 workflow 的写法

有两个测试是直接解析 workflow 文本的，改写法会让它们失效或误报：

- `tests/unit/test_pyav_packaging_contract.py` 只认步骤脚本里基础缩进的逐行追加：`NUITKA_OPTS="$NUITKA_OPTS <参数>"`（bash）和 `set NUITKA_OPTS=%NUITKA_OPTS% <参数>`（cmd）。写在 `if` 分支里（缩进更深）的追加不算「无条件参数」。一行一个参数的写法要保持。
- `tests/unit/test_desktop_build_arch_contract.py` 假定矩阵是 `fromJSON(inputs.windows_only && '[...]' || '[...]')` 这种恰好两个分支的表达式。再加分支或改成别的形式，要同步改测试里的解析。

## 其他已知约束

- 手动或 tag 指定的版本号在 `version` 任务里按 semver 语法校验，不合法的几秒钟内就失败，不用等两小时的后端构建。没指定版本时用「日期-短提交号」，写进壳的打包配置前会补成 `0.0.0-` 前缀。
- 检查某个依赖的安装版本时，期望值从 `pyproject.toml` 读，不要在 workflow 里再写一份字面量。曾经因为依赖升了一个小版本、workflow 里写死的版本没跟上，nightly 连续判红。
- 整个 workflow 共用一个并发组，且不取消进行中的运行，同组的运行会排队。
- Windows 腿在 `uv sync` 之后会编译并强制重装一个打过补丁的唤醒词运行时，并断言它的版本号。这之后的步骤一律用 `uv run --no-sync`：再做一次同步会把它换回 PyPI 上的版本。在这条腿上加新步骤时照做。
