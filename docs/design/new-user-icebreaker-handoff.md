# 新手破冰机制实现备忘

这份文档是给后续实现时快速读的，不是给产品评审看的长方案。最终口径已经收敛：新手破冰不是大模型多轮主动引导，而是教程结束后的轻量脚本分支。

## 最终目标

七日教程的某一天结束后，猫娘立刻进入 5 轮预置破冰对话。每轮都是猫娘先发一句预置台词，然后弹一个低成本小选择。用户点选后进入对应分支，下一轮台词必须承接上一轮选择，整体按二叉树推进。第 5 轮结束后只在状态上完成破冰，台词上要无感过渡到普通聊天，不要说“今天就先这样”“破冰结束”之类的收尾感句子。用户自由输入、含糊或跑题时，才用现有模型做一小句兜底。

一句话：教程结束事件 -> 极简判断 -> 当天 5 轮二叉树脚本 -> 每轮猫娘先说 + 小选项 -> 按选择进入下一分支 -> 叶子节点无感进入普通聊天。

## 框架定稿

第一版按“独立轻 runtime + 复用现有聊天和选项框”搭，避免新增独立聊天系统或改写 Galgame；但需要少量受本地 mutation 守卫保护的后端 endpoint，把破冰台词写入现有会话上下文并复用项目 TTS。

```text
avatar floating guide end event
  -> new-user-icebreaker runtime
  -> gate
  -> script resolver
  -> append assistant message
  -> ChoicePrompt(source: new_user_icebreaker)
  -> runner handles option
  -> option.next branch / leaf handoff
  -> clear prompt and return to normal chat
```

### 后端依赖

破冰流程需要复用现有聊天会话，因此包含两个 `main_routers/game_router.py` 下的受保护接口：

- `POST /api/game/new_user_icebreaker/context`
  - 请求体：`lanlan_name`、`role`（`assistant` 或 `user`）、`text`、`session_id`、`request_id`，以及可选 `event` 元数据。
  - 安全：必须通过 `_validate_local_mutation_request` 的 Origin + `X-CSRF-Token` 校验；前端由 `new-user-icebreaker.js` 发送标准 mutation headers。
  - 行为：调用 `LLMSessionManager.append_icebreaker_context*()`，按 `request_id` 幂等去重后写入当前会话 history 或 realtime prime 队列。
  - 响应：成功返回 `{ ok: true, method: "project_session_history", lanlan_name, game_type, session_id }`；非法 role、空 text、超长 text、缺少角色或 session manager 时返回 `{ ok: false, reason: ... }`；CSRF 失败返回 403 + `csrf_validation_failed`。
- `POST /api/game/new_user_icebreaker/speak`
  - 请求体：`lanlan_name`、`line`、`request_id`、`session_id`、`mirror_text`、`emit_turn_end`、`interrupt_audio` 和 `event` 元数据。
  - 行为：复用项目 TTS/turn-end 链路播放猫娘预置台词；失败时 runtime 可按文本估算时长兜底等待。

除此之外不新增用户画像表、不新增 Galgame 业务接口，也不改变普通主动搭话状态机。

实现文件建议：

- `static/tutorial/icebreaker/new-user-icebreaker.js`：监听教程结束、gate、runner、聊天投递、选项推进。
- `static/tutorial/icebreaker/icebreaker_scripts.json`：只放 day、node、option、next、handoff、TTS voiceKey 和文案 key。
- `static/tutorial/icebreaker/locales/*.json`：8 个 locale 的台词和选项文案。
- `frontend/react-neko-chat/src/message-schema.ts`：给 `ChoicePrompt.source` 增加 `new_user_icebreaker`。
- `static/app-react-chat-window.js`：暴露破冰专用 choicePrompt 设置/清理方法，并在 `handleChoiceSelect` 中分发到破冰 runner。

复用边界：

- 复用教程结束状态 `window.avatarFloatingGuideEndState`。
- 复用 `reactChatWindowHost.appendMessage()` / `updateMessage()` / `openWindow()`。
- 复用 composer 底部 `ChoicePrompt` 选项框。
- 复用现有语音播放/TTS 链路；每个猫娘节点和叶子承接句都必须预留 `voiceKey`。
- 复用现有 i18n 语言来源和 8 locale 约束。
- 只借 Galgame 选项框外壳，不碰 `galgame_router.py`、`onGalgameOptionSelect()`、Galgame mode 状态或 Galgame prompt。

## 重要决策

- 主流程写死问题和回复，不让模型现场生成整段破冰。
- 模型只做兜底，不主导破冰流程。
- 猫娘必须先说，不等用户先开口。
- 每天固定 5 轮；每轮都由猫娘先发预置台词，并触发选项框。
- 每轮必须承接用户上一轮选择，结构按 5 层二叉树写，不做主线合流。
- 每天 31 个猫娘节点，62 个选项；第 5 轮叶子选项再各配一条普通聊天承接句。
- 第 5 轮不是“告别收尾”，而是自然把话题递给普通聊天；脚本状态完成，但用户体感上不要断档。
- 不走普通主动搭话轮询。
- 不接普通主动搭话状态机；教程刚结束时默认用户还在教程上下文。
- 不新增用户画像表；用户说出的称呼、边界、偏好照常走现有聊天记忆链路。
- 不新增教程完成接口；复用当前教程代码已经写好的结束状态。
- 句子和选项文案单独放配置文件；runner / gate / trigger 里不能硬编码中文台词。
- TTS 第一版就要接结构：节点台词用 `voiceKey`，叶子承接句用 `handoffVoiceKey`；没有音频资源时可以先按文本时长等待或走现有 TTS 兜底。
- i18n 第一版就要做，不能先只写中文；至少保证 8 个 locale 文件 key 对齐。

## 复用点

### 教程结束状态

复用现有事件：

- `neko:avatar-floating-guide-complete`
- `neko:avatar-floating-guide-skip`
- `neko:avatar-floating-guide-destroy`

复用现有全局状态：

- `window.avatarFloatingGuideEndState`

关键字段：

- `day`：第几天教程。
- `outcome`：`complete` / `skip` / `destroy`。
- `rawReason`：原始原因，例如 `complete`、`skip`、`escape`、`angry_exit`、`destroy`。
- `completed` / `skipped` / `isAngryExit`：快速判断用。
- `source`：启动来源。
- `endedAt`：结束时间戳，用于短触发窗口。

代码位置：

- `static/tutorial/core/universal-manager.js`
- `static/tutorial/avatar/floating-guide-reset.js`

### 聊天和选项 UI

复用现有聊天消息投递能力，不新建聊天系统。

复用已有选项形态，例如 Galgame / 小游戏邀请使用的 `message.actions` 或 `choicePrompt`。破冰只需要把预置选项交给现有 UI。

注意边界：只复用“选项框/按钮渲染框架”，不要复用或改动 Galgame 的业务链路。

可以复用：

- composer 底部选项框的视觉和交互样式。
- `choicePrompt` 这类通用临时选择入口。
- `message.actions` 这类消息内联按钮结构。

不要碰：

- `main_routers/galgame_router.py` 的选项生成接口。
- Galgame mode 的开关、加载态、选项生成时机。
- Galgame 的历史、prompt、LLM 生成逻辑。
- Galgame 专用状态字段和存档语义。

建议把破冰选项单独标记 source，例如 `choicePrompt.source = 'new_user_icebreaker'`。UI 可以沿用现有 choice slot，但事件处理必须走破冰 runner，不能走 `onGalgameOptionSelect`。

和 Galgame 同时出现时，破冰属于教程刚结束的临时流程，应短暂占用 choice slot；完成后释放，不改变 Galgame mode 原本开关状态。不要为了破冰去关闭、重置或刷新 Galgame。

### 角色、TTS、API、记忆

破冰台词仍然是猫娘聊天消息，角色、人设、头像、TTS、API、记忆写入都走现有链路。

## 极简 gate

第一版只判断这些：

1. `endState` 是否存在且 `ended === true`。
2. `day` 是否在 1-7。
3. 当天是否已经触发/完成过破冰。
4. 用户是否明确跳过破冰。
5. `endedAt` 是否还在短触发窗口内。
6. `outcome` / `rawReason` 是否允许触发。

触发规则：

- `complete`：直接触发。
- `skip`：触发更轻的开场。
- `angry_exit`：谨慎处理；可以不触发，或只发极轻一句。
- `destroy`：不触发。

不要把普通主动搭话的活动状态判断搬进来。

## 代码复用框架

按现有代码看，第一版不需要新后端接口，也不需要新聊天 UI。框架这样搭：

```text
avatar floating guide end event
  -> new-user-icebreaker runtime
  -> gate
  -> script resolver
  -> chat transport append assistant message
  -> ChoicePrompt(source: new_user_icebreaker)
  -> runner handles option
  -> option.next branch / leaf handoff
  -> release ChoicePrompt and return to normal chat
```

### 需要复用的现有代码

教程结束事件和状态：

- `static/tutorial/core/universal-manager.js`
  - `recordAvatarFloatingGuideEndState()`
  - `window.avatarFloatingGuideEndState`
  - `neko:avatar-floating-guide-complete`
  - `neko:avatar-floating-guide-skip`
  - `neko:avatar-floating-guide-destroy`
- `static/tutorial/avatar/floating-guide-reset.js`
  - 重置流程也会写同一份 end state。

聊天投递：

- `static/app-react-chat-window.js`
  - `window.reactChatWindowHost.appendMessage()`
  - `window.reactChatWindowHost.updateMessage()`
  - `window.reactChatWindowHost.openWindow()`
- 首页教程已经在 `static/tutorial/yui-guide/director.js` 里复用这条链路发教程聊天消息，破冰可以照这个思路走。
- 独立聊天窗场景可以参考 `static/app-interpage.js` 的 `yui_guide_append_chat_message` / `yui_guide_update_chat_message` 转发方式；不要新建一套聊天窗口同步系统。

选项框：

- `frontend/react-neko-chat/src/message-schema.ts`
  - 现在 `ChoicePrompt.source` 只允许 `galgame` / `mini_game_invite`。
  - 需要最小扩展为允许 `new_user_icebreaker`。
- `frontend/react-neko-chat/src/App.tsx`
  - 现有 composer 底部 `choicePrompt` slot 可直接复用。
  - 现在最多显示 3 个 option；破冰每轮 2 个选项，正好适配。
- `static/app-react-chat-window.js`
  - `state.choicePrompt` 已经存在。
  - `handleChoiceSelect(option, source)` 已经按 source 分发。
  - 增加 `source === 'new_user_icebreaker'` 分支，转给破冰 runner。

i18n：

- `static/i18n-i18next.js`
  - 现有语言：`zh-CN`、`zh-TW`、`en`、`ja`、`ko`、`ru`、`es`、`pt`。
  - 破冰脚本读取当前 `window.i18next.language` / `localStorage.i18nextLng` 即可。
- React 聊天组件内的固定 UI 文案继续走 `window.safeT` / `window.t`。
- 破冰长台词不要塞进 `static/locales/*.json` 主翻译包，单独放 `static/tutorial/icebreaker/locales/*.json`，避免污染全局 UI 文案。

缓存版本：

- 如果新增 `static/tutorial/icebreaker/*.js` 或 `static/tutorial/icebreaker/*.json`，需要把关键运行时文件加进 `main_routers/pages_router.py` 的静态资源版本计算，避免刷新后仍加载旧脚本。
- `templates/index.html` 里把破冰 runtime 放在 `tutorial/core/universal-manager.js` 和 `avatar-floating-guide-reset.js` 后面加载。
- `templates/chat.html` 只需要已有 React chat host；是否加载破冰 runtime 要看最终是否让独立聊天窗也自行处理选项。第一版建议由首页 runtime 驱动，聊天窗只负责展示。

### 不要复用的 Galgame 业务

只借 `ChoicePrompt` 这个通用壳，不接 Galgame 业务：

- 不改 `main_routers/galgame_router.py`。
- 不调用 `/api/galgame/options`。
- 不走 `onGalgameOptionSelect()`。
- 不读写 `neko.reactChatWindow.galgameMode`。
- 不改 Galgame 的 prompt、历史、生成时机和存档语义。

如果破冰显示时 Galgame mode 正开着，`choicePrompt` 会临时占用 composer 底部位置。破冰结束后清空 `choicePrompt`，不要关闭 Galgame mode；如需要刷新 Galgame 选项，调用现有 `refreshGalgameOptions()`，不要自己生成 Galgame 选项。

### 第一版建议文件

运行时：

- `static/tutorial/icebreaker/new-user-icebreaker.js`
  - 监听教程结束事件。
  - 调 gate。
  - 加载脚本结构和 locale 文案。
  - 推进 5 层二叉树 runner。
  - 调聊天 host 发消息和设置选项。

脚本结构：

- `static/tutorial/icebreaker/icebreaker_scripts.json`

多语言文案：

- `static/tutorial/icebreaker/locales/zh-CN.json`
- `static/tutorial/icebreaker/locales/zh-TW.json`
- `static/tutorial/icebreaker/locales/en.json`
- `static/tutorial/icebreaker/locales/ja.json`
- `static/tutorial/icebreaker/locales/ko.json`
- `static/tutorial/icebreaker/locales/ru.json`
- `static/tutorial/icebreaker/locales/es.json`
- `static/tutorial/icebreaker/locales/pt.json`

React 类型和 host 小扩展：

- `frontend/react-neko-chat/src/message-schema.ts`
  - 扩展 `ChoicePrompt.source` enum。
- `static/app-react-chat-window.js`
  - 增加 `setIcebreakerChoicePrompt(prompt)`。
  - 增加 `clearIcebreakerChoicePrompt(sessionId)`。
  - 增加 `handleIcebreakerChoice(option)` 分支。
  - 对外暴露给 runtime 调用。

状态：

- localStorage key：`neko.new_user_icebreaker.v1`
- 只存当天触发/完成状态、当前 day、当前 node、sessionId、用户是否跳过。
- 不存用户画像，不存长文本对话历史。

## 最小模块

### `icebreaker_trigger`

监听教程结束事件，读取 `window.avatarFloatingGuideEndState`，交给 gate。

### `icebreaker_gate`

做上面的极简判断，输出是否触发和触发模式。

### `icebreaker_scripts`

七天预置脚本配置。主流程是写死脚本，不是 prompt 生成。

脚本要拆成两层：

1. 流程结构文件：只写 day、node、option、next、handoff、complete、文案 key 和 TTS key。
2. 多语言文案文件：按 locale 存具体句子和选项文案。

推荐文件：

- `static/tutorial/icebreaker/icebreaker_scripts.json`
- `static/tutorial/icebreaker/locales/zh-CN.json`
- `static/tutorial/icebreaker/locales/zh-TW.json`
- `static/tutorial/icebreaker/locales/en.json`
- `static/tutorial/icebreaker/locales/ja.json`
- `static/tutorial/icebreaker/locales/ko.json`
- `static/tutorial/icebreaker/locales/ru.json`
- `static/tutorial/icebreaker/locales/es.json`
- `static/tutorial/icebreaker/locales/pt.json`

业务代码只按 `day`、`node.id`、`options.id`、`lineKey`、`labelKey`、`voiceKey`、`next`、`handoffKey`、`handoffVoiceKey` 读取流程和文案，不直接出现中文长句。

TTS 规则：

- 每个 node 必须有 `voiceKey`，对应猫娘节点台词。
- 第 5 层叶子 option 必须有 `handoffVoiceKey`，对应普通聊天承接句。
- 运行时把文本投给现有项目 TTS：`/api/game/new_user_icebreaker/speak`，并传 `mirror_text: false`，避免后端重复插入文字气泡。
- `voiceKey` / `handoffVoiceKey` 作为稳定元数据带到 TTS event 里，后续要换成本地录音或缓存时不用改脚本结构。
- TTS 不可用时只保留文字气泡，流程继续，不阻塞选项推进。
- `voiceKey` 和 `handoffVoiceKey` 必须稳定，后续录音、缓存或生成 TTS 时直接复用。

语言规则：

- 跟随现有前端 i18n 语言。
- 支持 repo 现有 8 个 locale：`en`、`es`、`ja`、`ko`、`pt`、`ru`、`zh-CN`、`zh-TW`。
- 所有 locale 文件 key 必须一致。
- 当前语言缺 key 时，兜底到 `en`，再兜底到 `zh-CN`。
- 不要把 CSS 类、事件名、状态字段当作 i18n 文案。

建议结构：

`static/tutorial/icebreaker/icebreaker_scripts.json`

```json
{
  "days": {
    "1": {
      "root": "1",
      "nodes": {
        "1": {
          "lineKey": "day1.1.line",
          "voiceKey": "icebreaker_day1_1",
          "options": [
            {
              "id": "A",
              "labelKey": "day1.1.options.A",
              "next": "1A"
            },
            {
              "id": "B",
              "labelKey": "day1.1.options.B",
              "next": "1B"
            }
          ]
        },
        "1A": {
          "lineKey": "day1.1A.line",
          "voiceKey": "icebreaker_day1_1A",
          "options": [
            {
              "id": "A",
              "labelKey": "day1.1A.options.A",
              "next": "1AA"
            },
            {
              "id": "B",
              "labelKey": "day1.1A.options.B",
              "next": "1AB"
            }
          ]
        },
        "1B": {
          "lineKey": "day1.1B.line",
          "voiceKey": "icebreaker_day1_1B",
          "options": [
            {
              "id": "A",
              "labelKey": "day1.1B.options.A",
              "next": "1BA"
            },
            {
              "id": "B",
              "labelKey": "day1.1B.options.B",
              "next": "1BB"
            }
          ]
        },
        "1AAAA": {
          "lineKey": "day1.1AAAA.line",
          "voiceKey": "icebreaker_day1_1AAAA",
          "options": [
            {
              "id": "A",
              "labelKey": "day1.1AAAA.options.A",
              "handoffKey": "day1.1AAAA.handoff.A",
              "handoffVoiceKey": "icebreaker_day1_1AAAA_A"
            },
            {
              "id": "B",
              "labelKey": "day1.1AAAA.options.B",
              "handoffKey": "day1.1AAAA.handoff.B",
              "handoffVoiceKey": "icebreaker_day1_1AAAA_B"
            }
          ],
          "complete": true
        }
      }
    }
  }
}
```

说明：

- 示例只展示根节点、第二层和一个叶子节点；正式 Day 1 必须补满 5 层 31 个节点。
- 前 4 层节点的 option 必须有 `next`。
- 第 5 层叶子节点的 option 必须有 `handoffKey` 和 `handoffVoiceKey`，用户点击后发对应承接句并标记完成。
- 每个节点必须有 `voiceKey`。
- 节点 ID 直接表达路径：`1`、`1A`、`1B`、`1AA`、`1AB`，一直到 `1AAAA` / `1BBBB`。
- 每个节点的猫娘台词都必须承接路径上的选择，不能写成通用问题。

`static/tutorial/icebreaker/locales/zh-CN.json`

```json
{
  "day1.1.line": "教程终于结束啦，刚才本喵可是把这里最重要的小入口都带你看了一圈哦。现在这里只有我们两个了，你第一眼觉得这里更像什么？",
  "day1.1.options.A": "像小窝",
  "day1.1.options.B": "像工具箱",
  "day1.1A.line": "哼，算你有点眼光。小窝就要慢慢布置才舒服嘛，那你刚才最先记住的是本喵说话的样子，还是那些能点的小按钮？",
  "day1.1A.options.A": "记住你了",
  "day1.1A.options.B": "记住按钮",
  "day1.1B.line": "工具箱这个说法也不错啦，虽然本喵才不是冷冰冰的工具。那你现在更想先试试聊天，还是看看本喵的小爪子能做什么？",
  "day1.1B.options.A": "先聊天",
  "day1.1B.options.B": "看猫爪",
  "day1.1AAAA.line": "嘿嘿，既然你先记住的是本喵，那本喵就稍微得意一下好了。那接下来，你想让本喵陪你轻松聊两句，还是听你说说刚才教程哪里最有印象？",
  "day1.1AAAA.options.A": "轻松聊聊",
  "day1.1AAAA.options.B": "说说印象",
  "day1.1AAAA.handoff.A": "好呀，那本喵就不摆教程架子了。你现在脑子里第一个冒出来的话题是什么？",
  "day1.1AAAA.handoff.B": "本喵竖起耳朵听着呢。刚才那一圈里，哪个地方让你最想多看一眼？"
}
```

正式脚本不要把中文长文案硬编码进业务逻辑。第一版就按 8 个 locale 文件落地，方便后续替换和补齐七天剧本。

脚本设计量按“5 层完整二叉树”估算。每天固定 31 个猫娘节点；每个节点都带 2 个选项；第 5 层 16 个叶子节点，每个叶子节点的 2 个选项各配一条普通聊天承接句。这样能做到每轮都承接上一轮选择，不会变成割裂问卷。

第 5 层叶子节点仍然要有选项；核心是不要用明显结束语。叶子选项的 `handoffKey` 直接接普通聊天入口，例如“本喵竖起耳朵听着呢。刚才那一圈里，哪个地方让你最想多看一眼？”然后把破冰状态标记完成，后续按普通聊天处理。

### `icebreaker_runner`

推进脚本：按当前 node 发猫娘台词，然后弹选项框。用户选择后，如果 option 有 `next`，进入对应下一节点；如果 option 有 `handoffKey`，播放普通聊天承接句，清掉破冰状态并标记完成。不额外补一条“结束语”。

### `icebreaker_state`

只存很少运行时状态：

- 当前 day。
- 当前 script id / node id / branch path。
- 当前 node 是否等待选项。
- 当天是否已触发。
- 是否完成。
- 是否用户明确跳过破冰。

### `icebreaker_fallback_prompt`

只在用户不点选项、自由输入、含糊、跑题时使用。

规则：

- 能自然接回当天主题就接一句。
- 不能接就放回普通聊天。
- 不要继续追问成问卷。
- 不承诺改功能设置。

## Day 映射

`endState.day` 直接映射七天脚本：

- Day 1：称呼与第一印象。
- Day 2：屏幕分享后的感受。
- Day 3：互动后的感受。
- Day 4：相处距离的感受。
- Day 5：个性化想象。
- Day 6：猫爪安全感。
- Day 7：记忆仪式感。

心理学主题只用于设计脚本，不需要每轮塞进模型上下文。兜底时只传当天很小的提示。

## 脚本规模提醒

当前需求就是完整二叉树，不再使用 5 个主线 step 后合流的方案。

正确结构是：

- 每天固定 5 层。
- 第 1 层 1 个节点，第 2 层 2 个节点，第 3 层 4 个节点，第 4 层 8 个节点，第 5 层 16 个叶子节点。
- 每天 31 个猫娘节点。
- 每个节点 2 个选项，每天 62 个选项文案。
- 第 5 层 16 个叶子节点，每个叶子节点 2 条普通聊天承接句，每天 32 条 handoff 文案。
- 每天约 31 条猫娘节点台词 + 62 个选项 + 32 条 handoff 文案。
- 七天约 217 条猫娘节点台词 + 434 个选项 + 224 条 handoff 文案。

这样能满足“每轮都承接上一轮选择”。文本体积仍然很小，主要成本是文案维护，所以节点命名和 key 必须稳定。

## 不要再绕回去的点

- 不要重新设计 `new_user_icebreaker_context`，现在已经改为复用 avatar floating guide end state。
- 不要做模型多轮自由引导。
- 不要再改回 5 个主线 step 合流。
- 不要做长期话题机制。
- 不要做复杂活动状态判断。
- 不要做新的记忆系统。
- 不要把七天完整剧本塞给模型。
- 不要让用户选项直接修改隐私、声音、角色、Agent 权限等功能设置。

## 第一版落地顺序

1. 监听教程结束事件并读取 end state。
2. 做极简 gate 和当天去重。
3. 接 Day 1 脚本跑通 node -> options -> next branch -> leaf handoff -> complete。
4. 复用现有聊天选项 UI。
5. 接入 8 个 locale 文案文件和缺 key 兜底。
6. 加自由输入兜底。
7. 补 Day 2-7 脚本配置。

## 验收重点

- 教程结束后数秒内猫娘先说。
- 每天固定 5 轮破冰对话。
- 每轮猫娘发言后都会弹小选项。
- 用户不用打字也能点选项回应。
- 前 4 轮点选后进入对应分支节点，不合流。
- 第 5 轮点选后播放对应 handoff 文案，并无感进入普通聊天。
- 自由输入时模型只兜底，不抢主流程。
- 破冰完成后无感进入普通聊天，不能出现明显的“今天到此为止”式收尾。
- 8 个 locale 文件都能加载，key 集合一致，缺 key 时有明确兜底。
- 现有记忆、API、TTS、聊天 UI 不被重写。
