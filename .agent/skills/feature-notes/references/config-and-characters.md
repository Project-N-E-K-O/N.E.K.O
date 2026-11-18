# 角色配置锁、语言偏好、凭证中心、破冰教程

## 1. `character_config_mutation_lock` 的职责边界

定义在 `utils/character_memory.py`，是一把 `asyncio.Lock`。角色增删改名、角色卡保存、语言偏好、工坊同步 / 退订、小剧场和串门里改 `characters.json` 的路径都在用它。

它**只保护 `characters.json` 的「读、改、存」事务**，让这些路径的快照不会互相超车。再动这些路径时按下面的判据：

- ⚠️ 锁里不能有 `await request.json()`。那是在等客户端的 socket，一个慢客户端会把所有角色操作卡住。先解析请求体，再加锁。
- ⚠️ 锁里不能跨「等记忆侧清理完成」这类屏障。清理回调自己也要取同一把锁，放在锁里等就是互相卡到屏障超时，而且清理会跑在终态落盘之前。做法：事务只覆盖「存在性校验、记下 recent 文件的代际、请求 memory server」这几步；会话对账和清理放在锁外，回调自己取锁并重新校验身份。
- 等 Steam 的调用同理：退订流程在 `characters.json` 事务结束时就放锁，不横跨发给 Steam 的退订调用。
- 一句话判据：凡是「等客户端」「等 connector」「等 Steam」的 `await` 都不该在这把锁里。

## 2. 角色级语言偏好

代码在 `main_routers/characters_router/language_preference.py` 和 `app/memory_server/locale_state.py`。

- **会话的 `user_language` 是 `None` 表示「没有证据」，不是「和目标语言不同」。** 应用启动时会给每个角色建会话管理器，`user_language` 初始都是 `None`，只有 WebSocket 消息或开会话才会写。只打开角色卡管理页时它一直是 `None`。如果拿 `None != 目标语言` 当成「语言变了」，重选一个已经生效的语言也会清空最近对话。没有自己语言的会话不提供「需要隔离旧上下文」的信号，变没变只看持久化的偏好是否真的改了。
- **memory server 返回 409 是设计信号。** 语言偏好写入在因果序检查落败时返回 409。主服务这侧必须在 `raise_for_status()` **之前**把它识别成 `LanguagePreferenceConflictError`，否则会被兜底的 `except Exception` 压成 503 加异常堆栈。前端收到 409 应该重新拉取当前值，而不是把下拉框回滚成一个已经过期的值。
- **只读查询不要顺手建目录。** 语言偏好的 sidecar 路径解析（`_locale_path` / `_subject_locale_path`）不调 `ensure_character_dir`：写入时 `atomic_write_json` 自己会建父目录；读路径上建目录会让一次查询把刚删除或刚改名的角色目录重新建出来。
- ⚠️ 读和写共用同一个路径解析函数，不要另起「只读版本」。有一批测试是通过替换这个函数来重定向 sidecar 的，读写分叉后打桩只覆盖一半。

## 3. 凭证管理中心

页面是 `templates/cookies_login.html`、`templates/cookies_guide.html` 和 `static/js/cookies_login.js`，后端 `main_routers/cookies_login_router.py`。

- ⚠️ **图文教程里的截图提交前必须脱敏。** 浏览器开发者工具的截图里很容易带上真实的 Cookie 值。已经推上去的提交，后面再提交一张脱敏图是盖不掉的，真正的止损只有让那条凭证失效（退出登录）。
- 写「截图里还有没有字形」这类像素级守卫时，**不要先转灰度再比**（实测观察）：白字压在浅色选中底色上，亮度差很小，灰度判据放未脱敏的原图照样过；逐通道比才咬得住。判据里要排除本来就高对比的标注箭头。
- 全平台共用的提交按钮，恢复可点击状态时要用代际判断，不能在 `finally` 里无条件恢复，否则旧请求返回时会解开新请求刚上的锁。切换平台时除了作废旧代际，还要直接放行按钮；同平台的语言切换重渲染不放行。

## 4. 破冰教程的选项池

- 存储层 `utils/prompt_state/tutorial_choices.py`（`utils/tutorial_choices_state.py` 是兼容别名），落在全局配置目录的 `tutorial_choices.json`，按角色名归档。写入接口是 `POST /api/icebreaker/choice`（`main_routers/icebreaker_router.py`），前端在 `static/tutorial/icebreaker/new-user-icebreaker.js` 的 `recordChoiceToPool()`。
- **这个池子是有意只写不读的**：现在不进记忆、不影响模型，先把信号留下来，以后再逐步做消费侧。评审提「写了没人读」「死代码」时按设计意图回复。
- 它和 `/api/icebreaker/context` 是分开的：后者写的是喂给模型的临时会话上下文，前者是独立的持久留存。
- 选项里的 `node_id` 是不透明的字符串，不当结构键用，也不假设顺序。
