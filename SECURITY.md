# Security Policy / 安全策略

本文档说明 N.E.K.O 项目的漏洞报告渠道、响应预期与支持范围。
This document describes how to report vulnerabilities in N.E.K.O, what to
expect after reporting, and which versions receive security fixes.

## Supported Versions / 支持版本

仅最新发布的 release 与 `main` 分支接收安全修复；旧版本不再回溯修补。
Only the latest release and the `main` branch receive security fixes; older
releases are not patched retroactively. We recommend always running the
latest release, especially for instances exposed beyond localhost.

## Reporting a Vulnerability / 报告漏洞

**请不要为未修复的漏洞开公开 Issue。** 请通过以下私有渠道报告：

**Do NOT open a public issue for an unfixed vulnerability.** Please report
through one of these private channels:

1. **GitHub 私有漏洞报告（首选 / preferred）**：仓库 `Security` 标签页 →
   `Report a vulnerability`。若该入口未开放（需要维护者在仓库设置中启用
   Private vulnerability reporting），请改用下面的社区渠道。
   Repository `Security` tab → `Report a vulnerability`. If the entry point is
   not available (maintainers must enable Private vulnerability reporting in
   the repository settings), use the community channels below.
2. **Discord**：加入 [Discord 社区](https://discord.gg/5kgHfepNJr)后私信管理员；
   不要在公开频道贴出漏洞细节。
   Join the [Discord community](https://discord.gg/5kgHfepNJr) and DM a
   maintainer; do not post vulnerability details in public channels.
3. **QQ 群**：开发者 QQ 群 **1092413003**，私信管理员；同样不要在群内公开细节。
   Developer QQ group **1092413003**, DM a maintainer; likewise, do not share
   details publicly in the group.

报告时请尽量包含 / Please include, where possible:

- 受影响的版本或 commit / the affected version or commit;
- 复现步骤或最小触发方式 / reproduction steps or a minimal trigger;
- 影响评估（能造成什么后果、是否需要前置条件）/ an impact assessment
  (what harm is possible, and any preconditions);
- 是否已公开披露或将被 CVE 收录 / whether the issue is already public or
  destined for a CVE assignment.

## What to Expect / 响应预期

- **确认收到 / Acknowledgment**：我们通常会在 **72 小时内**回复确认。
  We will acknowledge your report within **72 hours**.
- **后续沟通 / Follow-up**：确认后会同步修复计划；修复发布前请与维护者
  保持协作，勿提前公开细节。
  We will follow up with a remediation plan; please keep coordinating with
  the maintainers and refrain from disclosing details before a fix ships.
- **致谢 / Credit**：修复发布后，经你同意可在发布说明中署名致谢。
  With your consent, we will credit you in the release notes once the fix is
  published.

## Scope / 范围

**属于本项目 / In scope**：

- 核心服务与本地 Web 界面（`main_routers/`、`app/`、`frontend/`）；
- 插件运行时与沙箱、插件间 IPC / 权限边界（`plugin/`、`main_logic/`）；
- 插件市场的供应链问题（市场基础设施、分发链路被篡改等）；
- 官方 Docker / 安装包的打包配置（`docker/`、`pkgbuild/`）。

**不属于本项目 / Out of scope**：

- 第三方插件自身的漏洞：请直接报告给对应插件作者；若漏洞出在市场
  分发链路而非插件代码，仍属本项目范围。
  Vulnerabilities in third-party plugins should be reported to the plugin
  author; issues in the market distribution pipeline itself remain in scope.
- 上游依赖的已知 CVE 转述（除非本项目特有的集成方式放大了影响）。
  Reposting known CVEs of upstream dependencies (unless N.E.K.O's own
  integration materially amplifies the impact).
- 需要物理接触设备或已完全控制宿主机的场景。
  Scenarios requiring physical access or full compromise of the host machine.

## Safe Harbor / 安全港

善意研究者按本政策行事时，我们不会追究其法律责任：

- 仅为验证漏洞而进行必要且最小化的测试，不访问、不改动人机主机的
  任何真实用户数据；
- 发现用户隐私数据（聊天记录、凭据、截图等）时立即停止操作并报告，
  不复制、不传播；
- 不利用漏洞破坏服务、勒索或公开未修复细节；
- 给合理时间完成修复后再行披露。

We will not pursue legal action against researchers acting in good faith
under this policy, provided that they: test only as much as necessary to
validate the issue; never access or alter real user data (chat history,
credentials, screenshots, etc.) and stop to report immediately upon
encountering it; do not exploit vulnerabilities to disrupt service, extort,
or disclose unpatched details; and allow reasonable time for remediation
before any public disclosure.
