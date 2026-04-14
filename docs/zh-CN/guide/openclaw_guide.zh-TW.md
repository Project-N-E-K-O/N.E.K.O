# 使用 NEKO 接入 QwenPaw

## QwenPaw 安裝指南

### 第一步：安裝

無需手動配置 Python，一行指令即可自動完成安裝。腳本會自動下載 `uv`（Python 套件管理器）、建立虛擬環境、安裝 QwenPaw 及其依賴（包含 Node.js 和前端資源）。注意：部分網路環境或企業權限管控下可能無法使用。

macOS / Linux：

```bash
curl -fsSL https://qwenpaw.agentscope.io/install.sh | bash
```

Windows（PowerShell）：

```powershell
irm https://qwenpaw.agentscope.io/install.ps1 | iex
```

### 第二步：初始化

安裝完成後，請開啟新終端並執行：

```bash
qwenpaw init --defaults
```

這裡有個很貼心的設計，就是安全警告。QwenPaw 會明確告訴你：

> 這是一個執行在你本機環境中的個人助理，可以連接各種通道、執行命令、呼叫 API。如果讓多個人共用同一個 QwenPaw 實例，他們將共享相同的權限（檔案、命令、金鑰）。

![啟用 Neko 頻道步驟圖 1](assets/openclaw_guide/image1.png)

你需要選擇 `yes` 確認已理解後才能繼續。

### 第三步：啟動

```bash
qwenpaw app
```

啟動成功後，終端最後一行會出現：

```text
INFO:     Uvicorn running on http://127.0.0.1:8088 (Press CTRL+C to quit)
```

服務啟動後，造訪 `http://127.0.0.1:8088`，即可看到 QwenPaw 的控制台介面。

## NEKO 頻道配置：讓 NEKO 接入 QwenPaw

初始化完成後，QwenPaw 會自動建立配置檔目錄。Windows 預設在 `C:\Users\你的使用者名稱\.qwenpaw`，mac 預設在 `~/.qwenpaw`，並啟用所有內建技能。

找到該路徑。因為 `.qwenpaw` 是隱藏資料夾：

- Windows 使用者需要從工作列開啟「檔案總管」，選擇「檢視 > 顯示」，然後勾選「隱藏的項目」以查看隱藏的檔案和資料夾。
- mac 使用者需要開啟 Finder，進入主資料夾後，同時按下 `Command + Shift + .`

將我們準備好的頻道配置檔 `custom_channels` 複製到 `.qwenpaw` 資料夾中。

將[人設資料夾中的檔案](assets/openclaw_guide/%E6%9B%BF%E6%8D%A2%E5%86%85%E5%AE%B9.zip)複製到 `.qwenpaw/workspaces/default` 中，並刪除 `BOOTSTRAP.md`。

然後在終端按 `CTRL+C` 結束 qwenpaw，再輸入 `qwenpaw app` 重新啟動。

接著按照圖片中的步驟啟用 Neko 頻道。

![啟用 Neko 頻道步驟圖 2](assets/openclaw_guide/image2.png)

## 基礎配置：模型設定

點擊模型，然後選擇 DashScope（根據你的 API Key 也可以選擇其他模型），點擊設定，輸入阿里雲百鍊 API Key 並儲存。

![啟用 Neko 頻道步驟圖 3](assets/openclaw_guide/image3.png)

儲存後回到聊天頁面，就能選擇已配置好的模型了。

回到 N.E.K.O 就能使用 openclaw 了。
