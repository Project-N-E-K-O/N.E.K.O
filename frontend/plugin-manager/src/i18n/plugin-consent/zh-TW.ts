import { withPermissions } from './shared'
import permissionLabels from '../plugin-permissions/zh-TW'

const messages = {
  title: '權限',
  dialogTitle: '確認外掛能力',
  introduction: '此外掛執行時會呼叫以下能力，確認後即可啟動。',
  analysisPending: '能力分析尚未完成，再次點擊啟動即可直接啟動：{names}',
  incomplete: '能力分析尚未完整完成，你仍可確認並啟動外掛。',
  upgraded: '升級後新增',
  pending: '待確認',
  decision: {
    approved: '已同意',
    denied: '已拒絕',
  },
  banner: {
    confirm: '發現 {n} 項能力待確認',
    upgradedConfirm: '升級後新增 {n} 項能力待確認',
    capabilitiesDesc:
      '外掛可能讀取你的對話或記憶、或向本體發送內容。確認後不再提示，升級出現新能力時會再次詢問。',
    incompleteTitle: '能力分析未完整完成',
    incompleteDesc: '分析結果可能不完整，你仍可同意並繼續使用。',
    deniedDesc: '自動啟動時會跳過此外掛，手動啟動時會再次詢問。',
    approvedAt: '{date} 確認',
    analyzingTitle: '正在分析外掛能力…',
    analyzingDesc: '分析完成後結果會自動顯示在這裡。',
  },
  approve: '同意',
  reject: '拒絕',
  approveAndStart: '同意並啟動',
  revoke: '撤回確認',
  revokeDecision: '撤回決定',
  evidenceToggle: '程式碼位置（{n}）',
  meta: {
    analyzed: '分析於 {date}',
    version: '版本 {version}',
    files: '{n} 個檔案',
  },
  noticesTitle: '有待確認的外掛能力',
  loading: '正在載入能力資訊…',
  loadFailed: '暫時無法載入能力資訊。',
  saveFailed: '確認儲存失敗，請重試。',
  unknown: '具體資料使用情況暫不明確。外掛可透過 SDK 讀取對話、記憶，或向本體發送內容。',
  noneFound: '未發現問題：程式碼掃描沒有發現用到任何權限，也沒有無法靜態確認的程式碼。',
  retry: '重新分析',
  findings: {
    usedTitle: '外掛用到了以下權限',
    usedBanner: '外掛用到了 {n} 項權限',
    declared: '作者宣告',
    detected: '程式碼中發現',
    blindSpotsTitle: '有 {n} 處無法靜態確認',
    blindSpotsDesc: '這些位置動態載入程式碼、使用反射或執行產生的程式碼，或者沒有被分析。不實際執行外掛，無法確定它們會做什麼。',
    unlocated: '外掛的一部分（沒有對應到具體檔案）',
    partialTag: '部分無法確認',
    partialTagHint: '部分程式碼無法靜態分析，詳見詳情頁。',
  },
  errors: {
    sourceUnknown: '無法確認此外掛目前的安裝來源，為保護你已授予的權限，本次替換已停止，外掛保持原樣。解除安裝此外掛後重新安裝即可恢復。',
    readFailed: '暫時無法讀取外掛權限記錄，沒有做任何變更。請稍後再試。',
    corrupt: '外掛權限記錄檔（plugin_permissions.json）已損壞，無法讀取，操作已停止。請將此檔案從 N.E.K.O 設定資料夾移走後重新啟動 N.E.K.O，外掛可能會重新詢問權限。',
    unsupportedVersion: '外掛權限記錄由較新版本的 N.E.K.O 儲存，目前版本無法讀取，操作已停止。請將 N.E.K.O 更新到最新版本後再試。',
    writeFailed: '無法儲存外掛權限記錄，沒有做任何變更。請檢查磁碟空間和 N.E.K.O 設定資料夾的寫入權限後再試。',
  },
  warnings: {
    budget_exceeded: '分析已達到工作預算。',
    source_too_large: '原始碼檔案超出分析大小預算。',
    source_unreadable: '部分原始碼無法分析。',
    analysis_failed: '背景分析未能完成。',
  },
  capabilities: {
    conversations_read: {
      title: '讀取對話',
      description: '可能讀取近期使用者發言、對話記錄，或監聽後續對話和回覆完成事件。',
    },
    memory_read: {
      title: '讀取記憶',
      description: '可能查詢長期記憶，或讀取角色相關記憶和画像資料。',
    },
    messages_push: {
      title: '向本體發送內容',
      description: '可能發送訊息、展示卡片或其他內容，或在本體回覆後附加內容。',
    },
  },
}

export default withPermissions(messages, permissionLabels)
