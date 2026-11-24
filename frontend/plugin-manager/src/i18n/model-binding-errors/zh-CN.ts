import type { ModelBindingErrorMessages } from './en-US'

const messages: ModelBindingErrorMessages = {
  conflict: '绑定已被其他操作更改，请刷新并确认后重试。',
  unknown: '操作结果尚未确认，请刷新确认后再进行修改。',
  failed: '操作未能完成，请确认当前绑定后重试。',
  version: '请刷新页面后再修改绑定。',
}

export default messages
