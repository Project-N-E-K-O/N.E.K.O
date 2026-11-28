import bindingErrors from '../model-binding-errors/zh-CN'
import type { ModelBindingsMessages } from './en-US'

const messages: ModelBindingsMessages = {
  bindingErrors,
  title: '模型 API 绑定',
  manage: '管理槽位与用量',
  description: '为插件声明的每个用途选择已保存的模型槽位，选择后立即保存。',
  ready: '所有必需的模型用途均已绑定',
  notReady: '部分必需的模型用途尚未完成绑定',
  readinessHint: '此状态仅表示必需用途的绑定是否完整，不验证上游可用性，也不控制插件启动。',
  boundSummary: '已绑定 {bound}/{total}',
  collapse: '收起',
  expand: '展开',
  required: '必需',
  optional: '可选',
  unbound: '未绑定',
  incompatible: '缺少所需能力',
  noSlots: '暂无模型槽位，请先在 Plugin API 中添加槽位，再刷新此处。',
  loadFailed: '无法加载模型绑定，请重试。',
  saveFailed: '绑定保存失败，当前仍显示原绑定。',
  capabilities: { text: '文本', image_input: '图片输入', tool_calling: '工具调用', streaming: '流式输出' },
  status: { bound: '已绑定', unbound: '尚未选择槽位', incompatible: '已绑定槽位不再满足此用途的能力要求' },
}

export default messages
