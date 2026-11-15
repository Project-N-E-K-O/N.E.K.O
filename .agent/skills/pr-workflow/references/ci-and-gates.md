# CI 分诊和门禁

## CI 红了，先分诊再动手

`pull_request` 事件跑的是 **merge commit**（PR 的 head 并进 base 之后的结果），不是 PR 分支本身。main 被别的改动合红之后，每个 PR 都会跟着红，自己怎么改都修不好。

三步，都很便宜：

1. 比对本 PR 相邻两次运行。两个提交之间的差异明显不可能改变行为（只改了注释、docstring、markdown），却一绿一红，那就不是你的改动。
2. 看 main 自己是不是红的：

   ```bash
   gh run list --branch main --workflow "<工作流名>" --limit 12 --json conclusion,headSha,createdAt
   ```

3. `gh run view <id> --log-failed`，比对失败用例的集合和自己那次是否逐字一致。一致就是继承来的。

确认是继承来的红之后：不要重跑（merge commit 还会并进坏的 main），不要把别人的回归拉进自己 PR 的范围。在 PR 上贴一条说明和证据（相邻提交的差异、两次运行的结论、main 上那次运行、造成问题的提交），修 main 的事单独处理。

要和偶发失败区分开：偶发失败重跑会过，继承红不会。

## 只有 base 是 main 的 PR 才会自动跑 CI

`analyze.yml`、`unit-tests.yml`、`unit-test-shards.yml`、`plugin-tests.yml`、`runtime-process-model.yml`、`wake-word-runtime.yml`、`pr-report-gate.yml` 这七个工作流的 `pull_request` 都限定了 `branches: [main]`。PR 叠在别的分支上时这些都不触发，PR 页面上看起来像「没有失败」。

- 叠在别的分支上的 PR，开出来和每次推送后手动触发。除 `pr-report-gate.yml` 外的六个都有 `workflow_dispatch`：

  ```bash
  # 每次都触发
  gh workflow run analyze.yml --ref <分支>
  gh workflow run unit-tests.yml --ref <分支>
  gh workflow run unit-test-shards.yml --ref <分支>
  # 在 PR 上按路径过滤的三个：改动碰到各自 paths 里的任何一项才需要触发
  gh workflow run plugin-tests.yml --ref <分支>            # plugin/**、utils/process_creation.py、pyproject.toml、uv.lock
  gh workflow run runtime-process-model.yml --ref <分支>   # launcher.py、launcher_core/**、utils/parent_guard.py、utils/single_instance.py、utils/port_utils.py、pyproject.toml、uv.lock
  gh workflow run wake-word-runtime.yml --ref <分支>       # main_logic/voice_input/wake_word/**、scripts/wake_word/ 下的构建脚本和补丁
  ```

  后三个手动触发时不按路径过滤，所以要自己对着各工作流文件的 `paths` 判断（上面注释里是目前的清单，以工作流文件为准；依赖清单变了也算，因为它会影响运行环境）。`pr-report-gate.yml` 没有 `workflow_dispatch`，只能等 base 改回 main，在那之前用下面「PR 描述门禁」一节的命令本地预验。
- 手动触发时，`analyze.yml` 里和 base 比差异的几步不跑（`check_i18n_sync.py`、`check_docstring_no_cjk.py`、`check_prompt_zh_tw.py`、`check_no_nonascii_asset_names.py` 的 `--base`）。要在本地对 PR 的 base 和 `origin/main` 各跑一遍。
- 汇报时不要把「PR 页面没有红」说成 CI 通过。写明手动触发了哪几个、结果如何。
- 上游 PR 合并后把 base 改成 main，之后每次推送自动跑全部工作流。

## 连续推送时，哪些运行会被取消

各工作流的 `concurrency` 设置不一样：

- `unit-tests.yml` 是 `cancel-in-progress: false`：正在跑的会跑完，只有排队中的会被更新的推送顶掉。所以连推几次之后，较早提交上的 Unit Tests 事后往往是通过的。
- `unit-test-shards.yml`、`plugin-tests.yml`、`runtime-process-model.yml` 在 PR 上会取消正在跑的；`docker-config.yml` 始终取消。

⚠️ 写「某个提交上没跑完」或「被顶掉」之前，按提交查最终结论，不要从中途看到的状态推断：

```bash
gh api "repos/<owner>/<repo>/actions/runs?head_sha=<完整提交号>" --jq '.workflow_runs[] | "\(.name) \(.status) \(.conclusion)"'
```

写的时候还在跑，就写「还在跑」。

## PR 描述门禁

`.github/workflows/pr-report-gate.yml` 调 `scripts/check_pr_report.py`，检查的是 PR 描述。规则写在 `.agent/rules/neko-guide.md` 的「提交规范」一节，补充几点：

- 触发「回归报告」的目录以脚本里的 `WATCHED_PREFIXES` 为准。`neko-guide.md` 里抄了一份清单，两边不一致时信脚本。
- 「不拆分理由」的 locale 豁免只认 `static/locales` 和 `frontend/plugin-manager/src/i18n/locales` 这两处。`docs/` 下的多语言 `.md` 是计入的，纯文档的多语言同步 PR 超过 20 个文件照样要写。
- 段落按标题里的关键词定位。`不适用`、`无`、`TBD` 这类占位符在触发条件成立时算没填，HTML 注释不算内容。
- 本地预验：

  ```bash
  PR_BODY="$(gh pr view <N> --json body -q .body)" PR_LABELS="" uv run python scripts/check_pr_report.py --base origin/main
  ```

开 PR 时就把报告写进描述，不要等这条检查红了再补。

## 冲突

- 每轮推送后查 `gh pr view <N> --json mergeable,mergeStateStatus`。main 合入很频繁，评审拖几轮就可能冲突，而评论和 CI 的通知都不会告诉你。
- 改了 `static/locales` 的 PR 合 main 时几乎必然在 locale 版本号那几处冲突，解法见 `i18n-pitfalls`。

## 合并 main 之后要补跑的

- 合并带进来的不只是 Python 改动。按 `.github/workflows/unit-test-shards.yml` 里 frontend-contract 那一步的 `node --test` 命令逐条跑。给 `node --test` 传目录在 Windows 上不工作。
- 起新的 locale 版本号之前，先在 `tests/` 里搜一下有没有对它的后缀或格式的断言。

## fork 来的 PR

- 首次贡献者的 fork PR，Actions 需要维护者手动批准才会跑；有的 fork PR 每次推送都要再批准一次。看到 CI 一直不动，先看是不是在等批准。

## 自动评审的标签

`.github/workflows/claude-review-state.yml` 维护 `claude-review` 和 `claude-converged` 两个标签：标了收敛的 PR 一有新提交就会被改回 `claude-review`；标收敛时如果没有 Codex 对当前 head 的评审，标签会被撤回。规则写在那个工作流文件的开头。
