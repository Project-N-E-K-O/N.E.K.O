
# 將 N.E.K.O. 接入 QwenPaw

為相容既有設定，N.E.K.O. 仍將 QwenPaw 整合稱為 **OpenClaw**。本指南中的 OpenClaw 開關會連線到另外執行的 QwenPaw 服務。

## 1. 核對來源並安裝

請以 [QwenPaw 官方儲存庫](https://github.com/agentscope-ai/QwenPaw)的目前說明為準。以下命令會下載並直接執行遠端安裝腳本；若安全政策有要求，請先審閱腳本。受限網路或受管裝置可能阻止安裝。

macOS / Linux：

```bash
curl -fsSL https://qwenpaw.agentscope.io/install.sh | bash
```

Windows PowerShell：

```powershell
irm https://qwenpaw.agentscope.io/install.ps1 | iex
```

安裝程式會準備 `uv`、隔離環境、QwenPaw 及其相依套件。完成後請開啟新的終端機。

## 2. 初始化

```bash
qwenpaw init --defaults
```

接受前請閱讀 QwenPaw 顯示的安全提示。同一個本機執行個體能存取其執行帳戶可用的檔案、命令及憑證；不要讓互不信任的使用者共用。

![QwenPaw 初始化安全提示](assets/openclaw_guide/image1.png)

## 3. 啟動並確認

```bash
qwenpaw app
```

預設主控台位址是 `http://127.0.0.1:8088/`。保持終端機執行，並在瀏覽器開啟該位址。若頁面無法載入，請先處理 QwenPaw 啟動錯誤，再啟用 N.E.K.O.。

除非已了解並設定驗證與網路邊界，否則不要將服務暴露到 localhost 之外。

## 4. 在 QwenPaw 中設定模型

在 QwenPaw 主控台開啟模型頁面，選擇 provider，填入所需憑證並儲存；再回到聊天頁選擇已設定的模型。可用 provider 與模型名稱由目前安裝的 QwenPaw 版本決定，請以即時介面為準，不要依賴複製的清單。

![QwenPaw 模型設定頁面](assets/openclaw_guide/image2.png)

## 5. 選用：執行器人設

隨文件提供的[替換檔案包](/downloads/qwenpaw-executor-profile.zip)包含 `SOUL.md`、`AGENTS.md` 與 `PROFILE.md`，可用於偏執行器的人設。此步驟不是連線 N.E.K.O. 的必要條件，而且會改變 QwenPaw 行為。

替換前：

1. 停止 QwenPaw，並備份 `.qwenpaw/workspaces/default`；
2. 檢查壓縮檔內容，與目前 workspace 比較；
3. 只複製確認要替換的檔案。

設定目錄通常位於 Windows 的 `%USERPROFILE%\.qwenpaw`，或 macOS/Linux 的 `~/.qwenpaw`。刪除 `BOOTSTRAP.md` 只屬於這套選用執行器人設流程，並非連線 N.E.K.O. 的要求。修改後重新執行 `qwenpaw app`。

## 6. 在 N.E.K.O. 中啟用

1. 啟動 QwenPaw 並保持執行。
2. 開啟 N.E.K.O. 的貓爪／Agent 面板。
3. 開啟 Agent 總開關。
4. 開啟 **OpenClaw** 子開關。
5. 等待可用性檢查。

N.E.K.O. 預設連線 `http://127.0.0.1:8088`。若 QwenPaw 使用其他位址，請在 N.E.K.O. 的 core 設定中更新 `openclawUrl`（adapter 也接受 `qwenpawUrl`），再重試。

目前 adapter 同時識別 QwenPaw v2 主控台 API 與舊版 agent 相容 API。可用性檢查會依實際版本探測 `/api/version` 或 `/api/agent/health`，之後使用相符的主控台或 agent endpoint。預設 console 情境不需要另建 channel 檔案。
