"""
★ 核心：1:1 移植 claudian/src/providers/claude/runtime/

包含：
- chat_runtime.py         — ClaudeChatRuntime.ts
- message_channel.py      — ClaudeMessageChannel.ts
- cold_start.py           — claudeColdStartQuery.ts
- spawn.py                — customSpawn.ts + Windows shim
- cli_resolver.py         — ClaudeCliResolver.ts
- query_options.py        — ClaudeQueryOptionsBuilder.ts
- session_manager.py      — ClaudeSessionManager.ts
- approval_handler.py     — ClaudeApprovalHandler.ts
- user_message_factory.py — ClaudeUserMessageFactory.ts
- turn_encoder.py         — ClaudeTurnEncoder.ts
- rewind.py               — ClaudeRewindService.ts
- dynamic_updates.py      — ClaudeDynamicUpdates.ts
- task_result.py          — ClaudeTaskResultInterpreter.ts
- types.py                — runtime/types.ts
"""
