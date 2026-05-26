# QQ 自動回覆插件

本插件透過 OneBot 協議連接 QQ，提供基於權限的智慧自動回覆。支援私聊與群組訊息，並整合 AI 對話能力。

## 啟動與引導

1. 先下載一個 OneBot 實作（推薦 NapCat）。以下範例會以 NapCat 為主。
   開啟：
   ```text 
   https://github.com/NapNeko/NapCatQQ/releases
   ```
   任選一個版本下載即可（推薦 `NapCat.Shell.zip`）。
2. 啟動 NapCat，並打開 NapCat 的設定頁面。
3. 在左側工具列中選擇 **網路設定**。
4. 新增一個 **WS Server**，啟用後記得保存。
5. 啟動插件。
6. 返回一次。
7. 再次打開本插件的面板。
8. 按照引導順序完成設定。

目前流程補充：
- 插件在 `startup` 階段會自動建立業務設定檔（如果還不存在）。
- NapCat 會優先從你設定的執行目錄啟動；若未設定則回退到預設目錄。
- 當 NapCat 產生登入 QR Code 後，會複製到插件靜態目錄；在介面中按下 **重新整理 QR Code** 即可同步顯示最新圖片。
- 前端 UI 可直接管理：
  - OneBot 位址 / Token / PATH
  - NapCat 前景 / 背景啟動
  - 信任使用者 / 信任群組
  - 自動回覆啟動 / 停止

## 功能特色

- 透過 NapCat 支援 OneBot 協議
- 多級使用者權限管理：`admin`、`trusted`、`normal`
- 多級群組權限控制：`trusted`、`open`、`normal`
- 透過 OmniOfflineClient 產生 AI 回覆
- 管理員私聊會同步到記憶系統
- 一般訊息可按機率轉述給管理員
- open 群組模式可在未 `@` 的情況下主動接話
- 支援信任使用者暱稱管理
- WebSocket 斷線後自動指數退避重連

## 設定說明

建議直接在插件 UI 的 **QQ OneBot 服務配置** 區域完成設定。首次啟動時會自動建立業務設定檔。常用欄位包括：

- `onebot_url`：OneBot 的 WebSocket 位址，預設值為 `ws://127.0.0.1:3001`
- `token`：OneBot 存取令牌
- `napcat_directory`：NapCat 執行目錄
- `show_napcat_window`：`true` 為前景啟動並顯示控制台，`false` 為背景啟動
- `trusted_users`：信任使用者列表
- `trusted_groups`：信任群組列表
- `normal_relay_probability`：一般訊息轉述給主人的機率
- `truth_reply_probability`：在 open 群組主動回覆的機率

### 設定項目表

| 鍵值 | 類型 | 說明 |
|------|------|------|
| `onebot_url` | string | OneBot 服務的 WebSocket 位址 |
| `token` | string | OneBot 服務的存取令牌（若服務端需要） |
| `trusted_users` | array | 信任使用者列表，包含 QQ 號碼、權限等級與暱稱 |
| `trusted_groups` | array | 信任群組列表，包含群號與權限等級 |
| `normal_relay_probability` | float | 一般私聊 / 群聊訊息轉述給主人的機率 |
| `truth_reply_probability` | float | 在 `open` 群組中無需 `@` 就直接回覆的機率 |

### 群聊中的機率欄位

`trusted_groups` 裡的每個群，除了 `group_id` 和 `level` 之外，還可以帶兩個可選的機率欄位：

- `normal_relay_probability`：只在 `level = normal` 的群聊中生效。當群訊息進入「轉述給主人」路徑時，會用這個值覆蓋該群的全域預設轉述機率。
- `open_reply_probability`：只在 `level = open` 的群聊中生效。在開放群裡，沒有人 `@` 機器人時，會用這個值覆蓋該群的全域預設主動回覆機率。

說明：
- 這兩個欄位都必須是 `0 ~ 1` 之間的小數。
- 若留空，插件會回退到全域預設設定。
- `trusted` 群不會使用這兩個欄位，因為 `trusted` 群只有在明確 `@` 機器人後才會進入正常回覆流程。

## 權限等級

### 使用者權限

| 等級 | 說明 | 行為 |
|------|------|------|
| `admin` | 管理員 | 私聊直接回覆、同步記憶、稱呼為「主人」 |
| `trusted` | 信任使用者 | 私聊直接回覆、不同步記憶、可設定暱稱 |
| `normal` | 一般使用者 | 不直接回覆，可能按機率轉述給管理員 |
| `none` | 未授權 | 忽略訊息 |

### 群組權限

| 等級 | 說明 | 行為 |
|------|------|------|
| `trusted` | 信任群組 | 只有在 `@` 機器人時才回覆 |
| `open` | 開放群組 | 可在未 `@` 的情況下直接回覆；使用臨時會話記憶與角色卡上下文，但不寫入記憶庫 |
| `normal` | 一般群組 | 不直接回覆，可能按機率轉述給管理員 |
| `none` | 未授權 | 忽略訊息 |

## 額外說明

### 1. 主動發送訊息

你可以透過面板中的專用 entry，先讓 AI 生成符合角色風格的內容，再主動發送給指定使用者或群組。

補充：
- `message` 現在代表提供給 AI 的提示內容，而不是最終原樣送出的文字。
- 會沿用既有角色設定與模型設定。
- 私聊主動發送時可能會讀取記憶上下文輔助生成，但這次主動發送本身不會寫回記憶庫。
- 群組主動發送也不會寫入記憶庫。
- 私聊 entry 使用 `target` 參數：可填入純數字 QQ 號，或填入已設定於信任使用者清單中的暱稱。
- `group_id` 必須是純數字字串。
- `message` 不能為空。
- 需先啟動自動回覆並確認 OneBot 已連線，否則 entry 會直接失敗。

### 2. 停止插件

停止插件時會執行：
- 中斷 WebSocket 連線
- 清理執行中的資源

## 常見問題

### 1. 無法連線到 OneBot

**問題**：日誌中出現 `Failed to connect to OneBot`

**解決方式**：
- 確認 NapCat 是否正常運行（連接埠 3001）
- 確認 `onebot_url` 是否正確
- 確認 `token` 是否正確

### 2. 機器人沒有回覆

**問題**：送出訊息後沒有收到回覆

**解決方式**：
- 確認發送者是否存在於 `trusted_users`
- 檢查權限等級（`normal` 使用者不會收到直接回覆）
- 查看日誌確認訊息是否有被接收
- 在 `trusted` 群組中請確認有 `@` 機器人
- 在 `open` 群組中若設定正確則不需要 `@`

### 3. 記憶同步失敗

**問題**：日誌顯示記憶同步失敗

**解決方式**：
- 確認 Memory Server 正在運行
- 只有管理員的私聊會同步到記憶系統
- 群組對話（包含 `open`）只保留暫時上下文，不會寫入記憶庫

### 4. 轉述功能沒有作用

**問題**：一般使用者的訊息沒有轉述給管理員

**解決方式**：
- 確認是否已設定管理員（`level = "admin"`）
- 確認 `normal_relay_probability` 的值（預設 0.1，即 10%）
- 查看日誌確認是否有觸發轉述

## 聯絡方式

如果有任何問題，請提交 issue 或寄信到 zhaijiunknown@outlook.com。

## 授權

本插件遵循 N.E.K.O 專案的授權條款。
