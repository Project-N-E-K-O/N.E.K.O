# UI Push Test

Identity: `ui_push_test` / UI 推送测试 / `UiPushTestPlugin` / `plugin.plugins.ui_push_test:UiPushTestPlugin`.

Manual test plugin for the public ChatCard and PluginView SDK. The Hosted TSX panel independently creates and updates chat cards and AgentHUD plugin content, closes Agent content, and shows local submission receipts and real button callback records. Each target character has its own latest handles and update counters.

All operations are explicit button clicks. No external services, LLM calls, automatic startup, polling, persisted state, or platform modifications. `passive=true` keeps this diagnostic plugin out of automatic Agent tool selection. A user must enter the exact recipient character name and verify display in that character's open chat.

UI permissions: state:read and action:call only. Plugin-owned i18n files serve Python action metadata, generated button text and the TSX panel. Tests stay in this plugin workspace.
