import bindingErrors from '../model-binding-errors/zh-TW'
import type { ModelBindingsMessages } from './en-US'

const messages: ModelBindingsMessages = {
  bindingErrors,
  title: '模型 API 綁定',
  manage: '管理槽位與用量',
  description: '為外掛宣告的每個用途選擇已儲存的模型槽位，選擇後立即儲存。',
  ready: '所有必要的模型用途均已綁定',
  notReady: '部分必要的模型用途尚未完成綁定',
  readinessHint: '此狀態僅表示必要用途的綁定是否完整，不驗證上游可用性，也不控制外掛啟動。',
  boundSummary: '已綁定 {bound}/{total}',
  collapse: '收合',
  expand: '展開',
  required: '必要',
  optional: '選用',
  unbound: '未綁定',
  incompatible: '缺少所需能力',
  noSlots: '目前沒有模型槽位，請先在 Plugin API 中新增槽位，再重新整理此處。',
  loadFailed: '無法載入模型綁定，請重試。',
  saveFailed: '綁定儲存失敗，目前仍顯示原綁定。',
  capabilities: { text: '文字', image_input: '圖片輸入', tool_calling: '工具呼叫', streaming: '串流輸出' },
  status: { bound: '已綁定', unbound: '尚未選擇槽位', incompatible: '已綁定槽位不再符合此用途的能力需求' },
}

export default messages
