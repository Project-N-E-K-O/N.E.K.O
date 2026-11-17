# 启动时序

这一类问题的共同点：只在冷启动、全新状态或慢机器上出现，开发时手动点一遍复现不了。排查时先把「谁在什么时候读了什么」列出来，不要先怀疑打包或翻译缺失。

## 1. 把界面语言传给后端：先等 i18n 就绪

症状：界面已经是英文，但启动时弹的更新日志是中文；紧跟着弹的问卷却是英文。

根因：

- `static/i18n-i18next.js` 初始化时要先解析初始语言（其中有一次带超时的 Steam 语言查询），完成之前 `window.i18next.language` 是空的。
- 启动流程里早跑的请求读到空值，传了 `lang=''`；后端的本地化读取（`main_routers/system_router/changelog_survey.py`）收到空语言就按中文原文返回。晚跑的请求（等用户点完前一个弹窗）碰巧躲过了。同一句取值代码，时机不同，结果不同。

现行做法（`static/app/app.js` 启动流程里的 `_ensureI18nReady()` + `_resolveUiLang()`）：

- 先等就绪再取语言。就绪信号是 `i18next.isInitialized`，或者 `localechange` 事件——`i18n-i18next.js` 在初始化成功、无后端手动加载、初始化失败这几种终态都会派发它，所以一定等得到。
- 等待要有超时，超时值必须盖住 i18n 自己的完整启动窗口（依赖轮询 + Steam 查询 + 它自己的硬安全网），现在是 12 秒。给短了会在 i18n 还没就绪时提前放行，竞态并没有关掉。
- 定时器要在完成回调里清掉。
- `localStorage` 的 `i18nextLng` 只做最后的兜底。单靠它关不掉竞态：手动设置的界面语言不一定落盘，首启时也可能还没写。

规则：前端任何把语言传给后端本地化接口的请求（更新日志、问卷、问候等），都不能裸读 `window.i18next.language`。

## 2. 「即将占屏」的教程和别的启动弹窗

首启期望的顺序是：存储位置 → 新手教程 → 选人格 → 破冰。

症状：新机器首次启动，选人格和新手教程同时弹出来。

根因：

- 选人格（`static/js/character_personality_onboarding.js` 的 `bootstrap()`）等教程流程结束，但带一个 15 秒的超时安全阀：超时后如果教程没有上锁，就不再等、直接弹。
- 教程真正上锁（`isTutorialRunning` / `window.isInTutorial`）发生在一长串等待之后：等悬浮按钮、等模型加载空闲、等 i18n。冷启动加载模型经常超过 15 秒，超时那一刻教程「马上要开始但还没上锁」，安全阀被误触发。

现行机制：

- `static/tutorial/core/universal-manager.js` 在决定要自动启动教程、进入那串等待之前，就通过 `setHomeTutorialPending(true)` 置 `window.isNekoHomeTutorialPending`。
- 需要给教程让路的地方把它和「已上锁」一起判断：选人格的 `isHomeTutorialInteractionLocked()`，以及 `app-auto-goodbye.js`、`app-autostart-prompt.js`、`app.js`、`static/js/agent_ui_v2.js`、新用户破冰、点击引导。新增启动期弹窗时也要读它。
- ⚠️ 结束 pending 并放行等待方的收口点是两个：`dispatchStartupGreetingRelease()`（教程决定不启动，或已经结束）和 `clearStartupGreetingRelease()`（教程已上锁，由 `isTutorialRunning` 接管）。新增「教程不启动」的出口要走前者，不要只手动清 pending：漏一个出口 pending 就永远为真，等它的一方永久卡死；只清 pending 而不派发放行事件，监听事件的一方同样等不到。
- 例外在 `static/tutorial/click-guide/home.js`：点击引导在启动阶段没有接手的几条路径（已有教程在跑、状态里没有待启动的点击引导、启动抛错、启动流程中途退出）直接调 `setHomeTutorialPending(false)`。这几处只清 pending，不派发放行事件。「清 pending」和「派发放行」是两件事，读代码时不要假设前者一定经过上面两个函数。
- 等待方还要监听 `neko:startup-greeting-release`（`detail.released === true`）来放行。教程中途夭折时不会有「完成」或「跳过」事件，后端状态也不会变成已结束，只靠轮询会一直等下去。
- `released: true` 在教程正常完成时也会派发，而选人格那边的「不再等教程」标志是持久的。所以从设置里重新选人格时，只有在此刻确实有教程占屏时才重置这个标志去重新等；无条件重置会在没有后续放行事件时永久挂起。
- 判断「教程是否占屏」用锁判定函数，不要用 `window.__NEKO_STARTUP_GREETING_RELEASED__`：启动放行时它会被消费掉并删除，之后读到的是空。

验证：这类竞态靠手测复现不了。`tests/frontend/test_initial_personality_onboarding.py` 里的做法是把超时值替换成很小的数，再断言「教程即将启动时不弹」。新增类似逻辑时照这个办法写测试。

## 3. 通用做法

- 两段异步流程有先后依赖时，给「我马上要占屏 / 马上要写」单独发一个早期信号，不要让对方只能观察「已经开始」。早期信号的清除挂在已有的、所有出口必经的收口函数上。
- 超时安全阀触发后要再判断一次「对方是不是正在进行中」，是就继续等。
- 页面里的全局是分脚本陆续注入的。读一个可能还没注入的全局时，区分「还没注入」和「注入了空值」（例子：`static/app/app-websocket.js` 的 `getWebSocketLanlanName()`，前者继续等，后者直接连）。
- 不要用固定的 `setTimeout` 时长假设某个资源一定加载完了（另见 `.agent/skills/vanilla-js-ui-race-conditions/SKILL.md`）。
