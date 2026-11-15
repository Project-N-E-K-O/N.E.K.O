---
name: local-env-pitfalls
description: 本地开发环境的坑：uv 和共享 .venv、在 git worktree 里干活、Windows 上的 shell 和编码问题、用脚本批量改文件。在 worktree 里跑测试或起应用、借用别的检出的环境、清理 worktree、用脚本或 heredoc 往文件里写代码之前读。
---

# 本地环境的坑

这里记的是「环境把人坑了」的那一类：命令没报错，但环境被弄坏了、文件被写坏了，或者结果根本不是你以为的那个东西跑出来的。测试本身的坑见 `testing-pitfalls`。

## uv 和 `.venv`

- 所有 Python 都用 `uv run`。`pyproject.toml` 把 Python 版本限定在 3.11，系统 Python 多半不满足。
- ⚠️ **环境已经建好之后，跑测试和脚本用 `uv run --no-sync`**（或者直接用 `.venv` 里的解释器）。不带 `--no-sync` 的 `uv run` 会先按 `uv.lock` 改动环境。下面两种情况会出事：
  - 这个 `.venv` 里有 lock 之外手装的东西（打包工具、本机编译的定制 wheel）。同步会把版本对不上的包换回 lock 里的版本。
  - 应用正在用这个 `.venv` 运行。Windows 上已加载的 `.pyd` 删不掉，同步卸到一半失败，留下「旧包已卸、新包没装」的环境，导入全挂，测试收集整片报错（实测观察）。
- 环境被同步弄坏之后，不要用全量 `uv sync` 去「修」，它会把手装的包一起卸掉。按 lock 里的版本把缺的包单独装回来：`uv pip install --python .venv/Scripts/python.exe --no-deps <包>==<版本>`。
- 只有确实要更新依赖时才跑 `uv sync`，并且先停掉应用。

## 在 git worktree 里干活

worktree 里没有 `.venv`，也没有 `node_modules`（它们不进版本库）。细节和命令见 `references/worktree.md`，要点：

- **起应用、做交互验证，在有 `.venv` 的主检出里做**，不在 worktree 里。worktree 用来写代码和开 PR。验证前先让主检出停在包含改动的提交上，否则跑的是旧代码。
- **跑 pytest**：当前目录切到 worktree 的根，用主检出 `.venv` 里的解释器跑。不要站在主检出里跑 worktree 的测试路径。
- **前端**：把主检出的 `node_modules` 用目录联接（junction）挂过来，或者在 worktree 里直接 `npm ci`。页面测试之前先构建。
- ⚠️⚠️ **删 worktree 之前先单独删掉里面的 junction。** `git worktree remove --force` 和递归删除会顺着 junction 删进真实目录，把主检出的 `.venv` 或 `node_modules` 删坏。
- ⚠️ **不要用 `git stash` 做「有我的改动 / 没有我的改动」的对照。** stash 存在公共的 `.git` 里，所有 worktree 共用；`pop` 可能把你没碰过的文件回退成旧内容。先提交，再用 `git checkout <提交> -- <文件>` 或另开一个 worktree 对比。
- 改文件时确认路径在 worktree 下面。主检出里有一份同名文件，路径写错了改动会落到主检出，把本该干净的 main 弄脏。
- 推送之前确认目标分支（见 `.agent/rules/neko-guide.md`）。

## Windows 上的 shell

细节见 `references/windows-shell-and-bytes.md`，要点：

- Bash（Git Bash）和 PowerShell 的语法不通用。`@'...'@` 只在 PowerShell 里是 here-string，在 bash 里会原样进入字符串；反引号在 bash 的双引号里是命令替换。多行或带 markdown 的正文一律先写文件，再用 `-F` / `--body-file` 传。
- ⚠️ 管道的退出码是最后一段的。`pytest ... | tail -3 && git commit` 里 `&&` 判的是 `tail`。要判前一段，先存返回码，或用 `${PIPESTATUS[0]}`。
- Git Bash 里常常没有独立的 `jq`，解析 `gh` 的输出用它自带的 `--jq`（见 `pr-workflow` 的 `references/github-api-pitfalls.md`）。
- PowerShell 不替外部命令展开通配符；需要通配符的命令（例如 CI 里那条 `node --test tests/unit/*.test.js`）在 Git Bash 里跑。给 `node --test` 传目录在 Windows 上不工作，要传文件。
- 路径上限 260 字符。临时目录、worktree 不要放在很深的路径下。
- 子进程的输出接到管道时按系统代码页编码，不是 UTF-8。脚本里打印非 ASCII 字符在英文 Windows 上会崩。

## ⚠️ 往文件里写代码时，转义序列会变成真实的字节

通过 shell 的 heredoc、`python -c`，或者通过编辑工具的参数，把含反斜杠转义序列的源码写进文件时，转义序列可能在途中被多解释一层：本想在源码里留下「反斜杠加 n」两个字符，落盘的却是一个真实的换行、空字节或退格符。

- 症状不一定是报错。空字节会让 `.py` 变成二进制文件、导入时报 `source code string cannot contain null bytes`；但正则里的单词边界变成退格符之后，文件照样能导入，只是那条正则再也匹配不上。
- 写分隔符这类东西，优先选不需要转义的可见字符。
- 写完立刻检查落盘的内容，不要等导入失败。

规则和检查命令见 `references/windows-shell-and-bytes.md`。

## 用脚本批量改文件

详见 `references/bulk-edit-scripts.md`，要点：

- ⚠️ **全部锚点都命中之后再统一写盘。** 边改边写，中间一个断言失败，前面的文件已经落盘、后面的没写，代码停在半截状态，语法检查还能通过。
- 锚点断言唯一性：`assert s.count(old) == 1`。`old in s` 只证明存在；`replace(old, new, 1)` 换的是文件里第一处，不一定是你正在看的那一处。
- 先 `encode("utf-8")` 成功，再 `write_bytes`。直接 `write_text`，编码失败时文件已经被清空。
- 改完回读落点，确认改在了预期的函数里。

## references

- `references/worktree.md`：在 worktree 里跑测试、挂前端依赖、借用环境、清理 worktree 之前读。
- `references/windows-shell-and-bytes.md`：在 Windows 上写 shell 命令、用 heredoc 或脚本生成源码、遇到「本机过 CI 不过」的编码问题时读。
- `references/bulk-edit-scripts.md`：写任何一次改多个文件、或者按锚点做替换的脚本之前读。
