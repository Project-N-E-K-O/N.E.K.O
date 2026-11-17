# 在 git worktree 里干活

下文「主检出」指装好了 `.venv` 和前端依赖的那个检出，「worktree」指用 `git worktree add` 另开的目录。命令以 Windows 为例。

## 起应用：在主检出里

- 真正启动后端、从页面上做交互验证，在主检出里做：`uv run --no-sync python launcher.py`。worktree 没有 `.venv`，可选依赖组（原生扩展）也只装在主检出的环境里。
- ⚠️ 主检出跑的是主检出里的代码。要验证 worktree 里的改动，先让主检出停在包含这些改动的提交上：改动提交之后，在主检出里 `git switch --detach <提交>`（同一个分支不能同时被两个检出占用，所以用 detach），用 `git rev-parse HEAD` 确认，验完切回原来的分支。没做这一步，页面上验证通过也证明不了 worktree 里的改动有效。
- 改动涉及 `frontend/react-neko-chat/` 时，切过去之后还要在主检出的 `frontend/react-neko-chat` 里跑一次 `npm run build` 再启动。`static/react/neko-chat/` 是构建产物，不进版本库，启动时也不会自动重建；不构建的话页面加载的是上一次构建留下的旧聊天窗口。
- 为调试临时加的改动（比如打印）应用到主检出的对应文件上，测完还原。主检出平时保持干净。

## 跑 pytest：当前目录是 worktree 的根

```powershell
Set-Location <worktree>
& <主检出>\.venv\Scripts\python.exe -m pytest tests\unit\test_xxx.py
```

- ⚠️ 不要「站在主检出里、跑 worktree 的测试路径」。个别靠 `__file__` 自定位的静态测试碰巧能过，但依赖当前目录的测试（静态资源版本、存储生命周期、页面测试等）会成批失败，全是假失败。
- 主检出的 `.venv` 里，项目本身是以可编辑方式安装的，指向主检出的源码。pytest 靠根 `pytest.ini` 的 `pythonpath = .` 把当前检出排到前面，所以当前目录必须是 worktree 的根。在 pytest 之外跑脚本时，如果它要导入产品模块，先确认 `模块.__file__` 指向的是 worktree 而不是主检出（这一条由 `pytest.ini` 的注释和 `pyproject.toml` 推得，没有逐个场景实跑验证）。
- 借用主检出的环境时永远带 `--no-sync` 或直接用解释器路径，理由见 `SKILL.md`。也不要把主检出的 `.venv` 用 junction 挂进 worktree，清理时很容易删进真实目录。真要一个独立环境，就在 worktree 里自己 `uv sync` 一份。
- 临时目录：`plugin/tests/pytest.ini` 的 `--basetemp=../.pytest-run` 相对当前目录解析，并排的几个 worktree 会共用同一个目录。同时在多个 worktree 里跑测试时各自指定一个短路径的 `--basetemp`，跑完删掉（见 `testing-pitfalls` 的 `references/pytest-traps.md`）。
- worktree 如果建在主检出的某个子目录里，「按目录名跳过」的源码扫描可能把整个 worktree 跳过，扫描量为 0（见 `testing-pitfalls` 的 `references/guard-test-failure-modes.md` 第 9 类）。

## 前端依赖

以 `frontend/react-neko-chat` 为例，两种办法：

1. 在 worktree 的那个目录里直接 `npm ci`。最省心，多占一份磁盘。
2. 用 junction 把主检出的 `node_modules` 挂过来（两边 `package-lock.json` 一致时才可以）：

   ```powershell
   New-Item -ItemType Junction -Path "<worktree>\frontend\react-neko-chat\node_modules" -Target "<主检出>\frontend\react-neko-chat\node_modules"
   ```

   - 先确认主检出那边的 `node_modules` 不是空的，否则 vitest 报 `ERR_MODULE_NOT_FOUND`。
   - ⚠️ 用完立刻拆：`cmd /c rmdir "<worktree>\frontend\react-neko-chat\node_modules"`。`rmdir` 不带 `/s` 只删联接本身，不动目标。

之后在那个目录里 `npm run typecheck`、`npm test`、`npm run build` 都能跑。

- 类型检查用 `npm run typecheck`。这个包的 `tsconfig.json` 是引用式的，裸的 `tsc --noEmit` 一个文件都不检查，永远通过。
- Playwright 页面测试之前要先 `npm run build`，否则页面加载不到聊天窗口的构建产物（见 `testing-pitfalls` 的 `references/frontend-node-tests.md`）。
- 构建产物 `static/react/neko-chat/` 不进版本库，PR 只带源码。

## 做对照实验：不要用 `git stash`

想验证「这个失败是不是我的改动引起的」时，不要 `git stash push` 跑一遍再 `git stash pop`。

- 根因：stash 存在公共的 `.git` 里，所有 worktree 共用同一个栈。别的 worktree、以前的会话留下的 stash 都在里面，`pop` 到底应用了什么事后无法还原。实际出过的事：`pop` 之后两个从没碰过的文件被换成旧版本，PR 里的代码被抹掉。
- 做法：先提交（临时提交也行），再用 `git checkout <提交> -- <文件>` 切换，或者另开一个分离头指针的 worktree 跑基线。提交过的东西能恢复，stash 不保证。
- 已经踩了的识别办法：`git status --porcelain` 里出现你没编辑过的文件，或者 `git diff --stat HEAD` 里某个文件有大量删除行。用 `git checkout HEAD -- <文件>` 逐个恢复，然后重新确认自己的每一处改动还在。
- 排查测试失败的顺序：先单文件跑，再组合跑定位交互，最后才动版本控制。

## 清理 worktree

删之前逐项确认：

- 工作区干净，或者未跟踪的文件只是别处已有内容的拷贝。有没提交的设计稿、笔记的不删。
- 没有正在使用它的进程或会话。
- 最新提交已经在远端有着落。本仓库的 PR 合并后是压成一个提交进 main 的，分支上的原始提交不会出现在 main 的历史里，所以 `git rev-list --not --remotes` 会把大量已经合并的提交报成「只在本地」。改查提交关联的 PR：`gh api repos/<owner>/<repo>/commits/<提交号>/pulls`。

删的时候：

- ⚠️⚠️ **先单独删掉目录里的 junction，再删 worktree。** `git worktree remove --force` 会顺着 junction 删进真实目录。出过的事：临时 worktree 里挂了指向真实 `.venv` 的 junction，删除中途报错退出，但真实 `.venv` 的 `pyvenv.cfg` 已经被删，之后解释器起不来，而 `uv sync` 显示一切正常（它不看这个文件），只能整个删掉 `.venv` 重建。

  ```powershell
  $i = Get-Item -LiteralPath <联接路径> -Force
  if ($i.LinkType -eq 'Junction') { $i.Delete() }
  ```

  删完确认真实目录完好，再 `git worktree remove`。
- 有进程把这个目录当作当前目录时，`git worktree remove` 报权限错误，但登记信息已经删掉了，只留下一个删不掉的空目录。等那个进程退出后再删目录。
- 路径太长删不掉（报文件名过长）时，用长路径前缀：在 `cmd` 里执行 `rd /s /q`，路径写成以两个反斜杠、问号、反斜杠开头的完整路径。
- 批量清理临时目录时，Git Bash 的 `rm -rf` 不会跟随 junction，比资源管理器或 PowerShell 的递归删除安全。
