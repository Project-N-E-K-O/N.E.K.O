# 快速開始

`sts2_autoplay` 會把本地 `STS2 AI Agent` 暴露出來的《Slay the Spire 2》局面接入到 N.E.K.O，讓你既可以看局面、看建議，也可以把尖塔交給她短時托管，並在途中用自然語言調整策略。當前這版的互動面已經收斂成幾類核心能力：看狀態／看目前局面、自動遊玩控制、陪玩模式、用一句話調整策略、查看下一步建議，以及按建議走一步。

## 使用教學

### 取得 MOD

使用 Git：
```text
https://github.com/CharTyr/STS2-Agent/releases
```

### 安裝遊戲 Mod

可以在 Steam 裡右鍵 *Slay the Spire 2*，選擇 管理 -> 瀏覽本機檔案。

Steam 預設遊戲目錄通常類似：

```text
...\Steam\steamapps\common\Slay the Spire 2
```

將 `STS2 AI Agent` mod 複製到尖塔遊戲目錄的 `mods/` 之下。

如果 *Slay the Spire 2* 目錄下沒有 `mods` 資料夾，請自行建立。

```text
使用 mod 可能導致存檔遺失，請備份，或利用主控台自助補償（在尖塔主選單按下「~」鍵，輸入「unlock all」，即可解鎖全部角色與難度）。
```

安裝完成後目錄應類似：

```text
Slay the Spire 2/
  mods/
    STS2AIAgent.dll
    STS2AIAgent.pck
    mod_id.json
```

### 設定 mod 埠

mod 預設監聽埠為 `8080`。這是常見埠，可能會被其他軟體占用。

可以開啟環境變數，在系統變數／全域變數處新增 `STS2_API_PORT`，值可以選擇較冷門的埠，例如 `18080`、`38080`。

範例：變數名稱 `STS2_API_PORT`，變數值 `18080`。

提示：可以用以下方式快速開啟環境變數：

- 按下 Win + R 組合鍵，開啟「執行」對話框。

- 輸入以下命令並按 Enter：

```text
rundll32 sysdm.cpl,EditEnvironmentVariables
```

### 啟動遊戲並確認介面

先正常啟動遊戲，讓 Mod 隨遊戲一起載入。

第一次切換到 mod 模式可能會閃退一次，屬於正常現象，再次啟動遊戲即可。

在 mod 載入後，請在 N.E.K.O 中啟用 Cat Paw、開啟插件、進入插件面板，並手動啟動殺戮尖塔插件。

如果你在打開插件或初始化插件的同時，剛好也在《Slay the Spire 2》裡進行操作，插件第一次回應可能會比平常慢一拍，屬於正常現象。等目前局面同步完成後，後續回應通常就會恢復正常。

### 可使用的指令

【打牌】【自動代打】【通一關】【牌打得如何】【停止】
【打出一張牌】【打出某張牌】【推薦一張牌】……諸如此類……

## 聯絡人

如有任何問題，請把遊戲執行日誌和 N.E.K.O 執行日誌寄送到 zhaijiunknown@outlook.com。

遊戲執行日誌：
```text
%AppData%\SlayTheSpire2\logs
```

N.E.K.O 執行日誌：
```text
您的使用者資料夾\AppData\Local\N.E.K.O\logs
```

## 功能概覽

- 連接本地 `STS2 AI Agent` HTTP 服務並讀取目前局面。
- 支援一鍵查看目前局面：會刷新一次狀態，並同時整理快照、局勢摘要和貓娘同步包。
- 支援背景自動遊玩控制：啟動、暫停、恢復、停止，以及按建議執行下一步。
- 支援陪玩模式：在不打斷主流程的前提下，按配置推送觀察、點評和提醒。
- 支援自然語言策略調整：使用者一句話就可以把目前事件／敵人級偏好寫進策略覆寫。
- 支援查看下一步建議：先看看她準備怎麼走，再決定是否按建議走一步。
- 支援安全保護：低血量暫停、危險攻擊減速、危險解除後恢復速度，以及必要時自動恢復自動遊玩。
- 支援前端被動推送：局面同步、觀察資訊、陪玩提示和控制回饋都會透過插件訊息通道送給宿主。

## 本插件配置

配置檔案：`plugin.toml`

### 基礎配置

| 配置項 | 預設值 | 說明 |
| --- | --- | --- |
| `base_url` | `http://127.0.0.1:8080` | 尖塔本地 Agent 位址。 |
| `connect_timeout_seconds` | `5` | 連線逾時秒數。 |
| `request_timeout_seconds` | `15` | 請求逾時秒數。 |
| `poll_interval_idle_seconds` | `3` | 閒置狀態輪詢間隔。 |
| `poll_interval_active_seconds` | `1` | 自動遊玩執行時輪詢間隔。 |
| `action_interval_seconds` | `1.5` | 每個動作之間的額外間隔。 |
| `post_action_delay_seconds` | `0.5` | 動作執行後等待局面穩定的間隔。 |
| `autoplay_on_start` | `false` | 插件啟動後是否自動開始遊玩。 |
| `character_strategy` | `defect` | 目前預設策略名，執行時會按局面對應到適合的策略上下文。 |
| `max_consecutive_errors` | `3` | 最大連續錯誤次數，超過後視為連線異常。 |

### 前端推送與陪玩觀察

| 配置項 | 預設值 | 說明 |
| --- | --- | --- |
| `llm_frontend_output_enabled` | `true` | 是否允許把自動遊玩動作／錯誤主動推送到前端。 |
| `llm_frontend_output_probability` | `1.0` | 普通動作推送機率。錯誤和關鍵控制回饋仍可能強制推送。 |
| `autoplay_push_probability` | `0.5` | 非陪玩狀態下，普通局面同步推送的機率。 |
| `companion_push_probability` | `0.7` | 陪玩模式下，普通局面同步推送的機率。 |
| `neko_reporting_enabled` | `true` | 是否啟用貓娘觀察能力。 |
| `neko_report_interval_steps` | `1` | 每隔多少個自動遊玩步驟整理一次觀察內容。 |
| `neko_report_hud_enabled` | `true` | 是否把整理好的觀察內容實際推送到前端 HUD／訊息通道。 |
| `neko_commentary_enabled` | `true` | 是否允許產生陪玩點評與提醒。 |
| `neko_commentary_probability` | `0.65` | 普通低優先級點評的觸發機率。 |
| `neko_commentary_min_interval_seconds` | `4` | 同類點評的最小間隔，用來減少洗版。 |
| `neko_critical_commentary_always` | `true` | 高優先級提醒是否總是播報。 |
| `neko_guidance_max_queue` | `50` | 指導／偏好相關上下文的內部佇列上限。 |

### 自動保護與節奏控制

| 配置項 | 預設值 | 說明 |
| --- | --- | --- |
| `neko_auto_low_hp_threshold` | `0.3` | 血量比例低於該值時，自動遊玩會優先暫停。 |
| `neko_auto_safe_hp_threshold` | `0.5` | 血量恢復到該值後，可重新視為安全。 |
| `neko_auto_dangerous_attack_threshold` | `20` | 敵人高傷害意圖達到該閾值時，可能觸發減速保護。 |
| `neko_auto_resume_after_low_hp` | `true` | 低血量暫停後，在重新安全時是否允許自動恢復。 |
| `neko_desperate_enabled` | `true` | 是否啟用殘血求生姿態。 |
| `neko_desperate_hp_threshold` | `0.2` | 觸發殘血求生姿態的血量比例。 |
| `neko_maximize_enabled` | `true` | 是否啟用偏收益最大化的決策傾向。 |

## 一般使用者推薦說法

一般使用者不需要記住底層參數。優先把原話交給目前保留的高層能力，讓插件自行判斷你是在看局面、調整策略，還是準備按建議執行一步。

推薦理解方式：

| 你想表達什麼 | 更適合用什麼能力 |
| --- | --- |
| `看看現在什麼情況` | `sts2_get_status` |
| `看看目前局面` | `sts2_read_state` |
| `讓她自己玩起來` / `先停一下自動玩` / `繼續讓她自己玩` / `別讓她自己玩了` | autoplay 控制入口 |
| `按我這句來調整策略：這個事件優先低代價路線` | `sts2_apply_user_override` |
| `看看她準備怎麼走` | `sts2_get_planned_operation` |
| `按建議走一步` | `sts2_execute_planned_operation` |
| `打開陪玩模式` / `關掉陪玩模式` | companion mode 控制入口 |

目前推薦的互動順序是：
1. 先看局面
2. 再看她準備怎麼走
3. 如果你有想法，用一句話調整策略
4. 最後決定是按建議走一步，還是讓她繼續自動玩

## 插件入口

下面這些入口是目前主腳本真正保留的對外能力。名稱已盡量改成自然語言表達，但底層 entry id 仍保持穩定，方便宿主繼續呼叫。

### `sts2_health_check`

看看本地尖塔 Agent 服務有沒有正常連上。適合在聯調、啟動後自檢、報錯排查時先用一次。

### `sts2_get_status`

看看目前整體狀態：連線是否正常、目前畫面是什麼、自動遊玩是否正在執行、目前是不是 standby、最近錯誤和目前模式如何。

### `sts2_read_state`

順手刷新一次目前局面，並把三層資訊一起整理出來：
- 目前快照
- 目前局勢摘要
- 目前貓娘同步包

適合在真正決定下一步前先讀一眼完整狀態。

### `sts2_set_standby`

切換待機模式。待機模式下不會繼續執行動作，但仍會保留狀態整理與同步能力。

### `sts2_start_autoplay`

讓她自己玩起來。會啟動背景自動遊玩迴圈，讓目前局面繼續往下推進。

### `sts2_pause_autoplay`

先停一下自動玩。適合你想自己接手，或準備臨時改策略時使用。

### `sts2_resume_autoplay`

繼續讓她自己玩。從暫停處恢復自動遊玩。

### `sts2_stop_autoplay`

別讓她自己玩了。會停止背景自動遊玩，把控制權完全收回來。

### `sts2_enable_companion_mode`

打開陪玩模式。開啟後會更積極地整理局面、推送觀察內容，並在合適時給出點評和提醒。

### `sts2_disable_companion_mode`

關掉陪玩模式。關閉陪玩點評，但不影響基礎狀態讀取和自動遊玩控制。

### `sts2_apply_user_override`

按你一句話來調整策略。它會結合目前場景，把你的自然語言偏好提取成對應的事件級或敵人級覆寫。

目前這條入口還有一個額外保護：
- 如果自動遊玩正在執行，會**先暫停自動遊玩**
- 更新完策略後，會提示你**如要繼續請手動恢復自動遊玩**
- 不會在你沒確認前擅自繼續往下打

### `sts2_get_planned_operation`

看看她準備怎麼走。適合你先想知道系統下一步打算做什麼，而不是馬上執行。

### `sts2_execute_planned_operation`

按建議走一步。會直接執行目前建議的下一步動作。

## 典型使用方式

### 檢查連線

1. 啟動《Slay the Spire 2》。
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

指導會在後續推薦與自動執行中被參考。

## 前端推送事件

插件會透過宿主訊息通道發送幾類被動資訊，主要分成三組：

1. **狀態與局面同步**
   - 目前局面摘要
   - 目前建議摘要
   - 目前陪玩模式下的同步資訊

2. **自動遊玩控制回饋**
   - 已開始自動遊玩
   - 已暫停 / 已恢復 / 已停止
   - 策略更新後要求你手動恢復

3. **陪玩與保護提示**
   - 陪玩點評
   - 風險提醒
   - 低血量暫停
   - 危險攻擊減速
   - 危險解除後恢復速度或恢復自動遊玩

這些推送預設都走被動投遞語義，不會強行打斷主對話；具體出現頻率還會受到：
- `autoplay_push_probability`
- `companion_push_probability`
- `neko_commentary_probability`
- `neko_report_hud_enabled`
等配置影響。

## 常見排查

### 呼叫插件入口時顯示連線失敗

先檢查：

- 遊戲是否已經啟動。
- `STS2 AI Agent` Mod 是否已正確放進遊戲 `mods/`。
- `http://127.0.0.1:8080/health` 是否可連線。
- `plugin.toml` 裡的 `base_url` 是否正確。

### `http://127.0.0.1:8080/health` 打不開

優先檢查：

1. 遊戲是否真的已經啟動。
2. `STS2AIAgent.dll`、`STS2AIAgent.pck`、`mod_id.json` 是否都已複製到遊戲目錄的 `mods/`。
3. 檔名是否被系統改名、重複或放錯目錄。
4. 你操作的是 Steam 遊戲目錄，而不是上游倉庫目錄。
5. 是否有防火牆或安全軟體阻止本地埠口。

### 自動遊玩能執行，但前端沒有收到訊息

檢查：

- `llm_frontend_output_enabled` 是否為 `true`。
- `llm_frontend_output_probability` 是否過低。
- `neko_reporting_enabled` 是否為 `true`。
- 聯調時可先把 `llm_frontend_output_probability` 設為 `1`。
- 宿主前端是否已接收到插件推送訊息。

### 貓娘中途指導沒有明顯效果

檢查：

- 目前是否處於待機狀態。
- `sts2_send_neko_guidance` 是否回傳 `ok`。
- 指導內容是否夠具體，例如「優先防禦」「先打最低血敵人」「保留藥水」。
- 目前合法動作是否真的能滿足指導。

### 半自動任務遲遲不完成

檢查 `stop_condition`：

- 如果是 `manual` / `none`，任務不會自動完成，需要呼叫 `sts2_stop_autoplay`。
- 如果是 `current_combat`，任務期間只要進入過戰鬥，隨後離開戰鬥後就會完成。
- 如果是 `current_floor`，通常在目前樓層完成或進入下一層後完成。

可以呼叫 `sts2_get_status` 查看 `autoplay.task`。

### 事件房、彈窗或過渡狀態卡住

目前版本已經對事件、彈窗、過渡狀態做過處理，優先動作包括：

- `confirm_modal`
- `dismiss_modal`
- `choose_event_option`
- `proceed`

如果仍卡住，先用 `sts2_read_state` 查看目前 `screen` 和 `available_actions`。

### 自動遊玩突然暫停或變慢

可能觸發了安全保護：

- 血量比例低於 `neko_auto_low_hp_threshold` 時會暫停。
- Boss 戰或危險攻擊時會減速。
- 若 `neko_auto_resume_after_low_hp` 為 `true`，血量恢復到 `neko_auto_safe_hp_threshold` 後可能自動恢復。

可呼叫 `sts2_get_status` 查看狀態，或呼叫 `sts2_resume_autoplay` / `sts2_stop_autoplay` 處理。
