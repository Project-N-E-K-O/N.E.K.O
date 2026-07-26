# 快速开始

`sts2_autoplay` 会把本地 `STS2 AI Agent` 暴露出来的《Slay the Spire 2》局面接入到 N.E.K.O，让你既可以看局面、看建议，也可以把尖塔交给她短时托管，并在中途用自然语言调整策略。当前这版的交互面已经收敛成几类核心能力：看状态 / 看当前局面、自动游玩控制、陪玩模式、按一句话调整策略、查看下一步建议，以及按建议执行一步。

## 使用教程

### 获取MOD

使用Git
```text
https://github.com/CharTyr/STS2-Agent/releases
```

### 安装游戏 Mod

可以在steam里右键Slay the Spire 2， 选择管理->浏览本地文件

Steam 默认游戏目录通常类似：

```text
...\Steam\steamapps\common\Slay the Spire 2
```

将`STS2 AI Agent` mod 复制到尖塔游戏目录的 `mods/` 下

如果Slay the Spire 2目录下没有mods文件夹，请自行创建。

```text
使用mod可能导致存档丢失，请备份或利用控制台创哥理赔(在尖塔主菜单按 "~" 键，输入"unlock all"，即可解锁全角色和难度)
```

安装完成后目录应类似：

```text
Slay the Spire 2/
  mods/
    STS2AIAgent.dll
    STS2AIAgent.pck
    mod_id.json
```

### 设置mod端口

mod默认监听端口为8080，是热门端口，会被很多软件占用。

可以打开环境变量，在全局变量处新建变量 STS2_API_PORT ，值可以选择一些冷门端口，比如18080，38080等等

样例 变量名："STS2_API_PORT" 变量值："18080"

Ps：环境变量可以通过一下方式快速打开：

- 按下 Win + R 组合键，打开“运行”对话框。

- 输入以下命令并按回车：

```
rundll32 sysdm.cpl,EditEnvironmentVariables
```

### 启动游戏并确认接口

先正常启动游戏，让 Mod 随游戏加载。

第一次加载 mod 后如果游戏出现一次异常退出，重新启动游戏即可。

在加载mod后，在NEKO中，启用猫爪，开启插件，进入插件面板，手动启动杀戮尖塔插件

如果你在打开插件或初始化插件的同时，刚好也在《杀戮尖塔 2》里进行操作，插件第一次回应可能会比平时慢一拍，属于正常现象。等当前局面同步完成后，后续响应通常会恢复正常。

### 可使用的指令

【打牌】【自动代打】【通一关】【牌打的如何】【停止】
【打出一张牌】【打出某张牌】【推荐一张牌】……诸如此类…

## 联系人

有任何问题请把游戏运行日志和NEKO运行日志发送邮件到 zhaijiunknown@outlook.com

游戏运行日志
```text
%AppData%\SlayTheSpire2\logs
```

NEKO运行日志
```text
您的用户文件夹\AppData\Local\N.E.K.O\logs
```

## 功能概览

- 连接本地 `STS2 AI Agent` HTTP 服务并读取当前尖塔局面。
- 支持一键查看当前局面：会刷新一次状态，并同时整理快照、局势摘要和猫娘同步包。
- 支持后台自动游玩控制：启动、暂停、恢复、停止，以及按建议执行一步。
- 支持陪玩模式：在不打断主流程的前提下，按配置推送观察、点评和提醒。
- 支持自然语言策略调整：用户一句话就可以把当前事件/敌人级偏好写进策略覆盖。
- 支持查看下一步建议：先看看她准备怎么走，再决定是否按建议走一步。
- 支持安全保护：低血量暂停、危险攻击减速、危险解除后恢复速度，以及必要时自动恢复自动游玩。
- 支持前端被动推送：局面同步、观察信息、陪玩提示和控制反馈都会通过插件消息通道发送给宿主。

## 本插件配置

配置文件：`plugin.toml`

### 基础配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `base_url` | `http://127.0.0.1:8080` | 尖塔本地 Agent 地址。 |
| `connect_timeout_seconds` | `5` | 连接超时秒数。 |
| `request_timeout_seconds` | `15` | 请求超时秒数。 |
| `poll_interval_idle_seconds` | `3` | 空闲状态轮询间隔。 |
| `poll_interval_active_seconds` | `1` | 自动游玩运行时轮询间隔。 |
| `action_interval_seconds` | `1.5` | 每个动作之间的额外间隔。 |
| `post_action_delay_seconds` | `0.5` | 动作执行后等待局面稳定的间隔。 |
| `autoplay_on_start` | `false` | 插件启动后是否自动开始游玩。 |
| `character_strategy` | `defect` | 当前默认策略名，会按当前局面自动匹配到对应策略上下文。 |
| `max_consecutive_errors` | `3` | 最大连续错误次数，超过后视为连接异常。 |

### 前端推送与陪玩观察

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `llm_frontend_output_enabled` | `true` | 是否允许把自动游玩动作/错误主动推送到前端。 |
| `llm_frontend_output_probability` | `1.0` | 普通动作推送概率。错误和关键控制信息仍可强制推送。 |
| `autoplay_push_probability` | `0.5` | 非陪玩状态下，普通局面同步推送的概率。 |
| `companion_push_probability` | `0.7` | 陪玩模式下，普通局面同步推送的概率。 |
| `neko_reporting_enabled` | `true` | 是否启用猫娘观察能力。 |
| `neko_report_interval_steps` | `1` | 每隔多少个自动游玩步骤整理一次观察内容。 |
| `neko_report_hud_enabled` | `true` | 是否把整理好的观察内容实际推送到前端 HUD / 消息通道。 |
| `neko_commentary_enabled` | `true` | 是否允许生成陪玩点评与提醒。 |
| `neko_commentary_probability` | `0.65` | 普通低优先级点评的触发概率。 |
| `neko_commentary_min_interval_seconds` | `4` | 同类点评的最小间隔，用来减少刷屏。 |
| `neko_critical_commentary_always` | `true` | 高优先级提醒是否总是播报。 |
| `neko_guidance_max_queue` | `50` | 指导/偏好相关上下文的内部队列上限。 |

### 自动保护与节奏控制

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `neko_auto_low_hp_threshold` | `0.3` | 血量比例低于该值时，自动游玩会优先暂停。 |
| `neko_auto_safe_hp_threshold` | `0.5` | 血量恢复到该比例后，可重新视为安全。 |
| `neko_auto_dangerous_attack_threshold` | `20` | 敌人高伤意图达到该阈值时，可能触发减速保护。 |
| `neko_auto_resume_after_low_hp` | `true` | 低血量暂停后，恢复安全时是否允许自动恢复。 |
| `neko_desperate_enabled` | `true` | 是否启用残血求生策略。 |
| `neko_desperate_hp_threshold` | `0.2` | 触发残血求生策略的血量比例。 |
| `neko_maximize_enabled` | `true` | 是否启用偏收益最大化的决策倾向。 |

## 普通用户推荐说法

普通用户不需要记住底层参数。优先把原话交给当前保留的高层入口，让插件自己判断你是在看局面、调整策略，还是准备按建议执行一步。

推荐理解方式：

| 你想表达什么 | 更适合用什么能力 |
| --- | --- |
| `看看现在什么情况` | `sts2_get_status` |
| `看看当前局面` | `sts2_read_state` |
| `让她自己玩起来` / `先停一下自动玩` / `继续让她自己玩` / `别让她自己玩了` | autoplay 控制入口 |
| `按我这句来调整策略：这个事件优先低代价路线` | `sts2_apply_user_override` |
| `看看她准备怎么走` | `sts2_get_planned_operation` |
| `按建议走一步` | `sts2_execute_planned_operation` |
| `打开陪玩模式` / `关掉陪玩模式` | companion mode 控制入口 |

当前推荐的交互顺序是：
1. 先看局面
2. 再看她准备怎么走
3. 如果你有想法，用一句话调整策略
4. 最后决定是按建议走一步，还是让她继续自动玩

## 插件入口

下面这些入口是当前主脚本真正保留的对外能力。名称已经尽量改成自然语言表达，但底层 entry id 仍保持稳定，便于宿主继续调用。

### `sts2_health_check`

看看本地尖塔 Agent 服务有没有正常连上。适合在联调、启动后自检、报错排查时先用一次。

### `sts2_get_status`

看看当前整体状态：连接是否正常、当前界面是什么、自动游玩是否正在运行、当前是不是 standby、最近错误和当前模式如何。

### `sts2_read_state`

顺手刷新一次当前局面，并把三层信息一起整理出来：
- 当前快照
- 当前局势摘要
- 当前猫娘同步包

适合在真正决定下一步前先读一眼完整状态。

### `sts2_set_standby`

切换待机模式。待机模式下不会继续执行动作，但会保留状态整理与同步能力。

### `sts2_start_autoplay`

让她自己玩起来。会启动后台自动游玩循环，让当前局面继续往下推进。

### `sts2_pause_autoplay`

先停一下自动玩。适合你想自己接管、或者准备临时改策略的时候使用。

### `sts2_resume_autoplay`

继续让她自己玩。从暂停处恢复自动游玩。

### `sts2_stop_autoplay`

别让她自己玩了。会停止后台自动游玩，把控制权完全收回来。

### `sts2_enable_companion_mode`

打开陪玩模式。开启后会更积极地整理局面、推送观察内容，并在合适的时候给点评和提醒。

### `sts2_disable_companion_mode`

关掉陪玩模式。关闭陪玩点评，但不影响基础状态读取和自动游玩控制。

### `sts2_apply_user_override`

按你一句话来调整策略。它会结合当前场景，把你的自然语言偏好提取成对应的事件级或敌人级覆盖。

当前这条入口还有一个额外保护：
- 如果自动游玩正在运行，会**先暂停自动游玩**
- 更新完策略后，会提示你**如要继续请手动恢复自动游玩**
- 不会在你没确认前擅自继续往下打

### `sts2_get_planned_operation`

看看她准备怎么走。适合你先想知道系统下一步打算做什么，而不是马上执行。

### `sts2_execute_planned_operation`

按建议走一步。会直接执行当前建议的下一步动作。

## 前端推送事件

插件会通过宿主消息通道发送几类被动信息，主要分成三组：

1. **状态与局面同步**
   - 当前局面摘要
   - 当前建议摘要
   - 当前陪玩模式下的同步信息

2. **自动游玩控制反馈**
   - 已开始自动游玩
   - 已暂停 / 已恢复 / 已停止
   - 策略更新后要求你手动恢复

3. **陪玩与保护提示**
   - 陪玩点评
   - 风险提醒
   - 低血量暂停
   - 危险攻击减速
   - 危险解除后恢复速度或恢复自动游玩

这些推送默认都走被动投递语义，不会强行打断主对话；具体出现频率还会受到：
- `autoplay_push_probability`
- `companion_push_probability`
- `neko_commentary_probability`
- `neko_report_hud_enabled`
等配置影响。

## 常见排查

### 调用插件入口时报连接失败

先检查：

- 游戏是否已经启动。
- `STS2 AI Agent` Mod 是否已正确放进游戏 `mods/`。
- `http://127.0.0.1:8080/health` 是否可访问。
- `plugin.toml` 里的 `base_url` 是否正确。

### `http://127.0.0.1:8080/health` 打不开

优先检查：

1. 游戏是否真的已经启动。
2. `STS2AIAgent.dll`、`STS2AIAgent.pck`、`mod_id.json` 是否都已复制到游戏目录 `mods/`。
3. 文件名是否被系统改名、重复或放错目录。
4. 你操作的是 Steam 游戏目录，而不是上游仓库目录。
5. 是否有防火墙或安全软件阻止本地端口。

### 自动游玩能运行，但前端没有收到消息

检查：

- `llm_frontend_output_enabled` 是否为 `true`。
- `llm_frontend_output_probability` 是否过低。
- `neko_reporting_enabled` 是否为 `true`。
- 联调时可先把 `llm_frontend_output_probability` 设为 `1`。
- 宿主前端是否已接收插件推送消息。

### 猫娘中途指导没有明显效果

检查：

- 当前是否处于待机状态。
- `sts2_send_neko_guidance` 是否返回 `ok`。
- 指导内容是否足够具体，例如“优先防御”“先打最低血敌人”“保留药水”。
- 当前合法动作是否真的能满足指导。

### 半自动任务迟迟不完成

检查 `stop_condition`：

- 如果是 `manual` / `none`，任务不会自动完成，需要调用 `sts2_stop_autoplay`。
- 如果是 `current_combat`，任务期间只要进入过战斗，随后离开战斗后就会完成。
- 如果是 `current_floor`，通常在当前楼层完成或进入下一层后完成。

可以调用 `sts2_get_status` 查看 `autoplay.task`。

### 事件房、弹窗或过渡态卡住

当前版本已经对事件、弹窗、过渡态做过处理，优先动作包含：

- `confirm_modal`
- `dismiss_modal`
- `choose_event_option`
- `proceed`

如果仍卡住，先用 `sts2_read_state` 查看当前 `screen` 和 `available_actions`。

### 自动游玩突然暂停或变慢

可能触发了安全保护：

- 血量比例低于 `neko_auto_low_hp_threshold` 时会暂停。
- Boss 战或危险攻击时会减速。
- 若 `neko_auto_resume_after_low_hp` 为 `true`，血量恢复到 `neko_auto_safe_hp_threshold` 后可能自动恢复。

可调用 `sts2_get_status` 查看状态，或调用 `sts2_resume_autoplay` / `sts2_stop_autoplay` 处理。
