import labels from '../plugin-permissions/zh-CN'
import { withPermissionLabels } from '../plugin-permissions/shared'
import type { PluginConsentMessages } from './en-US'

const messages: PluginConsentMessages = {
  title: '权限',
  dialogTitle: '确认插件能力',
  introduction: '该插件运行时会调用以下能力，确认后即可启动。',
  analysisPending: '能力分析尚未完成，再次点击启动即可直接启动：{names}',
  incomplete: '能力分析尚未完整完成，你仍可确认并启动插件。',
  upgraded: '升级后新增',
  pending: '待确认',
  decision: {
    approved: '已同意',
    denied: '已拒绝',
  },
  banner: {
    confirm: '发现 {n} 项能力待确认',
    upgradedConfirm: '升级后新增 {n} 项能力待确认',
    capabilitiesDesc:
      '插件可能读取你的对话或记忆、或向本体发送内容。确认后不再提示，升级出现新能力时会再次询问。',
    incompleteTitle: '能力分析未完整完成',
    incompleteDesc: '分析结果可能不完整，你仍可同意并继续使用。',
    deniedDesc: '自启时会跳过此插件，手动启动时会再次询问。',
    approvedAt: '{date} 确认',
    analyzingTitle: '正在分析插件能力…',
    analyzingDesc: '分析完成后结果会自动显示在这里。',
  },
  approve: '同意',
  reject: '拒绝',
  approveAndStart: '同意并启动',
  revoke: '撤回确认',
  revokeDecision: '撤回决定',
  evidenceToggle: '代码位置（{n}）',
  meta: {
    analyzed: '分析于 {date}',
    version: '版本 {version}',
    files: '{n} 个文件',
  },
  noticesTitle: '有待确认的插件能力',
  loading: '正在加载能力信息…',
  loadFailed: '暂时无法加载能力信息。',
  saveFailed: '确认保存失败，请重试。',
  unknown: '具体数据使用情况暂不明确。插件可通过 SDK 读取对话、记忆，或向本体发送内容。',
  noneFound: '未发现问题：代码扫描没有发现用到任何权限，也没有无法静态确认的代码。',
  retry: '重新分析',
  findings: {
    usedTitle: '插件用到了以下权限',
    usedBanner: '插件用到了 {n} 项权限',
    declared: '作者声明',
    detected: '代码中发现',
    blindSpotsTitle: '有 {n} 处无法静态确认',
    blindSpotsDesc: '这些位置动态加载代码、使用反射或执行生成的代码，或者没有被分析。不实际运行插件，无法确定它们会做什么。',
    unlocated: '插件的一部分（没有对应到具体文件）',
    partialTag: '部分无法确认',
    partialTagHint: '部分代码无法静态分析，详见详情页。',
  },
  errors: {
    sourceUnknown: '无法确认这个插件当前的安装来源，为保护你已授予的权限，本次替换已停止，插件保持原样。卸载这个插件后重新安装即可恢复。',
    readFailed: '暂时无法读取插件权限记录，没有做任何改动。请稍后重试。',
    corrupt: '插件权限记录文件（plugin_permissions.json）已损坏，无法读取，操作已停止。请把这个文件从 N.E.K.O 配置目录中移走后重启 N.E.K.O，插件可能会重新询问权限。',
    unsupportedVersion: '插件权限记录由更新版本的 N.E.K.O 保存，当前版本无法读取，操作已停止。请把 N.E.K.O 更新到最新版本后重试。',
    writeFailed: '无法保存插件权限记录，没有做任何改动。请检查磁盘空间和 N.E.K.O 配置目录的写入权限后重试。',
  },
  warnings: {
    budget_exceeded: '分析已达到工作预算。',
    source_too_large: '源码文件超出分析大小预算。',
    source_unreadable: '部分源码无法分析。',
    analysis_failed: '后台分析未能完成。',
  },
  capabilities: {
    conversations_read: {
      title: '读取对话',
      description: '可能读取近期用户发言、对话记录，或监听后续对话和回复完成事件。',
    },
    memory_read: {
      title: '读取记忆',
      description: '可能查询长期记忆，或读取角色相关记忆和画像资料。',
    },
    messages_push: {
      title: '向本体发送内容',
      description: '可能发送消息、展示卡片或其他内容，或在本体回复后附加内容。',
    },
  },
}

export default { ...messages, capabilities: withPermissionLabels(messages.capabilities, labels) }
