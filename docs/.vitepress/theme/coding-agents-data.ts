export type DocsLocale = 'en' | 'zh-CN' | 'ja'

export interface AgentTutorial {
  label: string
  url: string
  kind: 'docs' | 'video'
}

export interface AgentConfig {
  id: string
  label: string
  title: string
  downloadUrl?: string
  tutorials?: AgentTutorial[]
  openFolder: string
  allowActions: string
  contextNote: string
}

export interface OnboardingCopy {
  tabHeading: string
  tabHint: string
  downloadLabel: string
  tutorialsHeading: string
  openFolderHeading: string
  allowActionsHeading: string
  contextNoteHeading: string
  promptTitle: string
  promptDesc: string
  ideaLabel: string
  ideaPlaceholder: string
  copyPrompt: string
  copiedPrompt: string
  showPrompt: string
  hidePrompt: string
  agents: AgentConfig[]
}

export function normalizeDocsLocale(lang: string): DocsLocale {
  const lower = lang.toLowerCase()
  if (lower.startsWith('zh')) return 'zh-CN'
  if (lower.startsWith('ja')) return 'ja'
  return 'en'
}

export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
    if (typeof document !== 'undefined') {
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.setAttribute('readonly', 'true')
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      document.execCommand('copy')
      document.body.removeChild(textarea)
      return true
    }
  } catch {
    return false
  }
  return false
}

export function buildSetupPrompt(locale: DocsLocale, userIdea: string): string {
  const trimmed = userIdea.trim()

  if (locale === 'zh-CN') {
    const ideaLine = trimmed
      ? `我想做：${trimmed}`
      : '我还没想好做什么。请读完 AGENTS.md 后，给我 3 个适合新手的点子让我挑。'

    return `这个文件夹是一个 N.E.K.O 插件项目。请先阅读 AGENTS.md，并按里面的要求工作（如果没有 AGENTS.md，请告诉我先把 N.E.K.O 更新到最新版本）。

${ideaLine}

请先用大白话给我一个简短方案，等我确认后再动手。我不会用终端，需要运行的命令都由你自己执行，不要让我去运行。
做完后告诉我：在 N.E.K.O 里点哪里启动、重载插件，以及怎样试用效果。`
  }

  if (locale === 'ja') {
    const ideaLine = trimmed
      ? `作りたいもの：${trimmed}`
      : 'まだ作りたいものが決まっていません。AGENTS.md を読んだあと、初心者向けのアイデアを 3 つ提案してください。'

    return `このフォルダは N.E.K.O プラグインのプロジェクトです。まず AGENTS.md を読み、その指示に従って作業してください（AGENTS.md がない場合は、N.E.K.O を最新版に更新するよう私に伝えてください）。

${ideaLine}

まず初心者にも分かる短い案を出し、私が確認してから着手してください。私はターミナルを使えません。コマンドはすべてあなたが実行し、私に実行させないでください。
完成したら、N.E.K.O でプラグインを起動・リロードするために押すボタンと、試す方法を教えてください。`
  }

  const ideaLine = trimmed
    ? `I want to build: ${trimmed}`
    : "I haven't decided yet. After reading AGENTS.md, offer me 3 beginner-friendly ideas to pick from."

  return `This folder is a N.E.K.O plugin project. Please read AGENTS.md first and follow it (if there is no AGENTS.md, tell me to update N.E.K.O to the latest version instead).

${ideaLine}

Give me a short plain-English plan first and wait for my confirmation before acting. I cannot use a terminal — run any commands yourself and never ask me to run them.
When done, tell me where to click in N.E.K.O to start and reload the plugin, and how to try it out.`
}

const zhAgents: AgentConfig[] = [
  {
    id: 'dsh',
    label: 'DeepSeek Harness',
    title: 'DeepSeek Harness 桌面版',
    downloadUrl: 'https://github.com/deepseek-ai/deepseek-harness/releases',
    tutorials: [
      {
        label: '官方 GitHub 仓库（README 与桌面版指南）',
        url: 'https://github.com/deepseek-ai/deepseek-harness',
        kind: 'docs',
      },
    ],
    openFolder: '打开 DeepSeek Harness 桌面版，点「Choose workspace」，选择刚才新建的插件文件夹。',
    allowActions: 'AI 请求执行操作时点击「Approve / 允许」即可。',
    contextNote: 'dsh 会自动读取项目里的 AGENTS.md。',
  },
  {
    id: 'codex',
    label: 'Codex',
    title: 'OpenAI Codex',
    downloadUrl: 'https://openai.com/codex/',
    tutorials: [
      {
        label: '官方快速上手：3 步开始 Codex（英文）',
        url: 'https://chatgpt.com/features/codex-get-started/',
        kind: 'docs',
      },
      {
        label: 'OpenAI 帮助中心：Codex 使用指南（英文）',
        url: 'https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan',
        kind: 'docs',
      },
    ],
    openFolder: '打开 Codex 桌面应用并登录，选择插件文件夹作为工作目录。',
    allowActions: 'Codex 请求执行操作时点击「允许」。',
    contextNote: 'Codex 会自动读取项目里的 AGENTS.md。',
  },
  {
    id: 'trae',
    label: 'Trae',
    title: 'Trae',
    downloadUrl: 'https://www.trae.cn/',
    tutorials: [
      {
        label: '官方文档：快速开始 TRAE 桌面端',
        url: 'https://docs.trae.cn/ide_get-started-with-trae',
        kind: 'docs',
      },
      {
        label: '官方文档：Agent 窗口快速开始',
        url: 'https://docs.trae.cn/ide_get-started-with-the-agent-window',
        kind: 'docs',
      },
    ],
    openFolder: '在 Trae 中点「打开文件夹」，选择插件文件夹。',
    allowActions: '把右侧 AI 面板切到「Builder」或「SOLO」模式，请求执行时点「运行 / 允许」。',
    contextNote: 'Trae 会自动读取 AGENTS.md；若没有读到，把下方那段话发给它即可。',
  },
  {
    id: 'claude-code',
    label: 'Claude Code',
    title: 'Claude Code',
    downloadUrl: 'https://claude.ai/download',
    tutorials: [
      {
        label: '官方文档：桌面版快速上手（英文）',
        url: 'https://code.claude.com/docs/en/desktop-quickstart',
        kind: 'docs',
      },
      {
        label: '官方文档：Code 标签页完整指南（英文）',
        url: 'https://code.claude.com/docs/en/desktop',
        kind: 'docs',
      },
    ],
    openFolder: '打开 Claude 桌面应用并登录，切到「Code」标签页，选择插件文件夹。',
    allowActions: 'Claude 请求执行操作时点击允许。',
    contextNote: 'Claude Code 会读取项目里的 CLAUDE.md，它会自动指向 AGENTS.md。',
  },
  {
    id: 'cursor',
    label: 'Cursor',
    title: 'Cursor',
    downloadUrl: 'https://www.cursor.com/',
    tutorials: [
      {
        label: '官方 Quickstart（英文）',
        url: 'https://cursor.com/docs/get-started/quickstart',
        kind: 'docs',
      },
    ],
    openFolder: '在 Cursor 中点「Open Folder」，选择插件文件夹。',
    allowActions: '确认右侧聊天模式为「Agent」，请求执行时点允许。',
    contextNote: 'Cursor 会自动读取项目里的 AGENTS.md。',
  },
  {
    id: 'qoder',
    label: 'Qoder / 通义灵码',
    title: 'Qoder / 通义灵码',
    downloadUrl: 'https://qoder.com/',
    tutorials: [
      {
        label: '通义灵码官方：下载和安装教程',
        url: 'https://lingma.aliyun.com/lingma/download',
        kind: 'docs',
      },
      {
        label: '阿里云帮助中心：Qoder CN IDE 快速入门',
        url: 'https://help.aliyun.com/zh/lingma/lingma-ide-get-started',
        kind: 'docs',
      },
    ],
    openFolder: '在 Qoder IDE（或装了通义灵码的 VS Code / PyCharm）中打开插件文件夹。',
    allowActions: '切到「智能体 / Agent」模式，允许它读写文件并执行操作。',
    contextNote: 'Qoder 会自动读取 AGENTS.md；若没有读到，把下方那段话发给它即可。',
  },
  {
    id: 'codebuddy',
    label: 'CodeBuddy',
    title: '腾讯 CodeBuddy',
    downloadUrl: 'https://copilot.tencent.com/',
    tutorials: [
      {
        label: '官方中文文档：安装和登录',
        url: 'https://www.codebuddy.cn/docs/ide/Getting-Started/Installation',
        kind: 'docs',
      },
      {
        label: '官方中文文档：快速开始',
        url: 'https://www.codebuddy.cn/docs/ide/Getting-Started/Quickstart',
        kind: 'docs',
      },
    ],
    openFolder: '在 CodeBuddy IDE（或 CodeBuddy 插件）中打开插件文件夹。',
    allowActions: '打开侧边栏「Craft 智能体」面板，请求执行时点允许。',
    contextNote: 'CodeBuddy 会自动读取 AGENTS.md；若没有读到，把下方那段话发给它即可。',
  },
  {
    id: 'vscode',
    label: 'VS Code (Cline / Copilot)',
    title: 'VS Code + Cline / Copilot',
    downloadUrl: 'https://code.visualstudio.com/',
    tutorials: [
      {
        label: 'Cline 官方上手指南（英文）',
        url: 'https://docs.cline.bot/getting-started/for-new-coders',
        kind: 'docs',
      },
      {
        label: 'VS Code 官方文档：Copilot Agent 模式（英文）',
        url: 'https://code.visualstudio.com/docs/copilot/chat/chat-agent-mode',
        kind: 'docs',
      },
    ],
    openFolder: '在 VS Code 中打开插件文件夹。',
    allowActions: '把 Cline 设为「Act」模式，或把 Copilot Chat 切为「Agent」模式；请求执行时点允许。',
    contextNote: 'Cline 和 Copilot 都会读取 AGENTS.md；若没有读到，把下方那段话发给它即可。',
  },
  {
    id: 'other',
    label: '其他 Agent',
    title: '其他 Coding Agent（Windsurf / OpenCode / Aider 等）',
    openFolder: '在你的 AI 编程工具中打开插件文件夹。',
    allowActions: '允许它读写文件并执行操作（例如切换到智能体 / Agent 模式）。',
    contextNote: '如果你的工具不会自动读取 AGENTS.md，把下面这段话原样发给它即可。',
  },
]

const enAgents: AgentConfig[] = [
  {
    id: 'codex',
    label: 'Codex',
    title: 'OpenAI Codex',
    downloadUrl: 'https://openai.com/codex/',
    tutorials: [
      {
        label: 'Official: Get started with Codex',
        url: 'https://chatgpt.com/features/codex-get-started/',
        kind: 'docs',
      },
      {
        label: 'OpenAI Help: Using Codex with your ChatGPT plan',
        url: 'https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan',
        kind: 'docs',
      },
    ],
    openFolder: 'Open the Codex desktop app, sign in, and choose the new plugin folder as the workspace.',
    allowActions: 'Click Allow whenever Codex asks to run something.',
    contextNote: 'Codex reads AGENTS.md in the project automatically.',
  },
  {
    id: 'dsh',
    label: 'DeepSeek Harness',
    title: 'DeepSeek Harness Desktop',
    downloadUrl: 'https://github.com/deepseek-ai/deepseek-harness/releases',
    tutorials: [
      {
        label: 'Official GitHub repo (README & desktop app guide)',
        url: 'https://github.com/deepseek-ai/deepseek-harness',
        kind: 'docs',
      },
    ],
    openFolder: 'Open DeepSeek Harness Desktop, click Choose workspace, and select the new plugin folder.',
    allowActions: 'Click Approve when the AI asks to run something.',
    contextNote: 'DeepSeek Harness reads AGENTS.md in the project automatically.',
  },
  {
    id: 'claude-code',
    label: 'Claude Code',
    title: 'Claude Code',
    downloadUrl: 'https://claude.ai/download',
    tutorials: [
      {
        label: 'Get started with the Claude desktop app',
        url: 'https://code.claude.com/docs/en/desktop-quickstart',
        kind: 'docs',
      },
      {
        label: 'Claude Code Desktop reference',
        url: 'https://code.claude.com/docs/en/desktop',
        kind: 'docs',
      },
    ],
    openFolder: 'Open the Claude desktop app, sign in, switch to the Code tab, and select the plugin folder.',
    allowActions: 'Click Allow when Claude asks to run something.',
    contextNote: 'Claude Code reads CLAUDE.md in the project, which points to AGENTS.md.',
  },
  {
    id: 'cursor',
    label: 'Cursor',
    title: 'Cursor',
    downloadUrl: 'https://www.cursor.com/',
    tutorials: [
      {
        label: 'Official Cursor Quickstart',
        url: 'https://cursor.com/docs/get-started/quickstart',
        kind: 'docs',
      },
    ],
    openFolder: 'In Cursor, click Open Folder and select the plugin folder.',
    allowActions: 'Make sure the chat is in Agent mode, then click Allow when it asks to run something.',
    contextNote: 'Cursor reads AGENTS.md in the project automatically.',
  },
  {
    id: 'trae',
    label: 'Trae',
    title: 'Trae',
    downloadUrl: 'https://www.trae.ai/',
    tutorials: [
      {
        label: 'Official docs: Get started with TRAE Desktop',
        url: 'https://docs.trae.ai/ide/get-started-with-trae-desktop',
        kind: 'docs',
      },
    ],
    openFolder: 'In Trae, open the plugin folder.',
    allowActions: 'Switch the AI panel to Builder or SOLO mode and click Allow when asked to run something.',
    contextNote: 'Trae reads AGENTS.md automatically; if it does not, just send the message below.',
  },
  {
    id: 'vscode',
    label: 'VS Code (Cline / Copilot)',
    title: 'VS Code + Cline / Copilot',
    downloadUrl: 'https://code.visualstudio.com/',
    tutorials: [
      {
        label: 'Cline docs: installing and your first session',
        url: 'https://docs.cline.bot/getting-started/for-new-coders',
        kind: 'docs',
      },
      {
        label: 'VS Code docs: Copilot agent mode',
        url: 'https://code.visualstudio.com/docs/copilot/chat/chat-agent-mode',
        kind: 'docs',
      },
    ],
    openFolder: 'Open the plugin folder in VS Code.',
    allowActions: 'Set Cline to Act mode, or switch Copilot Chat to Agent mode; click Allow when it asks to run something.',
    contextNote: 'Cline and Copilot read AGENTS.md; if yours does not, just send the message below.',
  },
  {
    id: 'qoder',
    label: 'Qoder',
    title: 'Qoder / Tongyi Lingma',
    downloadUrl: 'https://qoder.com/',
    tutorials: [
      {
        label: 'Official docs: Qoder Quick Start',
        url: 'https://docs.qoder.com/product-series/quick-start',
        kind: 'docs',
      },
    ],
    openFolder: 'Open the plugin folder in Qoder IDE (or in VS Code / PyCharm with the Lingma extension).',
    allowActions: 'Switch the assistant to Agent / Quest mode and allow it to edit files and run actions.',
    contextNote: 'Qoder reads AGENTS.md automatically; if it does not, just send the message below.',
  },
  {
    id: 'codebuddy',
    label: 'CodeBuddy',
    title: 'CodeBuddy',
    downloadUrl: 'https://www.codebuddy.ai/',
    tutorials: [
      {
        label: 'Official docs: Installation & login',
        url: 'https://www.codebuddy.ai/docs/ide/Getting-Started/Installation',
        kind: 'docs',
      },
      {
        label: 'Official docs: Quick Start',
        url: 'https://www.codebuddy.ai/docs/ide/Getting-Started/Quickstart',
        kind: 'docs',
      },
    ],
    openFolder: 'Open the plugin folder in CodeBuddy IDE (or the CodeBuddy extension).',
    allowActions: 'Switch to the Craft agent panel and click Allow when it asks to run something.',
    contextNote: 'CodeBuddy reads AGENTS.md automatically; if it does not, just send the message below.',
  },
  {
    id: 'other',
    label: 'Other Agents',
    title: 'Other Coding Agents (Windsurf, OpenCode, Aider, etc.)',
    openFolder: 'Open the plugin folder in your AI coding tool.',
    allowActions: 'Allow it to edit files and run actions (for example, switch to its agent mode).',
    contextNote: 'If your tool does not read AGENTS.md automatically, just send it the message below.',
  },
]

const jaAgents: AgentConfig[] = [
  {
    id: 'codex',
    label: 'Codex',
    title: 'OpenAI Codex',
    downloadUrl: 'https://openai.com/codex/',
    tutorials: [
      {
        label: '公式：Codex をはじめる（英語）',
        url: 'https://chatgpt.com/features/codex-get-started/',
        kind: 'docs',
      },
      {
        label: 'OpenAI ヘルプ：Codex の使い方（英語）',
        url: 'https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan',
        kind: 'docs',
      },
    ],
    openFolder: 'Codex デスクトップアプリを開いてログインし、新しいプラグインフォルダをワークスペースにします。',
    allowActions: 'AI が操作の実行を求めてきたら「許可」を押します。',
    contextNote: 'Codex はプロジェクト内の AGENTS.md を自動で読みます。',
  },
  {
    id: 'dsh',
    label: 'DeepSeek Harness',
    title: 'DeepSeek Harness デスクトップ版',
    downloadUrl: 'https://github.com/deepseek-ai/deepseek-harness/releases',
    tutorials: [
      {
        label: '公式 GitHub リポジトリ（README とデスクトップ版ガイド）',
        url: 'https://github.com/deepseek-ai/deepseek-harness',
        kind: 'docs',
      },
    ],
    openFolder: 'DeepSeek Harness デスクトップ版を開き、「Choose workspace」で新しいプラグインフォルダを選びます。',
    allowActions: 'AI が操作の実行を求めてきたら「Approve」を押します。',
    contextNote: 'dsh はプロジェクト内の AGENTS.md を自動で読みます。',
  },
  {
    id: 'claude-code',
    label: 'Claude Code',
    title: 'Claude Code',
    downloadUrl: 'https://claude.ai/download',
    tutorials: [
      {
        label: '公式：デスクトップアプリを始める',
        url: 'https://code.claude.com/docs/ja/desktop-quickstart',
        kind: 'docs',
      },
      {
        label: '公式：Code タブ完全ガイド',
        url: 'https://code.claude.com/docs/ja/desktop',
        kind: 'docs',
      },
    ],
    openFolder: 'Claude デスクトップアプリを開いてログインし、「Code」タブでプラグインフォルダを選びます。',
    allowActions: 'AI が操作の実行を求めてきたら許可します。',
    contextNote: 'Claude Code はプロジェクト内の CLAUDE.md を読み、それが AGENTS.md を指します。',
  },
  {
    id: 'cursor',
    label: 'Cursor',
    title: 'Cursor',
    downloadUrl: 'https://www.cursor.com/',
    tutorials: [
      {
        label: '公式 Quickstart（英語）',
        url: 'https://cursor.com/docs/get-started/quickstart',
        kind: 'docs',
      },
    ],
    openFolder: 'Cursor で「Open Folder」からプラグインフォルダを開きます。',
    allowActions: 'チャットが「Agent」モードになっていることを確認し、実行を求められたら許可します。',
    contextNote: 'Cursor はプロジェクト内の AGENTS.md を自動で読みます。',
  },
  {
    id: 'trae',
    label: 'Trae',
    title: 'Trae',
    downloadUrl: 'https://www.trae.ai/',
    tutorials: [
      {
        label: '公式ドキュメント：TRAE Desktop をはじめる（英語）',
        url: 'https://docs.trae.ai/ide/get-started-with-trae-desktop',
        kind: 'docs',
      },
    ],
    openFolder: 'Trae でプラグインフォルダを開きます。',
    allowActions: '右側の AI パネルを「Builder」または「SOLO」モードにし、実行を求められたら許可します。',
    contextNote: 'Trae は AGENTS.md を自動で読みます。読まない場合は下のメッセージを送ってください。',
  },
  {
    id: 'vscode',
    label: 'VS Code (Cline / Copilot)',
    title: 'VS Code + Cline / Copilot',
    downloadUrl: 'https://code.visualstudio.com/',
    tutorials: [
      {
        label: 'Cline 公式ガイド（英語）',
        url: 'https://docs.cline.bot/getting-started/for-new-coders',
        kind: 'docs',
      },
      {
        label: 'VS Code 公式ドキュメント：Copilot エージェントモード（英語）',
        url: 'https://code.visualstudio.com/docs/copilot/chat/chat-agent-mode',
        kind: 'docs',
      },
    ],
    openFolder: 'VS Code でプラグインフォルダを開きます。',
    allowActions: 'Cline を「Act」モード（または Copilot Chat を「Agent」モード）にし、実行を求められたら許可します。',
    contextNote: 'Cline と Copilot は AGENTS.md を読みます。読まない場合は下のメッセージを送ってください。',
  },
  {
    id: 'qoder',
    label: 'Qoder',
    title: 'Qoder',
    downloadUrl: 'https://qoder.com/',
    tutorials: [
      {
        label: '公式ドキュメント：Qoder Quick Start（英語）',
        url: 'https://docs.qoder.com/product-series/quick-start',
        kind: 'docs',
      },
    ],
    openFolder: 'Qoder でプラグインフォルダを開きます。',
    allowActions: 'アシスタントを Agent / Quest モードにし、ファイル編集と操作の実行を許可します。',
    contextNote: 'Qoder は AGENTS.md を自動で読みます。読まない場合は下のメッセージを送ってください。',
  },
  {
    id: 'codebuddy',
    label: 'CodeBuddy',
    title: 'CodeBuddy',
    downloadUrl: 'https://www.codebuddy.ai/',
    tutorials: [
      {
        label: '公式ドキュメント：インストールとログイン（英語）',
        url: 'https://www.codebuddy.ai/docs/ide/Getting-Started/Installation',
        kind: 'docs',
      },
      {
        label: '公式ドキュメント：Quick Start（英語）',
        url: 'https://www.codebuddy.ai/docs/ide/Getting-Started/Quickstart',
        kind: 'docs',
      },
    ],
    openFolder: 'CodeBuddy でプラグインフォルダを開きます。',
    allowActions: '「Craft」エージェントパネルを開き、実行を求められたら許可します。',
    contextNote: 'CodeBuddy は AGENTS.md を自動で読みます。読まない場合は下のメッセージを送ってください。',
  },
  {
    id: 'other',
    label: 'その他の Agent',
    title: 'その他の Coding Agent（Windsurf / OpenCode / Aider など）',
    openFolder: 'お使いの AI コーディングツールでプラグインフォルダを開きます。',
    allowActions: 'ファイル編集と操作の実行を許可します（Agent モードなどに切り替えます）。',
    contextNote: '使っているツールが AGENTS.md を自動で読まない場合は、下のメッセージをそのまま送ってください。',
  },
]

export const ONBOARDING_COPY: Record<DocsLocale, OnboardingCopy> = {
  'zh-CN': {
    tabHeading: '选择你使用的 Coding Agent',
    tabHint: '不局限于以下工具，任意能读写文件并执行操作的 AI 编程工具均可',
    downloadLabel: '官网下载',
    tutorialsHeading: '官方上手教程',
    openFolderHeading: '怎样打开插件文件夹',
    allowActionsHeading: '允许 AI 执行操作',
    contextNoteHeading: '小提示',
    promptTitle: '复制这段话发给 AI',
    promptDesc: '把这段话粘贴到 Agent 的对话框里发送即可。',
    ideaLabel: '可选：先写一句你想做的插件（留空也行，AI 会给你出点子）',
    ideaPlaceholder: '例如：做一个提醒喝水的插件，猫娘会定时提醒我',
    copyPrompt: '一键复制',
    copiedPrompt: '已复制',
    showPrompt: '查看完整内容',
    hidePrompt: '收起完整内容',
    agents: zhAgents,
  },
  en: {
    tabHeading: 'Choose your coding agent',
    tabHint: 'Any AI coding tool that can edit files and run actions on your computer works',
    downloadLabel: 'Official site',
    tutorialsHeading: 'Official tutorials',
    openFolderHeading: 'How to open the plugin folder',
    allowActionsHeading: 'Let the AI take actions',
    contextNoteHeading: 'Good to know',
    promptTitle: 'Copy this message and send it to the AI',
    promptDesc: 'Paste it into your agent’s chat box and send.',
    ideaLabel: 'Optional: describe your plugin idea in one line (or leave blank and let the AI suggest ideas)',
    ideaPlaceholder: 'e.g. a plugin where the catgirl reminds me to drink water',
    copyPrompt: 'Copy',
    copiedPrompt: 'Copied',
    showPrompt: 'Preview full text',
    hidePrompt: 'Hide full text',
    agents: enAgents,
  },
  ja: {
    tabHeading: '使用する Coding Agent を選択',
    tabHint: 'この一覧以外でも、ファイル編集と操作の実行ができる AI ツールなら使えます',
    downloadLabel: '公式サイト',
    tutorialsHeading: '公式チュートリアル',
    openFolderHeading: 'プラグインフォルダの開き方',
    allowActionsHeading: 'AI に操作を許可する',
    contextNoteHeading: '補足',
    promptTitle: 'この文をコピーして AI に送信',
    promptDesc: 'Agent のチャット欄に貼り付けて送るだけです。',
    ideaLabel: '任意：作りたいプラグインを 1 文で入力（空欄なら AI が提案します）',
    ideaPlaceholder: '例：猫娘が水分補給を教えてくれるプラグイン',
    copyPrompt: 'コピー',
    copiedPrompt: 'コピーしました',
    showPrompt: '全文を表示',
    hidePrompt: '全文を閉じる',
    agents: jaAgents,
  },
}
