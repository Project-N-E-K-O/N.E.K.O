# 数据流

## WebSocket 聊天生命周期

这是主要的交互流程 —— 用户与 AI 角色聊天。

```
浏览器                      主服务器                      LLM 提供商
  │                            │                              │
  │──── WS connect ───────────>│                              │
  │     /ws/{lanlan_name}      │                              │
  │                            │                              │
  │──── start_session ────────>│                              │
  │     {input_type: "audio"}  │──── WS connect ─────────────>│
  │                            │     (OmniRealtimeClient)     │
  │                            │                              │
  │──── stream_data ──────────>│──── send_audio ─────────────>│
  │     {audio chunks}         │                              │
  │                            │<──── on_text_delta ──────────│
  │<──── {type: "text"} ──────│                              │
  │                            │<──── on_audio_delta ─────────│
  │<──── {type: "audio"} ─────│     (resampled 24→48kHz)     │
  │                            │                              │
  │──── end_session ──────────>│──── close ───────────────────│
  │                            │                              │
  │                            │── hot-swap to next session ──│
```

### 消息格式

**客户端 -> 服务器（JSON 文本帧）：**

```json
{ "action": "start_session", "input_type": "audio", "new_session": true }
{ "action": "stream_data", "input_type": "audio", "data": "<base64 PCM>" }
{ "action": "stream_data", "input_type": "text", "data": "Hello!" }
{ "action": "end_session" }
{ "action": "pause_session" }
{ "action": "screenshot_response", "data": "<base64 image>" }
{ "action": "ping" }
```

**服务器 -> 客户端（JSON 文本帧）：**

```json
{ "type": "text", "text": "Hi there!" }
{ "type": "audio", "audio_data": "<base64 PCM 48kHz>" }
{ "type": "status", "message": "Session started" }
{ "type": "emotion", "emotion": "happy" }
{ "type": "agent_notification", "text": "...", "source": "...", "status": "..." }
{ "type": "catgirl_switched", "data": { ... } }
{ "type": "pong" }
```

## REST API 请求流程

```
浏览器 ──── GET /api/characters/ ────> FastAPI Router
                                            │
                                            ├── shared_state（全局会话管理器）
                                            ├── config_manager（角色数据）
                                            └── Response（JSON）
```

所有 REST 端点遵循标准 FastAPI 模式。路由通过 `shared_state.py` 的 getter 函数访问全局状态，以避免循环导入。

## 智能体任务流程

```
LLMSessionManager                  智能体服务器
  │                                    │
  │── ZMQ PUB (analyze request) ──────>│
  │                                    │── DirectTaskExecutor:
  │                                    │   并行评估:
  │                                    │   ├── _assess_mcp()
  │                                    │   ├── _assess_browser_use()
  │                                    │   ├── _assess_computer_use()
  │                                    │   └── _assess_user_plugin()
  │                                    │   优先级选择 & 执行
  │                                    │── Analyzer：评估结果
  │<── ZMQ PUSH (task_result) ────────│
  │                                    │
  │── 注入到下一轮 LLM 对话 ──>        │
```

## TTS 流水线

```
LLM 文本输出 ──> TTS 请求队列 ──> TTS 工作线程
                                              │
                                     ┌────────┼──────────────┐
                                     │        │              │
                                     ▼        ▼              ▼
                                CosyVoice  GPT-SoVITS   StepFun RT
                               (DashScope) (本地)       (WebSocket)
                                     │        │              │
                                     └────────┼──────────────┘
                                              │
                                         TTS 响应队列
                                              │
                                    soxr ResampleStream（24→48kHz）
                                        （状态化，按消息）
                                              │
                                         WebSocket 发送至浏览器
```

TTS 流水线完全支持中断 —— 当用户开始说话（中断事件）时，待处理的 TTS 输出会被立即丢弃。`soxr` 重采样器在块之间维护内部状态以防止不连续性，并在每条新消息时重置状态以避免伪影。
