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

## N.E.K.O 小剧场与剧本工坊

以下入口分别维护运行合同、问题证据和作者SDK。接手先读架构总览、第2节职责与第13节当前边界，再读问题文档第3—6节；具体失败按编号追溯。代码及可复现结果优先，历史实验不是当前实施计划。

- [小剧场架构开发文档](./neko-theater-architecture)：当前运行、模型、存档、前端与体验边界。
- [小剧场模块职责速查](./neko-theater-architecture#2-模块与权限)：按当前生产导入关系列出运行端、HTTP/本体、工坊 SDK 和压测模块的作用与权限边界；其它专项文档不重复维护模块定义。
- [小剧场实测问题与解决方案](./neko-theater-issues-and-solutions)：按原编号保存反例、修复和未采用实验。
- [剧本工坊SDK迁移与接入说明](./neko-theater-workshop-sdk-migration)：已迁入能力、宿主保护、发布和待验发行范围。
- [运行端框架审查](./neko-theater-framework-review)：TF-01—03已处理，保留优化前证据。
- [工坊SDK框架审查](./neko-theater-workshop-framework-review)：WS-01—02已处理，保留作者编排与发布边界。
- [公共数据结构审查](./neko-theater-shared-data-structures-review)：DS-01—02已处理，作者项目、正式包与Session继续分离。
- [运行端框架二次审查](./neko-theater-framework-review-round2)：FR2-01—12；延迟项已按问题2.140实施，FR2-11为演绎效果与回复速度优化方案（建议，未实施），FR2-12为模块开关化与作者禁令窄判定（已实施，问题2.143）。
- [小剧场优化方案（参考 DiceFrame）](./neko-theater-diceframe-optimization)：外部参考机制的借鉴方案，含事实账本、结构化提议与确定性裁定、逐字段放行、提交幂等、工坊变更决策与工程制度；**仅为建议，未实施**。

SDK调用示例在仓库 `theater_workshop/README.md`；原独立工作台的作者协议在InkAI的 `docs/superpowers/specs/2026-08-06-neko-theater-numeric-v2-generator-design.md`，后台IB-01—04修复见同目录 `neko-theater-generator-framework-review.md`。四份结构审查的11项处理统一归入问题2.125。SDK不带网页，InkAI仍保留独立界面，共享创作与评改规则继续同步。

运行侧已有默认关闭的演绎文案日志，压测默认7推荐／3自由。问题2.139的更强模型接入已回退，仅保留验证结论；当前模型与复核预算以架构第10节为准，不把实验提速当作现行能力。

[小剧场胶囊演绎迁移方案](./neko-theater-capsule-migration)只作自由模式退役与胶囊迁移的历史记录，不能覆盖当前架构，也不是未完成任务清单。

## Product-flow and interaction records

- [Seven-day floating avatar guide](./avatar-floating-7day-complete-guide-dev)
- [Post-tutorial low-disruption chat branches](./avatar-floating-post-theater-chat-branches)
- [CAT1 Playground Drop](./cat1-playground-drop-design)
- [Focus / True-Name mode](./focus-truename-mode)
- [Memory-browser particle dissolve](./memory-browser-particle-dissolve)
- [Yui guide-system cursor hiding](./yui-guide-system-cursor-hiding)

## Security, persistence, and incident analysis

- [Local mutation endpoint authentication](./security/local-mutation-auth)
- [Steam Auto-Cloud synchronization](./cloud-save-sync-optimization-plan)
- [Telemetry distribution and Steam user ID race](./telemetry-distribution-race-impact)

New records should state whether they are a current contract, implemented record, proposal, historical snapshot, or deprecated document near the beginning.
