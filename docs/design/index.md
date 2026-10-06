# Design and Implementation Records

These documents preserve design intent and implementation context. They are grouped by maintenance purpose, not by delivery date. Most records are written in the language used by the original implementation work.

> The current code and tests are authoritative. Read [Documentation Maintenance](/contributing/documentation) before treating a proposal or dated record as a current contract.

## Architecture and long-lived contracts

- [Avatar performance module maintenance](./avatar-performance-module-maintenance)
- [Avatar tool interaction design and maintenance](./avatar-tool-interaction-design-and-maintenance)
- [Avatar tool prompt guidelines](./avatar-tool-prompt-guidelines)
- [Cat Mind state-machine rules](./cat-idle-state-machine-rules)
- [Cat idle states](./cat-idle-states-feature)
- [Deep topic hooks](./deep-topic-hooks)
- [LLM prompt budget](./llm-prompt-budget)
- [Proactive reason-code guide](./proactive-reason-code-guide.zh-CN)
- [User activity tracker](./user-activity-tracker)
- [Voice design architecture](./voice-design-architecture)

## Implemented design records

- [ASR client phase record](./asr-client-phase1)
- [Compact chat mode](./compact-chat-mode-design)
- [Memory event journal](./memory-event-log-rfc)
- [User-driven memory evidence](./memory-evidence-rfc)
- [PNGTuber lightweight avatar](./pngtuber-lightweight-avatar-plan)
- [Translation subtitle panel](./translation-subtitle-panel-design)
- [TTS provider and voice-source unification](./tts-voice-source-unification)
- [Live2D idle motion selection and recovery](/live2d_motion_plan)
- [PNGTubeRemix layered physics compatibility](/pngtuber-remix-physics-plan)

## Active remediation plans

- [PR #2951 public-knowledge boundary remediation](./public-knowledge-pr2951-boundary-remediation) — review evidence preserved by round; the record itself states which rounds have landed, and code, tests and CI remain the source of truth

## N.E.K.O 小剧场与剧本工坊

- [小剧场架构](./neko-theater-architecture)：当前唯一的实现合同——模块与权限、Story Package／Session／Ledger／记忆归档数据合同、回合流水线、复核与确定性检查、生命周期事务、胶囊展示合同与可选模块开关。
- [小剧场设计决策记录](./neko-theater-decisions)：关键取舍、被否决方案及已知风险与未验证范围。

剧本工坊 SDK 的调用、宿主与发布合同见仓库 `theater_workshop/README.md`。

## Product-flow and interaction records

- [Seven-day floating avatar guide](./avatar-floating-7day-complete-guide-dev)
- [Floating avatar panel functions](./avatar-floating-panel-functions)
- [Post-tutorial low-disruption chat branches](./avatar-floating-post-theater-chat-branches)
- [CAT1 Playground Drop](./cat1-playground-drop-design)
- [Focus / True-Name mode](./focus-truename-mode)
- [Memory-browser particle dissolve](./memory-browser-particle-dissolve)
- [Yui guide-system cursor hiding](./yui-guide-system-cursor-hiding)

## Security, persistence, and incident analysis

- [Screen-history isolation and local references](./screen-history-local-reference) — Chinese-only. Stage 0A-1 (request-side rewrite of chained screen comments) is implemented; explicit reference selection, delivery and context isolation remain a proposal.
- [Local mutation endpoint authentication](./security/local-mutation-auth)
- [Steam Auto-Cloud synchronization](./cloud-save-sync-optimization-plan)
- [Telemetry distribution and Steam user ID race](./telemetry-distribution-race-impact)

## Approved proposals (not yet implemented)

- [Catgirl visiting infrastructure (v3, all decisions approved)](./visit-infrastructure)
  - [T1~T5 on-device measurements](./visit-infrastructure-t1-t5) — Chinese-only. Implemented record (2026-10-02): same-origin iframe checks in the real Electron Pet window; all passed on Windows (one compatibility-mode T2 blank-frame run was excluded by the owner as operator interference; 0 blanks in 7,700 re-tested frames); macOS T3/T4 is still pending.

New records should state whether they are a current contract, implemented record, proposal, historical snapshot, or deprecated document near the beginning.
