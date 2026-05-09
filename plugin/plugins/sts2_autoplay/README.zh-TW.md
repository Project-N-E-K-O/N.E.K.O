# 快速開始

`sts2_autoplay` 用於把 `STS2 AI Agent` 暴露出來的本地《殺戮尖塔 2》狀態接入到 N.E.K.O。插件可以讀取局面、執行合法動作、按策略自動遊玩、讓貓娘選擇單張牌、向前端推送觀察資訊，並允許貓娘在背景任務中發送軟指導來影響下一輪決策。

## 使用教學

### 取得 MOD

使用 Git Clone：
```text
git clone https://github.com/CharTyr/STS2-Agent.git
```

### 安裝遊戲 Mod

可以在 Steam 裡右鍵《殺戮尖塔 2》，選擇「管理 -> 瀏覽本機檔案」

Steam 預設遊戲目錄通常類似：

```text
...\Steam\steamapps\common\Slay the Spire 2
```

將 `STS2 AI Agent` mod 複製到尖塔遊戲目錄的 `mods/` 下

如果《殺戮尖塔 2》目錄下沒有 mods 資料夾，請自行建立。

```text
使用 mod 可能導致存檔遺失，請備份或利用主控台救援（在尖塔主選單按 "~" 鍵，輸入 "unlock all"，即可解鎖全角色和難度）
```

安裝完成後目錄應類似：

```text
Slay the Spire 2/
  mods/
    STS2AIAgent.dll
    STS2AIAgent.pck
    mod_id.json
```

### 啟動遊戲並確認介面

先正常啟動遊戲，讓 Mod 隨遊戲載入。

在載入 mod 後，在 NEKO 中，啟用貓爪，開啟插件，進入插件面板，手動啟動殺戮尖塔插件

### 可使用的指令

【打牌】【自動代打】【通一關】【牌打的如何】【停止】
【打出一張牌】【打出某張牌】【推薦一張牌】……諸如此類…

## 功能概覽

- 連接本機 `STS2 AI Agent` HTTP 服務並讀取遊戲狀態。
- 支援手動執行一步、背景半自動遊玩、暫停、恢復和停止。
- 支援三種決策模式：`full-program`、`half-program`、`full-model`。
- 支援按角色載入策略文件，策略檔案位於 `strategies/`。
- 支援貓娘單次選牌：只從目前可打出的 `play_card` 動作中選擇一張牌，先推送原因，再執行。
- 支援貓娘軟指導：使用者或貓娘可以發送自然語言指導，下一輪 LLM 決策會參考。
- 支援背景觀察回報：把目前樓層、戰鬥、手牌、敵人意圖、LLM 理由等推送給前端。
- 支援安全保護：低血量暫停、Boss/危險攻擊減速、血量恢復後自動恢復、殘血求生策略、收益最大化和連攜評分。

## 依賴

本插件依賴上游 Mod `STS2 AI Agent` 提供的本機 HTTP 服務：

- 遊戲內 Mod：`STS2AIAgent`
- 預設本機介面位址：`http://127.0.0.1:8080`
- 健康檢查位址：`http://127.0.0.1:8080/health`

也就是說，這個插件運作的前提是：

1. 已經把 `STS2 AI Agent` 的 Mod 安裝進《殺戮尖塔 2》。
2. 遊戲啟動後，`http://127.0.0.1:8080/health` 可以連線。
3. N.E.K.O 中啟用了 `sts2_autoplay` 插件。

## 本插件設定

設定檔：`plugin.toml`

### 基礎設定

| 設定項 | 預設值 | 說明 |
| --- | --- | --- |
| `base_url` | `http://127.0.0.1:8080` | 尖塔本機 Agent 位址。 |
| `connect_timeout_seconds` | `5` | 連線逾時秒數。 |
| `request_timeout_seconds` | `15` | 請求逾時秒數。 |
| `poll_interval_idle_seconds` | `3` | 空閒狀態輪詢間隔。 |
| `poll_interval_active_seconds` | `1` | 自動遊玩執行時輪詢間隔。 |
| `action_interval_seconds` | `1.5` | 每個動作之間的額外間隔。 |
| `post_action_delay_seconds` | `0.5` | 動作執行後等待局面穩定的間隔。 |
| `autoplay_on_start` | `false` | 插件啟動後是否自動開始遊玩。 |
| `semi_auto_autoplay` | `true` | 啟動自動遊玩時是否建立半自動任務上下文。 |
| `mode` | `half-program` | 目前自動遊玩模式。 |
| `character_strategy` | `defect` | 角色策略名稱，對應 `strategies/<name>.md`。 |
| `max_consecutive_errors` | `3` | 最大連續錯誤次數，超過後視為斷線。 |
| `push_notifications` | `true` | 歷史保留欄位。 |
| `event_stream_enabled` | `false` | 預留欄位，目前未實際啟用。 |

### 決策模式

`mode` 支援以下值，也支援對應中文別名：

| 模式 | 中文別名 | 說明 |
| --- | --- | --- |
| `full-program` | `全程序` | 純程式啟發式，不呼叫模型。 |
| `half-program` | `半程序` | 先進行程式預檢查，再呼叫一次模型決策，並做合法性校驗/回退。 |
| `full-model` | `全模型` | 兩次模型呼叫：先 reasoning，再 final action；中間進行程式檢查，最終再做合法性驗證。 |

### 角色策略

`character_strategy` 會按 `strategies/<name>.md` 查找策略文件。目前內建策略：

- `defect`
- `ironclad`
- `silent_hunter`
- `necrobinder`
- `regent`

你可以在 `strategies/` 中新增 Markdown 檔案來擴展策略。例如：

```text
strategies/my_strategy.md
```

然後把設定或入口參數設為：

```text
my_strategy
```

### 前端推送與貓娘觀察

| 設定項 | 預設值 | 說明 |
| --- | --- | --- |
| `llm_frontend_output_enabled` | `true` | 是否把自動遊玩動作/錯誤主動推送到前端。 |
| `llm_frontend_output_probability` | `0.15` | 普通動作推送機率，範圍會收斂到 `0.0 ~ 1.0`。錯誤會強制推送。 |
| `neko_reporting_enabled` | `true` | 是否推送貓娘觀察報告。 |
| `neko_report_interval_steps` | `1` | 每隔多少個自動遊玩步驟推送一次觀察報告，至少為 `1`。 |
| `neko_commentary_enabled` | `true` | 是否在觀察報告中產生貓娘即時解說。關閉後仍可推送結構化觀察報告，但 `live_commentary.text` 會保持空。 |
| `neko_commentary_probability` | `0.65` | 普通低優先解說的觸發機率，範圍會收斂到 `0.0 ~ 1.0`；低血量、斬殺、高攻擊等高優先場景可繞過機率。 |
| `neko_commentary_min_interval_seconds` | `4` | 同一低優先場景重複解說的最小間隔秒數，用於減少洗版和重複口播。 |
| `neko_critical_commentary_always` | `true` | 是否讓 `critical` / `high` 緊急度解說總是播報，例如殘血、斬殺、敵人高攻擊。 |
| `neko_guidance_max_queue` | `50` | 貓娘軟指導佇列最大長度。 |

貓娘觀察報告會夾帶精簡後的 `report`、`neko_context`、`live_commentary`、`task` 等 metadata，供前端或對話邏輯判斷這是「過程觀察」，不是任務完成通知。為節省使用者 token，推送內容只保留目前動作、血量、手牌、敵人、戰術摘要、已消費指導和任務摘要。

`live_commentary` 會給前端/TTS 提供短口播欄位：`text`、`scene`、`mood`、`urgency`、`priority`、`tts`、`interrupt`、`tone`、`character_strategy`。解說會按場景從範本池隨機選擇，減少重複；也會按角色策略調整傾向，例如 `defect` 偏理性、`ironclad` 偏穩健。目前涵蓋殘血、低血量、斬殺、敵人來襲、防守、普通戰鬥、獎勵、商店、休息點、事件、地圖，以及戰鬥結束、關鍵遺物、路線選擇完成等事件級解說。

### 安全保護與自主動作

| 設定項 | 預設值 | 說明 |
| --- | --- | --- |
| `neko_auto_low_hp_threshold` | `0.3` | 目前血量比例低於該值時，背景自動遊玩會自主暫停。 |
| `neko_auto_safe_hp_threshold` | `0.5` | 血量恢復到該比例後，可自動恢復。 |
| `neko_auto_dangerous_attack_threshold` | `20` | 敵人來襲傷害達到該值且會破防時，自動減速。 |
| `neko_auto_resume_after_low_hp` | `true` | 低血量暫停後是否允許血量恢復時自動恢復。 |
| `neko_desperate_enabled` | `true` | 是否啟用殘血求生策略。 |
| `neko_desperate_hp_threshold` | `0.2` | 觸發殘血求生策略的血量比例。 |
| `neko_maximize_enabled` | `true` | 是否啟用收益最大化出牌選擇。 |
| `neko_synergy_enabled` | `true` | 是否啟用連攜/協同評分。 |

目前自主動作包括：

- `pause`：低血量時暫停，等待使用者或貓娘指令。
- `slow_down`：Boss 戰或危險攻擊時把動作間隔暫時調慢。
- `resume`：滿足安全血量條件後恢復。

## 一般使用者推薦說法

一般使用者不需要記住下面的底層入口。優先把使用者原話交給 `sts2_neko_command`，由插件內部判斷是查看狀態、給建議、實際出牌、執行一步、開啟自動遊玩、暫停、恢復、停止、複盤最近出牌、回答自動遊玩疑問，還是把話術當作自動遊玩中的軟指導。

推薦互動規則：

| 使用者說法 | 插件行為 |
| --- | --- |
| `尖塔連上了嗎` / `現在什麼情況` | 只查看連線、狀態或快照，不操作遊戲。 |
| `這回合怎麼打` / `打哪張牌好` | 只推薦一張可打出的牌並說明理由，不自動出牌。 |
| `幫我打一張牌` / `選一張牌打出去` | 明確授權後，只從 `play_card` 動作裡選一張並打出。 |
| `幫我打一步` / `執行一步` | 明確授權後執行一步合法動作，可能包含結束回合、選獎勵或走地圖。 |
| `幫我打這一關` / `自動打一下` | 啟動半自動遊玩，預設以目前樓層完成為停止條件。 |
| `先防一下` / `別貪輸出` | 自動遊玩執行中會作為軟指導進入下一輪決策；未執行時會保守要求釐清，不會擅自執行。 |
| `剛才打得怎麼樣` / `複盤一下剛才那張牌` | 根據最近輕量快照給出牌感點評，不會操作遊戲。 |
| `為什麼這麼打` / `你在幹嘛` | 自動遊玩執行中回答目前策略和局面依據，不會額外操作。 |
| `暫停一下` / `繼續` / `停了吧` | 分別暫停、恢復或停止自動遊玩。 |

安全預設：諮詢不操作，模糊表達不執行危險動作；只有使用者明確說「幫我打」「執行」「自動打」「託管」時才會實際操作。

## 插件入口

下面這些入口已經暴露給宿主，可直接在 N.E.K.O 中呼叫。一般使用者場景建議優先呼叫 `sts2_neko_command`，其他入口主要作為開發者精確控制介面。

### `sts2_neko_command`

殺戮尖塔自然語言總入口。使用者沒有明確指定底層工具時優先呼叫它。

參數：

- `command`：必填，使用者原話。例如：`這回合怎麼打`、`幫我打一張牌`、`先防一下`、`暫停一下`。
- `scope`：可選，預設 `auto`。可選值：`auto`、`status`、`advice`、`one_card`、`one_action`、`autoplay`、`control`、`guidance`、`review`、`question`、`chat`。
- `confirm`：可選，預設 `false`。用於確認持續託管等高風險操作。

回傳中會包含 `intent`、`action`、`executed`、`needs_confirmation`、`summary` 和底層 `result`。

### `sts2_health_check`

檢查本機尖塔 Agent 服務是否可用。

### `sts2_refresh_state`

強制刷新一次目前尖塔狀態。

### `sts2_get_status`

取得連線狀態、自動遊玩狀態、目前模式、角色策略、半自動任務、最近錯誤、最近動作等資訊。

### `sts2_get_snapshot`

取得最近快取的遊戲快照和目前可執行動作。

### `sts2_step_once`

按目前策略執行一步。

### `sts2_play_one_card_by_neko`

讓貓娘選擇並打出一張牌。

參數：

- `objective`：可選，使用者授權目標。例如：`幫我選一張牌打出去`。

行為：

1. 讀取目前玩家、手牌、敵人和合法動作。
2. 只保留 `play_card` 動作。
3. 讓目前模式/策略選擇一張牌。
4. 先向前端推送「準備打出哪張牌和原因」。
5. 重新校驗動作仍然合法。
6. 執行出牌並推送完成觀察。

如果目前沒有可打出的牌，會回傳 `idle`，並推送失敗原因。

### `sts2_start_autoplay`

啟動背景半自動遊玩迴圈。

參數：

- `objective`：可選，使用者授權目標。例如：`幫我打這一關`。
- `stop_condition`：停止條件，預設 `current_floor`。

`stop_condition` 支援：

- `current_floor`：目前樓層完成或進入下一層後結束。
- `current_combat` / `combat`：任務期間只要進入過戰鬥，隨後離開戰鬥後結束。
- `manual` / `none`：不自動完成，需要手動停止。

啟動後插件會建立半自動任務上下文，並向前端推送任務開始事件。任務完成時會推送 `semi_auto_task_completed`。

### `sts2_pause_autoplay`

暫停自動遊玩。

### `sts2_resume_autoplay`

恢復已暫停且背景任務仍存在的自動遊玩。如果背景任務已經不存在，會安全回傳 `idle`，不會隱式重新啟動自動遊玩。

### `sts2_stop_autoplay`

停止自動遊玩並清除半自動任務上下文。

### `sts2_get_history`

取得最近動作和狀態歷史。

參數：

- `limit`：回傳筆數，預設 `20`，範圍會限制在 `1 ~ 100`。

### `sts2_send_neko_guidance`

向背景自動遊玩發送貓娘軟指導。指導會進入佇列，並在下一輪 LLM 決策時注入上下文。

參數：

- `content`：必填，自然語言指導內容。例如：`先防一下，別急著輸出`。
- `step`：可選，對應步數。
- `type`：可選，預設 `soft_guidance`。

### `sts2_set_mode`

設定自動遊玩模式。

參數：

- `mode`：支援 `full-program` / `全程序`、`half-program` / `半程序`、`full-model` / `全模型`。

### `sts2_set_character_strategy`

設定角色策略名稱。

參數：

- `character_strategy`：會經過名稱標準化後比對 `strategies/<name>.md`。例如 `defect` 會比對 `strategies/defect.md`。

### `sts2_set_speed`

設定速度參數，並寫回本機 `plugin.toml`。

參數：

- `action_interval_seconds`
- `post_action_delay_seconds`
- `poll_interval_active_seconds`

## 典型使用方式

### 檢查連線

1. 啟動《殺戮尖塔 2》。
2. 確認 `http://127.0.0.1:8080/health` 可連線。
3. 在 N.E.K.O 中呼叫 `sts2_health_check`。

### 手動執行一步

呼叫：

```text
sts2_step_once
```

插件會根據目前 `mode` 和 `character_strategy` 選擇一個合法動作並執行。

### 讓貓娘打一張牌

使用者可以對貓娘說類似：

```text
幫我選一張牌打出去
```

宿主應呼叫：

```text
sts2_play_one_card_by_neko
```

插件會只從目前可打出的卡牌中選擇，不會選擇結束回合、地圖、獎勵或其他動作。

### 讓貓娘幫忙打一關

使用者可以說：

```text
幫我打這一關
```

宿主應呼叫：

```text
sts2_start_autoplay
```

推薦參數：

```json
{
  "objective": "幫我打這一關",
  "stop_condition": "current_floor"
}
```

任務執行期間，觀察事件只是過程回報，不代表完成。只有收到半自動任務完成事件時，才應告訴使用者這一關完成。

### 中途指導

自動遊玩中，使用者或貓娘可以發送指導：

```text
先防一下吧，別吃太多傷害
```

應呼叫：

```text
sts2_send_neko_guidance
```

推薦參數：

```json
{
  "content": "先防一下吧，別吃太多傷害",
  "type": "soft_guidance"
}
```

指導會在下一輪 LLM 決策時被參考。`full-program` 模式不依賴模型，軟指導影響有限。

## 前端推送事件

插件會透過宿主的訊息通道推送以下幾類事件。除任務開始/完成、錯誤和單卡預告外，普通觀察會盡量使用短文字和精簡 metadata，以減少使用者 token 消耗。

| 事件類型 | 說明 |
| --- | --- |
| `action` | 普通自動遊玩動作觀察，受機率控制。 |
| `error` | 自動遊玩錯誤，強制推送。 |
| `neko_report` | 完整貓娘觀察報告，包含目前局面、手牌、敵人、戰術摘要和模型理由。 |
| `neko_card_task_planned` | 貓娘單卡任務計畫打出某張牌。 |
| `neko_card_task_completed` | 貓娘單卡任務已執行。 |
| `neko_card_task_failed` | 貓娘單卡任務無法執行。 |
| `semi_auto_task_started` | 半自動任務開始。 |
| `semi_auto_task_completed` | 半自動任務完成。 |
| `neko_autonomous_action` | 系統自主暫停、減速或恢復。 |

注意：`neko_report` 是過程觀察，不是任務完成通知。前端或對話邏輯不應把單步動作、出牌、結束回合或狀態刷新說成「任務完成」「打完 Boss」「戰鬥結束」或「通關」。如果貓娘要影響下一輪決策，應呼叫 `sts2_send_neko_guidance`；如果要硬控制流程，應呼叫暫停、恢復或停止入口。

## 常見排查

### 呼叫插件入口時報連線失敗

先檢查：

- 遊戲是否已經啟動。
- `STS2 AI Agent` Mod 是否已正確放進遊戲 `mods/`。
- `http://127.0.0.1:8080/health` 是否可連線。
- `plugin.toml` 裡的 `base_url` 是否正確。

### `http://127.0.0.1:8080/health` 打不開

優先檢查：

1. 遊戲是否真的已經啟動。
2. `STS2AIAgent.dll`、`STS2AIAgent.pck`、`mod_id.json` 是否都已複製到遊戲目錄 `mods/`。
3. 檔案名是否被系統改名、重複或放錯目錄。
4. 你操作的是 Steam 遊戲目錄，而不是上游倉庫目錄。
5. 是否有防火牆或安全軟體阻止本機連接埠。

### 自動遊玩能執行，但前端沒有收到訊息

檢查：

- `llm_frontend_output_enabled` 是否為 `true`。
- `llm_frontend_output_probability` 是否過低。
- `neko_reporting_enabled` 是否為 `true`。
- 聯調時可先把 `llm_frontend_output_probability` 設為 `1`。
- 宿主前端是否已接收插件推送訊息。

### 貓娘中途指導沒有明顯效果

檢查：

- 目前模式是否為 `half-program` 或 `full-model`。
- `sts2_send_neko_guidance` 是否回傳 `ok`。
- 指導內容是否足夠具體，例如「優先防禦」「先打最低血敵人」「保留藥水」。
- 目前合法動作是否真的能滿足指導。

### 半自動任務遲遲不完成

檢查 `stop_condition`：

- 如果是 `manual` / `none`，任務不會自動完成，需要呼叫 `sts2_stop_autoplay`。
- 如果是 `current_combat`，任務期間只要進入過戰鬥，隨後離開戰鬥後就會完成。
- 如果是 `current_floor`，通常在目前樓層完成或進入下一層後完成。

可以呼叫 `sts2_get_status` 查看 `autoplay.task`。

### 事件房、彈窗或過渡狀態卡住

目前版本已經對事件、彈窗、過渡狀態做過處理，優先動作包含：

- `confirm_modal`
- `dismiss_modal`
- `choose_event_option`
- `proceed`

如果仍卡住，先用 `sts2_get_snapshot` 查看目前 `screen` 和 `available_actions`。

### 自動遊玩突然暫停或變慢

可能觸發了安全保護：

- 血量比例低於 `neko_auto_low_hp_threshold` 時會暫停。
- Boss 戰或危險攻擊時會減速。
- 若 `neko_auto_resume_after_low_hp` 為 `true`，血量恢復到 `neko_auto_safe_hp_threshold` 後可能自動恢復。

可呼叫 `sts2_get_status` 查看狀態，或呼叫 `sts2_resume_autoplay` / `sts2_stop_autoplay` 處理。
