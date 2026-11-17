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

export interface RouteCard {
  id: 'A' | 'B'
  title: string
  short: string
  badge?: string
  points: string[]
}

export interface OnboardingCopy {
  routesHeading: string
  routeCards: RouteCard[]
  routeStatusText: string
  routeStatusLink: string
  routeCurrentTag: string
  routeSwitchText: string
  tabHeading: string
  tabHint: string
  downloadLabel: string
  tutorialsHeading: string
  openFolderHeading: string
  /** Fills `{folder}` in AgentConfig.openFolder for each route. */
  folderRouteA: string
  folderRouteB: string
  allowActionsHeading: string
  contextNoteHeading: string
  /** Route A starts from an empty folder, so the per-agent AGENTS.md note does not apply yet. */
  contextNoteRouteA: string
  promptTitle: string
  promptDesc: string
  routeALabel: string
  routeBLabel: string
  ideaLabel: string
  ideaPlaceholder: string
  copyPrompt: string
  copiedPrompt: string
  copyFailed: string
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
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // Permission denied, page not focused, iframe without clipboard-write:
      // fall through to the legacy path instead of giving up.
    }
  }
  if (typeof document === 'undefined') return false
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', 'true')
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  try {
    textarea.select()
    // execCommand reports an unsupported / disabled copy by returning false.
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    document.body.removeChild(textarea)
  }
}

export function buildSetupPrompt(
  locale: DocsLocale,
  userIdea: string,
  route: 'A' | 'B',
): string {
  const trimmed = userIdea.trim()

  if (locale === 'zh-CN') {
    if (route === 'A') {
      const ideaStep = trimmed
        ? `5. 我想做：${trimmed}
   先用大白话给我一个简短方案，等我确认后再写代码。写完自己跑检查，修好所有错误。`
        : '5. 我还没想好做什么。读完 AGENTS.md 后，给我 3 个适合新手的点子让我挑，等我选好再写方案。'

      return `我想用源码版 N.E.K.O 做一个插件。我不会用终端：所有命令都由你自己执行，不要让我去运行；需要我做的只有在你的界面里点「允许」，或在浏览器里点按钮。耗时的步骤先告诉我大概要等多久。

请按顺序做：
1. 准备工具：检查 git、uv、Node.js，缺什么就自己装。下载慢或失败时自己换国内镜像（GitHub、PyPI、npm），不要来问我。
2. 准备 N.E.K.O 源码：在当前文件夹里按仓库 README 中从源码运行的步骤完成（部分克隆 https://github.com/Project-N-E-K-O/N.E.K.O 、uv sync、构建前端）。如果已有 N.E.K.O 文件夹就直接复用，不要删除或覆盖。
3. 用独立的开发实例启动：按仓库 docs/contributing/developer-notes.md 里「Two backends on one machine」的做法，给它单独的数据目录、锁目录和端口，不要动我电脑上已安装的 N.E.K.O 的数据；同时设置 NEKO_PLUGIN_HOT_RELOAD=true，在后台运行 launcher.py，然后把网页地址告诉我。第一次打开如果需要配置模型 API，告诉我在网页的哪里填。
4. 创建插件：在 N.E.K.O 仓库根目录运行 uv run neko-plugin init <插件ID> --name "<插件名>"，之后严格按插件目录里的 AGENTS.md 工作。
${ideaStep}
6. 告诉我在网页的插件管理里点哪里启动和试用。以后我对你说「帮我启动 N.E.K.O」，你就按第 3 步的方式重新启动。`
    }

    const ideaLine = trimmed
      ? `我想做：${trimmed}
先用大白话给我一个简短方案，等我确认后再动手。`
      : '我还没想好做什么。读完 AGENTS.md 后，给我 3 个适合新手的点子让我挑。'

    return `这个文件夹是我在 N.E.K.O「插件制作工作台」里新建的插件项目，请先阅读 AGENTS.md 并按它工作。
我用的是 Steam / 桌面版，电脑上没有 N.E.K.O 源码，所以 AGENTS.md 里需要源码的检查命令跑不了：写完后请仔细自查代码，然后告诉我在插件页点「启动」或「重载」；如果出错，我会把报错原样发给你。
我不会用终端，不要让我运行任何命令。

${ideaLine}`
  }

  if (locale === 'ja') {
    if (route === 'A') {
      const ideaStep = trimmed
        ? `5. 作りたいもの：${trimmed}
   まず初心者にも分かる短い案を出し、私が確認してからコードを書いてください。書き終わったら自分でチェックを実行し、エラーをすべて直してください。`
        : '5. まだ作りたいものが決まっていません。AGENTS.md を読んだあと、初心者向けのアイデアを 3 つ提案し、私が選んでから案を出してください。'

      return `ソース版 N.E.K.O でプラグインを作りたいです。私はターミナルを使えません。コマンドはすべてあなたが実行し、私に実行させないでください。私がするのは、あなたの画面で「許可」を押すことと、ブラウザでボタンを押すことだけです。時間のかかる手順は、先にだいたいの所要時間を教えてください。

順番にお願いします：
1. ツールの準備：git、uv、Node.js を確認し、足りないものは自分でインストールしてください。ダウンロードが遅い・失敗するときは自分でミラー（GitHub、PyPI、npm）に切り替え、私に聞かないでください。
2. N.E.K.O ソースの準備：現在のフォルダで、リポジトリ README のソースから実行する手順を完了させてください（部分クローン https://github.com/Project-N-E-K-O/N.E.K.O 、uv sync、フロントエンドのビルド）。すでに N.E.K.O フォルダがあれば削除・上書きせず再利用してください。
3. 独立した開発インスタンスで起動：リポジトリの docs/contributing/developer-notes.md の「Two backends on one machine」に従い、専用のデータディレクトリ・ロックディレクトリ・ポートを使い、私の PC にインストール済みの N.E.K.O のデータに触れないでください。NEKO_PLUGIN_HOT_RELOAD=true を設定し、launcher.py をバックグラウンドで実行して、Web ページの URL を教えてください。初回にモデル API の設定が必要なら、Web ページのどこに入力するか教えてください。
4. プラグインの作成：N.E.K.O リポジトリのルートで uv run neko-plugin init <プラグインID> --name "<プラグイン名>" を実行し、その後はプラグイン内の AGENTS.md に厳密に従って作業してください。
${ideaStep}
6. Web ページのプラグイン管理で起動・試用のために押すべきボタンを教えてください。今後「N.E.K.O を起動して」と言ったら、手順 3 と同じ方法で再起動してください。`
    }

    const ideaLine = trimmed
      ? `作りたいもの：${trimmed}
まず初心者にも分かる短い案を出し、私が確認してから着手してください。`
      : 'まだ作りたいものが決まっていません。AGENTS.md を読んだあと、初心者向けのアイデアを 3 つ提案してください。'

    return `このフォルダは、N.E.K.O の「プラグイン制作」で新規作成したプラグインプロジェクトです。まず AGENTS.md を読み、それに従って作業してください。
私は Steam / デスクトップ版を使っていて、N.E.K.O のソースがありません。AGENTS.md にあるソース版用のチェックコマンドは実行できないので、書き終わったら自分でコードを入念に確認し、プラグイン画面で押すべき「起動」「リロード」を教えてください。エラーが出たら、そのまま貼って送ります。
私はターミナルを使えません。私にコマンドを実行させないでください。

${ideaLine}`
  }

  if (route === 'A') {
    const ideaStep = trimmed
      ? `5. I want to build: ${trimmed}
   First give me a short plain-language plan and wait for my confirmation before writing code. After writing, run the checks yourself and fix every error.`
      : '5. I have not decided yet. After reading AGENTS.md, offer me 3 beginner-friendly ideas, and wait for my pick before writing the plan.'

    return `I want to build a plugin for the source version of N.E.K.O. I cannot use a terminal: run every command yourself and never ask me to run one. The only things I should do are click "Allow" in your interface or click buttons in the browser. For slow steps, tell me roughly how long they will take.

Please do this in order:
1. Prepare the tools: check git, uv, and Node.js, and install whatever is missing yourself. If downloads are slow or fail, switch to a mirror yourself (GitHub, PyPI, npm) — do not ask me.
2. Get the N.E.K.O source: inside the current folder, follow the "run from source" steps in the repo README (partial clone https://github.com/Project-N-E-K-O/N.E.K.O , uv sync, build the frontend). If a N.E.K.O folder already exists, reuse it — do not delete or overwrite it.
3. Launch an isolated dev instance: follow "Two backends on one machine" in docs/contributing/developer-notes.md — separate data directory, lock directory, and port; do not touch the data of my installed N.E.K.O. Set NEKO_PLUGIN_HOT_RELOAD=true, run launcher.py in the background, then give me the web page URL. If it asks to configure a model API key the first time, tell me where in the web UI to enter it.
4. Create the plugin: run uv run neko-plugin init <plugin id> --name "<plugin name>" in the N.E.K.O repo root, then strictly follow the AGENTS.md inside the plugin directory.
${ideaStep}
6. Tell me where to click in the plugin manager on the web page to start and try it. Later, when I say "start N.E.K.O for me", restart it the same way as step 3.`
  }

  const ideaLine = trimmed
    ? `I want to build: ${trimmed}
First give me a short plain-language plan and wait for my confirmation before acting.`
    : "I haven't decided yet. After reading AGENTS.md, offer me 3 beginner-friendly ideas to pick from."

  return `This folder is a plugin project I created in N.E.K.O "Plugin studio". Please read AGENTS.md first and work by it.
I use the Steam / desktop build and have no N.E.K.O source checkout, so the source-based check commands in AGENTS.md will not work: after writing the code, review it carefully yourself, then tell me to click "Start" or "Reload" on the plugin page. If something goes wrong, I will paste the error back to you.
I cannot use a terminal — never ask me to run any command.

${ideaLine}`
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
    openFolder: '打开 DeepSeek Harness 桌面版，点「Choose workspace」，选择{folder}。',
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
    openFolder: '打开 Codex 桌面应用并登录，选择{folder}作为工作目录。',
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
    openFolder: '在 Trae 中点「打开文件夹」，选择{folder}。',
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
    openFolder: '打开 Claude 桌面应用并登录，切到「Code」标签页，选择{folder}。',
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
    openFolder: '在 Cursor 中点「Open Folder」，选择{folder}。',
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
    openFolder: '在 Qoder IDE（或装了通义灵码的 VS Code / PyCharm）中打开{folder}。',
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
    openFolder: '在 CodeBuddy IDE（或 CodeBuddy 插件）中打开{folder}。',
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
    openFolder: '在 VS Code 中打开{folder}。',
    allowActions: '把 Cline 设为「Act」模式，或把 Copilot Chat 切为「Agent」模式；请求执行时点允许。',
    contextNote: 'Cline 和 Copilot 都会读取 AGENTS.md；若没有读到，把下方那段话发给它即可。',
  },
  {
    id: 'other',
    label: '其他 Agent',
    title: '其他 Coding Agent（Windsurf / OpenCode / Aider 等）',
    openFolder: '在你的 AI 编程工具中打开{folder}。',
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
    openFolder: 'Open the Codex desktop app, sign in, and choose {folder} as the workspace.',
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
    openFolder: 'Open DeepSeek Harness Desktop, click Choose workspace, and select {folder}.',
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
    openFolder: 'Open the Claude desktop app, sign in, switch to the Code tab, and select {folder}.',
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
    openFolder: 'In Cursor, click Open Folder and select {folder}.',
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
    openFolder: 'In Trae, open {folder}.',
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
    openFolder: 'Open {folder} in VS Code.',
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
    openFolder: 'Open {folder} in Qoder IDE (or in VS Code / PyCharm with the Lingma extension).',
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
    openFolder: 'Open {folder} in CodeBuddy IDE (or the CodeBuddy extension).',
    allowActions: 'Switch to the Craft agent panel and click Allow when it asks to run something.',
    contextNote: 'CodeBuddy reads AGENTS.md automatically; if it does not, just send the message below.',
  },
  {
    id: 'other',
    label: 'Other Agents',
    title: 'Other Coding Agents (Windsurf, OpenCode, Aider, etc.)',
    openFolder: 'Open {folder} in your AI coding tool.',
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
    openFolder: 'Codex デスクトップアプリを開いてログインし、{folder}をワークスペースにします。',
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
    openFolder: 'DeepSeek Harness デスクトップ版を開き、「Choose workspace」で{folder}を選びます。',
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
    openFolder: 'Claude デスクトップアプリを開いてログインし、「Code」タブで{folder}を選びます。',
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
    openFolder: 'Cursor で「Open Folder」から{folder}を開きます。',
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
    openFolder: 'Trae で{folder}を開きます。',
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
    openFolder: 'VS Code で{folder}を開きます。',
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
    openFolder: 'Qoder で{folder}を開きます。',
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
    openFolder: 'CodeBuddy で{folder}を開きます。',
    allowActions: '「Craft」エージェントパネルを開き、実行を求められたら許可します。',
    contextNote: 'CodeBuddy は AGENTS.md を自動で読みます。読まない場合は下のメッセージを送ってください。',
  },
  {
    id: 'other',
    label: 'その他の Agent',
    title: 'その他の Coding Agent（Windsurf / OpenCode / Aider など）',
    openFolder: 'お使いの AI コーディングツールで{folder}を開きます。',
    allowActions: 'ファイル編集と操作の実行を許可します（Agent モードなどに切り替えます）。',
    contextNote: '使っているツールが AGENTS.md を自動で読まない場合は、下のメッセージをそのまま送ってください。',
  },
]

export const ONBOARDING_COPY: Record<DocsLocale, OnboardingCopy> = {
  'zh-CN': {
    routesHeading: '选一条路',
    routeCards: [
      {
        id: 'A',
        title: '路线 A：全交给 AI',
        short: '路线 A',
        badge: '推荐',
        points: [
          '需要：一个 Coding Agent',
          'AI 自己准备源码版 N.E.K.O，能自己检查代码，改完自动重载',
          '第一次准备要下载不少东西，需要等一阵',
          '和你电脑上已装的 N.E.K.O 互不影响（第一次打开网页要在界面里填一次模型 API）',
        ],
      },
      {
        id: 'B',
        title: '路线 B：用已装好的 Steam / 桌面版，自己点',
        short: '路线 B',
        points: [
          '需要：已安装的 N.E.K.O + {git}（用官网的安装包装即可）',
          '起步快，马上能做',
          'AI 没法自己检查和看日志，出错时要你把报错复制给它',
          '每次改完要回插件页点「重载」',
        ],
      },
    ],
    routeStatusText: '当前：{route} — ',
    routeStatusLink: '步骤在下方「3. 跟着做」',
    routeCurrentTag: '当前路线',
    routeSwitchText: '改走{route}',
    tabHeading: '选择你使用的 Coding Agent',
    tabHint: '不局限于以下工具，任意能读写文件并执行操作的 AI 编程工具均可',
    downloadLabel: '官网下载',
    tutorialsHeading: '官方上手教程',
    openFolderHeading: '怎样打开文件夹',
    folderRouteA: '刚才新建的空文件夹',
    folderRouteB: '「插件制作工作台」生成的插件文件夹',
    allowActionsHeading: '允许 AI 执行操作',
    contextNoteHeading: '小提示',
    contextNoteRouteA:
      '空文件夹里还没有 AGENTS.md。AI 创建好插件后，下方那段话会让它去读插件目录里的 AGENTS.md，你不用额外操作。',
    promptTitle: '复制这段话发给 AI',
    promptDesc: '选好路线，把这段话粘贴到 Agent 的对话框里发送即可。',
    routeALabel: '路线 A：全交给 AI',
    routeBLabel: '路线 B：用已装好的 N.E.K.O',
    ideaLabel: '可选：先写一句你想做的插件（留空也行，AI 会给你出点子）',
    ideaPlaceholder: '例如：做一个提醒喝水的插件，猫娘会定时提醒我',
    copyPrompt: '一键复制',
    copiedPrompt: '已复制',
    copyFailed: '复制失败，请在下方选中全文手动复制',
    showPrompt: '查看完整内容',
    hidePrompt: '收起完整内容',
    agents: zhAgents,
  },
  en: {
    routesHeading: 'Pick a route',
    routeCards: [
      {
        id: 'A',
        title: 'Route A: let the AI do it all',
        short: 'Route A',
        badge: 'Recommended',
        points: [
          'You need: a coding agent',
          'The AI prepares a source build of N.E.K.O itself, can check its own code, and changes reload automatically',
          'The first setup downloads a lot, so it takes a while',
          'It does not affect the N.E.K.O already installed on your computer (the first time the web page opens, you fill in a model API key once in the UI)',
        ],
      },
      {
        id: 'B',
        title: 'Route B: use your installed Steam / desktop app, click yourself',
        short: 'Route B',
        points: [
          'You need: an installed N.E.K.O + {git} (install it with the official installer)',
          'Quick to start',
          'The AI cannot run checks or read logs, so when something fails you paste the error back to it',
          'After every change you click Reload on the plugin page yourself',
        ],
      },
    ],
    routeStatusText: 'Selected: {route} — ',
    routeStatusLink: 'steps are in “3. Follow the steps” below',
    routeCurrentTag: 'Current route',
    routeSwitchText: 'Switch to {route}',
    tabHeading: 'Choose your coding agent',
    tabHint: 'Any AI coding tool that can edit files and run actions on your computer works',
    downloadLabel: 'Official site',
    tutorialsHeading: 'Official tutorials',
    openFolderHeading: 'How to open the folder',
    folderRouteA: 'the empty folder you just created',
    folderRouteB: 'the plugin folder that Plugin studio created',
    allowActionsHeading: 'Let the AI take actions',
    contextNoteHeading: 'Good to know',
    contextNoteRouteA:
      'The empty folder has no AGENTS.md yet. Once the AI creates the plugin, the message below tells it to read the AGENTS.md in the plugin folder — nothing extra for you to do.',
    promptTitle: 'Copy this message and send it to the AI',
    promptDesc: 'Pick a route, paste the message into your agent’s chat box and send.',
    routeALabel: 'Route A: let the AI do it all',
    routeBLabel: 'Route B: use my installed N.E.K.O',
    ideaLabel: 'Optional: describe your plugin idea in one line (or leave blank and let the AI suggest ideas)',
    ideaPlaceholder: 'e.g. a plugin where the catgirl reminds me to drink water',
    copyPrompt: 'Copy',
    copiedPrompt: 'Copied',
    copyFailed: 'Copy failed — select the full text below and copy it manually',
    showPrompt: 'Preview full text',
    hidePrompt: 'Hide full text',
    agents: enAgents,
  },
  ja: {
    routesHeading: 'ルートを選ぶ',
    routeCards: [
      {
        id: 'A',
        title: 'ルート A：全部 AI に任せる',
        short: 'ルート A',
        badge: 'おすすめ',
        points: [
          '必要なもの：Coding Agent 1 つ',
          'AI がソース版 N.E.K.O を自分で用意し、コードのチェックもでき、変更は自動でリロードされる',
          '初回はダウンロードが多く、時間がかかる',
          'すでにインストール済みの N.E.K.O には影響しない（初めて Web ページを開いたとき、画面でモデル API を一度入力する）',
        ],
      },
      {
        id: 'B',
        title: 'ルート B：インストール済みの Steam / デスクトップ版で自分で押す',
        short: 'ルート B',
        points: [
          '必要なもの：インストール済みの N.E.K.O + {git}（公式インストーラーでインストール）',
          'すぐ始められる',
          'AI は自分でチェックやログ確認ができないので、エラーが出たらそのまま貼って送る',
          '変更のたびにプラグイン画面で「リロード」を押す',
        ],
      },
    ],
    routeStatusText: '選択中：{route} — ',
    routeStatusLink: '手順は下の「3. 手順どおりに進める」へ',
    routeCurrentTag: '選択中',
    routeSwitchText: '{route}に切り替え',
    tabHeading: '使用する Coding Agent を選択',
    tabHint: 'この一覧以外でも、ファイル編集と操作の実行ができる AI ツールなら使えます',
    downloadLabel: '公式サイト',
    tutorialsHeading: '公式チュートリアル',
    openFolderHeading: 'フォルダの開き方',
    folderRouteA: '先ほど作った空のフォルダ',
    folderRouteB: '「プラグイン制作」で作成したプラグインフォルダ',
    allowActionsHeading: 'AI に操作を許可する',
    contextNoteHeading: '補足',
    contextNoteRouteA:
      '空のフォルダにはまだ AGENTS.md がありません。AI がプラグインを作成したあと、下のメッセージがプラグインフォルダ内の AGENTS.md を読むよう指示するので、追加の操作は不要です。',
    promptTitle: 'この文をコピーして AI に送信',
    promptDesc: 'ルートを選び、この文を Agent のチャット欄に貼って送るだけです。',
    routeALabel: 'ルート A：全部 AI に任せる',
    routeBLabel: 'ルート B：インストール済みの N.E.K.O を使う',
    ideaLabel: '任意：作りたいプラグインを 1 文で入力（空欄なら AI が提案します）',
    ideaPlaceholder: '例：猫娘が水分補給を教えてくれるプラグイン',
    copyPrompt: 'コピー',
    copiedPrompt: 'コピーしました',
    copyFailed: 'コピーできませんでした。下の全文を選択して手動でコピーしてください',
    showPrompt: '全文を表示',
    hidePrompt: '全文を閉じる',
    agents: jaAgents,
  },
}
