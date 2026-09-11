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

以下入口分别维护运行合同、问题证据和作者SDK。代码及可复现结果优先；历史实验不是当前实施计划。

- [小剧场架构开发文档](./neko-theater-architecture)：当前运行、模型、存档、前端与体验边界。
- [小剧场实测问题与解决方案](./neko-theater-issues-and-solutions)：按原编号保存反例、修复和未采用实验。
- [剧本工坊SDK迁移与接入说明](./neko-theater-workshop-sdk-migration)：已迁入能力、宿主保护、发布和待验发行范围。

SDK调用示例在仓库 `theater_workshop/README.md`；原独立工作台的作者协议在InkAI的 `docs/superpowers/specs/2026-08-06-neko-theater-numeric-v2-generator-design.md`。SDK不带网页，InkAI仍保留独立界面，共享创作与评改规则继续同步。

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
