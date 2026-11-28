---
name: pr-workflow
description: 维护者处理 PR 的流程和判断标准：在提交者的分支上就地改、推送和回复的顺序、自动评审意见收不收怎么判、CI 红了怎么分诊、怎么确认「评论都处理完了」。开 PR、往 PR 分支推送、处理评审意见、评审别人的 PR 之前读。
---

# PR 处理流程

这份文档记的是本仓库维护者处理 PR 的习惯和判断标准。帮维护者开 PR、接手别人的 PR、处理评审意见、评审别人的 PR 时照这个做。

## 一轮的固定顺序

1. **先看状态**：`gh pr view <N> --json state,mergeable,mergeStateStatus,headRefOid`。不是 `OPEN` 就停。维护者可能在你等待期间已经合并。
2. **把评论读完、判完**：行内评论、PR 级评论、review 正文三处都要看（见 `references/github-api-pitfalls.md`）。
3. **改 → 只跑直接相关的测试 → commit → push**。
4. **回读**：`gh pr view <N> --json headRefOid`，确认 PR 的 head 就是刚推的提交。只看 push 的回显不算数。
5. **回复线程**，在回复里引用提交号。修掉的线程回复后关闭；驳回的写清理由，留给维护者过目。
6. **查冲突**：再看一次 `mergeable`。`CONFLICTING` 就立刻合 PR 的 base 分支解掉（`gh pr view <N> --json baseRefName`）。base 通常是 main；叠在别的分支上的 PR 要合那个分支，合 `origin/main` 不一定带得进造成冲突的改动。
7. **看 CI**（见 `references/ci-and-gates.md`）。

顺序是「先 push、再回帖」。自动评审收到回复后会立刻拉 head 核对，回复早于代码就会被判成「没落地」。

## 分支和推送

- **接手别人的 PR = 在提交者自己的分支上就地改**，提交直接推回那个分支，让 PR 里的提交历史连续。不新建分支，不另开 PR。
- ⚠️ **推之前先判断 PR 是不是来自 fork**：

  ```bash
  gh pr view <N> --json isCrossRepository,headRepositoryOwner,headRepository,headRefName,maintainerCanModify
  ```

  - `isCrossRepository: false` → `git push origin HEAD:<headRefName>`
  - `isCrossRepository: true` → 推到 fork 的地址，并且需要 `maintainerCanModify: true`：`git push https://github.com/<owner>/<repo>.git HEAD:<headRefName>`

  对 fork 来的 PR 执行 `git push origin HEAD:<headRefName>` 不会更新 PR，而是在上游仓库新建一个同名分支，回显还是成功的 `* [new branch]`。
- ⚠️ **往已合并、分支已删的 PR 推送，会静默重建那个远端分支**：提交不属于任何 PR，CI 不跑，评审机器人不评。推送后机器人和 CI 全无动静时，先怀疑 PR 状态。`gh pr edit`、回复线程之前同样先查 `state`。
- 在 worktree 里干活时，推之前确认目标分支，不要把无关提交带进 PR 分支。
- **测试、提交、推送写在同一条命令里时，先存退出码再提交**。`pytest ... | tail -1 && git commit` 判的是 `tail` 的退出码，红着的提交会被推上去；用 `;` 串也一样。固定写法：

  ```bash
  uv run pytest <文件> > log.txt 2>&1; rc=$?; tail -3 log.txt
  [ $rc -eq 0 ] && git commit -m "..." && git push ...
  ```

## 哪些事直接做，哪些事要停下来问

直接做，不问：

- 修复做完、相关测试通过后的 commit 和 push。
- 冲突解完后的提交。
- 判断完评审意见后回复线程。
- 自动评审一轮轮出意见时，是否继续处理下一轮。
- 自己这一轮引入、修法明确的回归：当场修掉、跑测试、推上去，汇报里写「已修」。不要当成待确认项端给维护者。
- 影响面为零或极小、自己已有明确建议的取舍：按建议做，汇报里写一句「按 X 处理」。
- 干活时顺手发现的范围外问题，如果根因已经定位到具体行：切独立分支直接修，开独立 PR，不混进当前 PR。

停下来问（按 `maintainer-collaboration` 里的拍板格式）：

- 架构或产品取舍：两套设计互斥，或者改动会推翻别人刚合并且写了测试的行为。
- 会改变既有用户行为的取舍，或者自己拿不准的建议。
- 评审别人的 PR 时要不要动插件内部代码。
- 觉得 PR 太大想拆：PR 数量按和维护者商定的走，不自己决定拆。

## 评审意见怎么判

详见 `references/review-triage.md`。要点：

- **独立判断，不盲从**。自动评审常基于通用规则或不完整的上下文。判断它错了就在线程里写明理由驳回。
- 逐条问两件事：**有收益吗**（真缺陷还是纯理论边界），**有回归风险吗**（尤其是碰到已经实测过的共享链路）。无风险有收益就改；有回归风险的不盲改，写明取舍，收进后续 issue。
- 会被自动评审反复扫的 PR，**先写下收和不收的边界**，之后每条先复现再归类。
- 三类意见的处理是固定的：PR 把既有行为改坏了，必修；新状态机或两个入口之间的时序竞争，继续修；修了反而更危险的，不收。
- 机器人的复现往往是真的，但根因诊断常常是错的。先做最小化对照再动手。
- 以前拍板驳回过的同类意见再出现，按当时的理由回，不重新摆选项。

## 说「都处理完了」之前

下面几项都查过才能说，缺一项都漏过：

- 未关线程数为 0。查询必须分页，作者不限。
- PR 级评论（`issues/<N>/comments`）里没有未回应的。未关线程查不到这一类。
- 每条 review 的正文看过。CodeRabbit 的「Outside diff range」「Nitpick」意见只写在 review 正文里，不生成线程；意见多的时候「Minor」级的也全在正文里。
- 线程总数和上次比有没有变多。
- 不能拿「没收到推送」当依据。任何自动推送评论的通道都可能漏，尤其会漏掉用 PR 所用账号发的评论，而维护者的评审往往就是用这个账号发的。

## PR 描述

- 改了高风险目录（`scripts/check_pr_report.py` 的 `WATCHED_PREFIXES`）下的 `*.py`，开 PR 时就把「回归报告」写进描述；计入上限的文件超过 20 个要写「不拆分理由」。规则见 `.agent/rules/neko-guide.md`，本地预验见 `references/ci-and-gates.md`。
- 动到模型调用的改动，描述里带改动前后的调用次数、token、延迟对比（见 `maintainer-collaboration`）。
- 改已开 PR 的描述：先 `gh pr view <N> --json body --jq .body` 拉线上正文，在它上面改。拿开 PR 时的本地旧稿覆盖，会抹掉机器人或别人后来追加的内容。
- 写完回读一次，确认章节标题还在，再本地跑一遍描述门禁。

## 评审别人的 PR

- 只关心这个 PR 对框架、主程序、公共代码动了什么：加了什么、改了什么契约、引入了什么钩子，以及怎么收敛或去耦合。插件内部的实现是贡献者自己的事，默认不提修改建议。
- 向维护者汇报评审结果时不要平铺成一串「文件:行 — 摘要」。每条四段：作者做了什么、建议做什么、照做的回归风险、收益。纯理论边界要直说。

## references

- `references/review-triage.md`：评审意见收不收的判断标准、边界表模板、什么时候收尾、回复怎么写。处理自动评审之前读。
- `references/github-api-pitfalls.md`：用 `gh` 查评论、回复线程、轮询新评论时踩过的坑。写任何查评论或发评论的命令之前读。
- `references/ci-and-gates.md`：CI 红了怎么分诊、哪些 PR 的 CI 不会自动跑、PR 描述门禁的本地预验、合并 main 之后要补跑什么。
