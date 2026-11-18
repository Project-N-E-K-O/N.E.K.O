# 区域判定、免费线路与音色、遥测、版本问卷

## 1. 区域判定（大陆 / 非大陆）

代码在 `utils/config_manager/core_config.py`。模块 docstring 列了五条不变式，改之前先读。下面是 docstring 之外、容易再踩的部分。

### 结构：一个后台探测线程，其余只读

- `_ip_probe_loop` 是一个 daemon 线程，退避重试直到拿到结论后退出，是 `_ip_check_cache` 的**唯一写者**。`get_core_config`、会话启动、应用启动都只读这个值（并幂等地确保探测线程在跑）。
- ⚠️ **不要再往里加「再起一个探测」的补丁。** 早先的实现是每个调用点各自起探测线程，为了不让并发探测互相覆盖，堆出了代际票号、卡死线程台账、过期顶替、退避账本一整套东西；收成单写者之后这些全删了。DNS 卡住时单线程靠系统解析超时自愈，不泄漏；「起一个替代探测」的新线程一样会卡，只是多漏一个线程。
- **探测绝不能在调用方线程做网络 IO。** `get_core_config` / `get_model_api_config` 是同步函数，被大量 `async def` 直接调用（包括会话启动路径）。里面一旦有会超时的网络调用，合并模式下整个事件循环冻住，所有 WebSocket 握手和心跳停摆，用户看到的是「连不上」。
- 应用启动时预热一次（`awarmup_region_check`）。每条「把 base URL 冻进会话」的路径在冻之前都要先 `await aensure_region_resolved()`（主会话、热切换、小游戏会话池都是）。新增这类路径时别漏，有结构测试盯着。
- 探测永不永久放弃：开机自启时网络常常还没好，几分钟后才有网。按连续失败次数指数退避、有上限。指数先封顶再做乘法：Python 里 `float * (2 ** 很大的整数)` 抛 `OverflowError`，不是得到 `inf`。

### 裁决顺序

IP 有结论就用 IP，并落定 `_region_cache`；IP 没结论才用 Steam 的地区，但**不落定**；两者都没有就按大陆兜底、不缓存。

- ⚠️ Steamworks 在「非 Steam 版」和「装了 Steam 版但没开 Steam 客户端」两种情况下永远拿不到地区。任何要求 Steam 投赞成票才算海外的设计，都会把这批用户永久钉在国内线路。
- ⚠️ Steam 的 `GetIPCountry()` 并不防代理：它返回的是 Steam 服务端看到的出口 IP 所在国，挂代理时跟着代理走。反而是 IP 探测显式绕开了系统代理，只设了系统 HTTP 代理的大陆用户在 IP 这一侧才判得对。
- ⚠️ 「IP 连续失败 N 次就拿 Steam 结论落定」试过，被否掉：探测长期失败不代表 Steam 就对，网络恢复后必须让 IP 接管。同类提议按此驳回。
- `main_routers/config_router/language.py` 里回写 Steam 地区时会清 `_region_cache`，但**有意不清探测的退避状态**。那个接口是前端启动时必打的，跟着清等于给退避开后门。评审提「这里也该清退避」按此驳回。

### 只有免费线路才探测

用自己 API key 的用户不会把公网 IP 交给第三方地理定位服务。闸在调用方：`get_core_config` 先确认配置里有区域敏感的免费线路 URL，才会走区域判定。不要在别处重新推导「需不需要区域」。

- `aensure_region_resolved` / `awarmup_region_check` 在配置里没有区域敏感 URL 时直接返回 True。判据是「有免费 provider」**并且**「配置需要区域」，只看 host 会把显式自己配了海外地址的用户误判成永远未落定。
- ⚠️ `AGENT_MODEL_URL` 不参与区域改写，豁免写在**改写循环里**（`non_mainland=False if key == 'AGENT_MODEL_URL'`）。`_normalize_agent_url` 是恒等函数，撤销不了循环已经做的改写。只测恒等函数的用例放走过这个 bug，端到端护栏是 `tests/unit/test_geoip_region_gate.py` 的 `test_agent_url_survives_the_overseas_rewrite_end_to_end`。
- 调试时可以用 `config/__init__.py` 的 `GEOIP_FORCE_NON_MAINLAND` 强制结论（生产保持 `None`）。

### 区域结论变了之后的长寿对象

- 长期存活的 LLM client 要按线路指纹重建，并且把 `(线路, client)` 作为一个整体原子替换（例子：`utils/language_utils.py` 的 `TranslationService`）。分成两个属性先后写，会交错出「旧 client 配新线路」并一直钉住。
- 「先等区域落定」要放在读取其它会随时间变的数据（比如角色数据）**之前**，否则等完之后手里的数据已经是旧的。

### 测试写法

- ⚠️ 断言「只发生一次」的并发用例第一版常常是假绿：临界区太短，线程根本不交错，去掉锁照样过。要把临界区里某个调用换成带 `sleep` 的版本撑开窗口，并做变异验证（去掉锁必须转红）。
- 被生产代码 `try/except` 包住的调用点，测试桩里「抛 AssertionError」会被吞成默认值，和「没调用」没区别。用**计数**断言。

## 2. 免费音色和线路的配对

- 国内和海外两条免费线路的音色目录不相交。两条闸都按**手上这份配置快照的 `base_url`** 判，不依赖区域判定是否已落定；错配时一律不下发，让服务端用自己的默认值。护栏：`tests/unit/test_free_voice_route_pairing.py`。
- **换默认猫娘的免费音色 ID**：旧 ID 已经写进存量用户的 `characters.json`。只改配置的话，旧值不在白名单里，`cleanup_invalid_voice_ids`（应用初始化和每次开会话都会跑）会把它清空，用户静默掉到通用默认音色。做法：把被换掉的旧值追加进 `utils/config_manager/persona_payload.py` 的 `_DEPRECATED_FREE_YUI_VOICE_IDS`，`cleanup_invalid_voice_ids` 在判无效之前会先经 `remap_deprecated_free_yui_voice_id` 迁移。
- 迁移是分线路的：只有国内免费线路才把废弃值迁到现役值；海外免费线路和非免费线路不迁移，交给既有校验清成**空串**。⚠️ 客户端不能把废弃值改写成别的非空别名存回去：非空值会让用户切回国内线路时「补默认音色」那一步因为已有值而跳过，随后又被清空。

## 3. 遥测：event 不上报，counter 才上报

- 前端 `window.appTelemetry.event()`（`static/app/app-telemetry.js`）和后端 `instrument.event()`（`utils/instrument.py`）都只走 `utils/event_logger.py`：写本机按天分片的 jsonl，定期滚删，**没有任何上传**。
- 上报通道只取 `instrument.snapshot()`，里面只有 `counters` 和 `histograms`；`has_data()` 也只看这两类。
- 所以：要在服务端看到的指标必须用 `counter` / `histogram`。既有做法是成对打，counter 负责上报，event 留在本机带上下文方便诊断。只打 event 的实验曝光，服务端永远是 0，实验等于没法分析。
- ⚠️ 前端三个 API 的参数形态不同：`event(name, fields)` 是两个参数，`counter(name, value, dims)` 的维度是**第三个**参数。把维度对象放到第二位，后端会因为 value 不是数字回退成加 1，维度全丢。
- 维度必须是低基数的枚举值；后端会过滤和截断（`main_routers/websocket_router.py` 的 `_handle_ws_telemetry`）。
- 改前端埋点后，测试里的 mock 要跟着换通道和参数形态，否则被 mock 的还是旧 API，断言永远不触发。
- `distribution` 和 `steam_user_id` 必须来自同一次观测，见 `docs/design/telemetry-distribution-race-impact.md`。

## 4. 版本问卷

文件格式、多语言回退、`locales` 白名单、`expires_at` 见 `config/surveys/README.md`。这里只列「为什么没弹」的排查顺序。闸全过才弹，任何一道不过都静默不弹，`/api/survey` 只回 `{has_survey: false}`。

后端（`main_routers/system_router/changelog_survey.py` 的 `get_survey`）：

1. `utils/survey_client.py` 的 `is_reporting_enabled()`：配置了问卷服务地址，并且没开 DNT。DNT 是进程启动时读一次环境变量（`NEKO_DO_NOT_TRACK` / `DO_NOT_TRACK`），和遥测共用一个开关。
2. `is_steam_user()`：遥测元数据里的 `distribution` 是 `steam`。源码运行永远不是，没有开发后门。⚠️ 这道闸跑在 Python 后端进程里，「Steam 客户端开着」不等于后端拿到了 Steam 信号，要经 Steam 启动、让后端启动时初始化成功才算（实测观察）。
3. `_load_survey_for_version`：`config/surveys/<APP_VERSION>.json` 存在，且过了 `locales` / `expires_at`。每次请求都重读磁盘，没有缓存。

前端（`static/app/app.js`）：

4. `localStorage` 里有 `neko_survey_eligible_for`。只有「老玩家升级」（之前记过别的版本号）才会写；全新安装或清过 `localStorage` 的永远不写，连 `/api/survey` 都不请求。
5. `neko_last_survey_version` 不等于这一版（没填过也没跳过过）。

排查和强制触发：

- 先看 `/api/steam/workshop/status` 的 `steamworks_initialized` 和 `/api/survey` 的返回，判断卡在后端哪道闸。
- 源码模式下后端两道闸过不了，`/api/survey` 只会返回 `has_survey: false`，所以本机看不到问卷弹窗。能验证的只到「前端发出了 `/api/survey` 请求」：在控制台把 `neko_last_notified_version` 设成一个旧版本号，删掉另外两个 key，刷新（会先弹更新日志）。要看弹窗本身，得让这个接口返回一份有效问卷，比如在 DevTools 里给它配一条本地覆盖响应。
- 打包后不弹的一个固定原因：dist 里没有 `config/surveys` 目录。加新数据目录要同步本地打包脚本、CI 打包流程和 `scripts/check_nuitka_dist.py`，见 `docs/contributing/nuitka-packaging.md`。
