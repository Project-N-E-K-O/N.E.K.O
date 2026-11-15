export type DocsLocale = 'en' | 'zh-CN' | 'ja'

export interface AgentStep {
  text: string
  code?: string
  isPromptStep?: boolean
}

export interface AgentConfig {
  id: string
  label: string
  title: string
  eyebrow: string
  tagline: string
  websiteUrl: string
  websiteLabel: string
  walkthroughAgent?: 'codex' | 'dsh'
  steps: AgentStep[]
}

export interface FallbackSection {
  title: string
  intro: string
  items: Array<{
    label: string
    description: string
    code?: string
  }>
}

export interface OnboardingCopy {
  prepBadge: string
  prepText: string
  tabHeading: string
  tabHint: string
  stepsHeading: string
  walkthroughBadge: string
  promptStepTitle: string
  promptStepDesc: string
  ideaLabel: string
  ideaPlaceholder: string
  copyPrompt: string
  copiedPrompt: string
  copyCode: string
  copiedCode: string
  showPrompt: string
  hidePrompt: string
  walkthroughCtaTitle: string
  walkthroughCtaDesc: string
  walkthroughCtaCodex: string
  walkthroughCtaDsh: string
  fallbacks: FallbackSection[]
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
    const requirementBlock = trimmed
      ? `我想做的插件需求如下：\n「${trimmed}」\n\n请先完成第 1 步（检查 git/uv、就绪 N.E.K.O 仓库并确认 \`uv run neko-plugin --help\` 可用），为这个需求拟定一个合规的英文小写下划线 \`plugin_id\`，然后严格用 \`uv run neko-plugin init\` 创建项目、实现功能并跑通第 4 步检查。`
      : '我还没有写具体需求。请先帮我完成第 1 步（检查 git/uv、就绪 N.E.K.O 仓库并确认 `uv run neko-plugin --help` 可用），然后用 2～3 句通俗的话问我想给 N.E.K.O 做什么插件（可以举 3 个简单有趣的例子启发我），等我回答后再用 `uv run neko-plugin init` 开始制作。'

    return `你现在是我的 N.E.K.O 插件开发助手。请全程通过 N.E.K.O 官方 \`neko-plugin\` CLI 帮我创建、编写、校验并打包插件，并用简短通俗的话向我同步每一步进展（遇到需要安装的软件或耗时命令先告诉我）。

## 1. 环境检查与 N.E.K.O 源码仓库就绪
1. 在终端检查 \`git --version\` 和 \`uv --version\`：
   - 若缺少任一工具，请先停下来给我对应系统的官方安装命令（Git: https://git-scm.com/downloads ，uv: https://docs.astral.sh/uv/getting-started/installation/ ），不要跳过 \`uv\` 直接使用系统 \`pip\`。
2. 定位 \`N.E.K.O\` 仓库根目录：
   - 若当前目录已有 \`launcher.py\` 和 \`plugin/neko_plugin_cli/\`，当前目录即为仓库根目录；
   - 若子目录 \`./N.E.K.O\` 已存在，直接进入复用，严禁删除或覆盖已有的 \`N.E.K.O\` 目录；
   - 若都没有，先征得我同意后执行：
     \`git clone --filter=blob:none https://github.com/Project-N-E-K-O/N.E.K.O.git\`
     并进入 \`N.E.K.O\` 目录。
3. 在 \`N.E.K.O\` 仓库根目录同步依赖并验证 CLI：
   \`\`\`bash
   uv sync
   uv run neko-plugin --help
   \`\`\`
   （注：CLI 输出开头若带有应用初始化日志属正常现象，退出码为 0 且无 \`[FAIL]\` 即表示就绪。后续所有 \`uv run neko-plugin ...\` 命令均在 \`N.E.K.O\` 仓库根目录执行。）

## 2. 必须使用 \`neko-plugin init\` 创建插件（严禁手搓初始目录）
- 插件 ID 必须符合 Market 规范 \`^[a-z][a-z0-9_]*$\`（小写字母开头，仅含小写字母、数字和下划线）。
- 在 \`N.E.K.O\` 根目录运行脚手架命令：
  \`\`\`bash
  uv run neko-plugin init <plugin_id> --type plugin --name "<插件显示名>"
  \`\`\`
  （仅在需要桥接 MCP/NoneBot 等外部协议时才用 \`--type adapter\`。）
- 生成的源码位于 \`plugin/plugins/<plugin_id>/\`（自带插件独立 \`.git/\`、\`plugin.toml\`、\`config.example.toml\`、\`__init__.py\`、\`pyproject.toml\`、\`ruff.toml\`、\`tests/test_smoke.py\` 及 \`.github/workflows/\`）。
- 读写边界：只修改 \`plugin/plugins/<plugin_id>/\` 内的文件；不要修改 N.E.K.O 宿主源码，不要把插件复制到用户数据目录或创建符号链接。

## 3. 编码规范与仓库内权威文档索引
动笔前请按需直接阅读仓库本地文档与公开 SDK 定义，不要凭猜测调用内部接口：
- 本地中文文档：\`docs/zh-CN/plugins/plugin-toml.md\`、\`entries.md\`、\`sdk-reference.md\`、\`push-message.md\`、\`decorators.md\`、\`tool-calling.md\`、\`hosted-ui.md\`、\`chat-cards.md\`、\`cli.md\`（英文主规范在 \`docs/plugins/*.md\`）
- 公开 SDK 导出：\`plugin/sdk/plugin/__init__.py\`
- 硬性契约：
  1. 普通插件只从 \`plugin.sdk.plugin\` 导入；严禁使用已移除的 \`extension\` / \`script\` 包类型、\`plugin.sdk.extension\`、\`self.memory\` 或内部模块 \`plugin.core.*\` / \`plugin.server.*\`。
  2. 所有运行时入口（\`@plugin_entry\`、\`@llm_tool\`、\`@lifecycle\`、\`@timer_interval\` 等）必须是 \`async def\` 并在参数末尾保留 \`**_\`，返回 \`Ok(data={...})\` 或 \`Err(code="...", message="...")\`。
  3. 区分 \`[plugin].entry\`（加载类 \`plugin.plugins.<plugin_id>:<ClassName>\`）、\`@plugin_entry\`（手动/Task Agent 调用入口）与 \`@llm_tool\`（对话期间供猫娘调用的工具）；在 \`plugin.toml\` 的 \`[plugin]\` 下补齐 \`description\` 与单行 \`short_description\`。
  4. 随包目录视为只读：持久化数据用 \`self.data_path(...)\`，缓存用 \`self.cache_path(...)\`，配置读 \`self.config\` 并在 \`config.example.toml\` 提供默认项。
  5. 如需第三方 Python 依赖，先写入 \`plugin/plugins/<plugin_id>/pyproject.toml\`，再在仓库根目录执行 \`uv run neko-plugin sync <plugin_id> --clean\` 同步到插件 \`vendor/\`。

## 4. 每次修改后必跑 \`neko-plugin\` CLI 校验
在向我交付前，必须在 \`N.E.K.O\` 仓库根目录依次通过以下检查（修复所有 error 和 \`[FAIL]\`，并更新对应单元测试）：
\`\`\`bash
uv run neko-plugin check <plugin_id>
uv run neko-plugin lint <plugin_id>
uv run pytest plugin/plugins/<plugin_id>/tests
\`\`\`
（若包含 Hosted TSX 面板，还需在 \`frontend/plugin-manager\` 下运行 \`npm run check-hosted-tsx -- plugin/plugins/<plugin_id>\`；若准备打包或发布，运行 \`uv run neko-plugin precheck <plugin_id>\` 跑通完整 CI 门禁。）

## 5. 运行验证与打包交付
检查全部通过后，请清晰告诉我两种验证方式：
- 源码版直接运行：在 \`N.E.K.O\` 根目录运行 \`uv run python launcher.py\`（可加 \`NEKO_PLUGIN_HOT_RELOAD=true\` 开启自动热重载），在插件页面点「刷新 → 启动」（后续修改点「重载」）。
- 导出安装包（适合 Steam / 桌面安装版 N.E.K.O 或分享给他人）：运行 \`uv run neko-plugin build <plugin_id> --out target/<plugin_id>.neko-plugin\`，提示我在 N.E.K.O「插件中心」导入该文件（不要使用已禁用的 \`neko-plugin install\`）。

## 6. 我的插件需求
${requirementBlock}`
  }

  if (locale === 'ja') {
    const requirementBlock = trimmed
      ? `作成したいプラグインの要件は次のとおりです：\n「${trimmed}」\n\nまずステップ 1（git / uv の確認、N.E.K.O リポジトリの準備、\`uv run neko-plugin --help\` の確認）を完了し、規約に合った英小文字スネークケースの \`plugin_id\` を提案したうえで、\`uv run neko-plugin init\` でプロジェクトを作成し、実装とステップ 4 の検証まで進めてください。`
      : '具体的な要件はまだ入力していません。まずステップ 1（git / uv の確認、N.E.K.O リポジトリの準備、\`uv run neko-plugin --help\` の確認）を完了してから、作りたいプラグインの内容を 2〜3 文でやさしく質問してください（初心者向けの簡単なアイデア例を 3 つ挙げてください）。私の回答後に \`uv run neko-plugin init\` で作成を始めてください。'

    return `あなたは私の N.E.K.O プラグイン開発アシスタントです。公式の \`neko-plugin\` CLI を使ってプラグインの作成・実装・検証・パッケージ化まで一貫してサポートし、各ステップをわかりやすく簡潔に共有してください（ツールのインストールや時間のかかるコマンドの前に一言知らせてください）。

## 1. 環境確認と N.E.K.O ソースリポジトリの準備
1. ターミナルで \`git --version\` と \`uv --version\` を確認します：
   - 未インストールのものがあれば、OS に合った公式インストール案内（Git: https://git-scm.com/downloads 、uv: https://docs.astral.sh/uv/getting-started/installation/ ）を提示して確認してください。\`uv\` を飛ばしてシステムの \`pip\` を直接使わないでください。
2. \`N.E.K.O\` リポジトリのルートを特定します：
   - カレントディレクトリに \`launcher.py\` と \`plugin/neko_plugin_cli/\` があれば、そこがルートです。
   - \`./N.E.K.O\` サブディレクトリが既に存在する場合はそこへ移動して再利用し、既存の \`N.E.K.O\` ディレクトリを決して削除・上書きしないでください。
   - どちらもない場合は、私の確認を取ってから次を実行して \`N.E.K.O\` に移動します：
     \`git clone --filter=blob:none https://github.com/Project-N-E-K-O/N.E.K.O.git\`
3. \`N.E.K.O\` ルートで依存関係を同期し、CLI を確認します：
   \`\`\`bash
   uv sync
   uv run neko-plugin --help
   \`\`\`
   （先頭に初期化ログが出力されても終了コード 0 かつ \`[FAIL]\` がなければ正常です。以後の \`uv run neko-plugin ...\` コマンドはすべて \`N.E.K.O\` ルートで実行してください。）

## 2. 必ず \`neko-plugin init\` で作成する（手動で雛形を作らない）
- \`<plugin_id>\` は Market 規約 \`^[a-z][a-z0-9_]*$\`（英小文字始まり、英小文字・数字・アンダースコアのみ）に従います。
- \`N.E.K.O\` ルートでスキャフォールドを実行します：
  \`\`\`bash
  uv run neko-plugin init <plugin_id> --type plugin --name "<表示名>"
  \`\`\`
  （MCP / NoneBot など外部プロトコルをブリッジする場合のみ \`--type adapter\` を使用。）
- \`plugin/plugins/<plugin_id>/\` に独立した \`.git/\`、\`plugin.toml\`、\`config.example.toml\`、\`__init__.py\`、\`pyproject.toml\`、\`ruff.toml\`、\`tests/test_smoke.py\`、\`.github/workflows/\` が生成されます。
- 編集範囲：\`plugin/plugins/<plugin_id>/\` 内のみを編集してください。ホスト側のコードは変更せず、ユーザーデータディレクトリへのコピーやシンボリックリンク作成も行わないでください。

## 3. 実装ルールとローカル公式ドキュメント参照
推測で内部 API を呼ばず、リポジトリ内の公式ドキュメントと公開 SDK を参照してください：
- ローカルドキュメント：\`docs/ja/plugins/\` または \`docs/plugins/\` 配下の \`plugin-toml.md\`、\`entries.md\`、\`sdk-reference.md\`、\`push-message.md\`、\`decorators.md\`、\`tool-calling.md\`、\`hosted-ui.md\`、\`chat-cards.md\`、\`cli.md\`
- 公開 SDK：\`plugin/sdk/plugin/__init__.py\`
- 必須ルール：
  1. 通常のプラグインは \`plugin.sdk.plugin\` からのみインポートする。廃止済みの \`extension\` / \`script\`、\`plugin.sdk.extension\`、\`self.memory\`、内部モジュール \`plugin.core.*\` / \`plugin.server.*\` は使用禁止。
  2. 実行時ハンドラ（\`@plugin_entry\`、\`@llm_tool\`、\`@lifecycle\`、\`@timer_interval\` など）はすべて \`async def\` とし、末尾に \`**_\` を受け取り、\`Ok(data={...})\` または \`Err(code="...", message="...")\` を返す。
  3. \`[plugin].entry\`（クラス \`plugin.plugins.<plugin_id>:<ClassName>\`）、\`@plugin_entry\`（手動 / Task Agent 呼び出し）、\`@llm_tool\`（会話中の LLM ツール）を明確に区別し、\`plugin.toml\` の \`[plugin]\` に \`description\` と 1 行の \`short_description\` を記述する。
  4. パッケージ内のコードは読み取り専用として扱い、永続データは \`self.data_path(...)\`、キャッシュは \`self.cache_path(...)\`、設定は \`self.config\`（初期値は \`config.example.toml\`）を使う。
  5. 外部 Python パッケージが必要な場合は \`plugin/plugins/<plugin_id>/pyproject.toml\` に追記し、ルートで \`uv run neko-plugin sync <plugin_id> --clean\` を実行して \`vendor/\` を同期する。

## 4. 変更後に必ず実行する \`neko-plugin\` CLI チェック
完了報告の前に、\`N.E.K.O\` ルートで次をすべて通し、error や \`[FAIL]\` を解消してください：
\`\`\`bash
uv run neko-plugin check <plugin_id>
uv run neko-plugin lint <plugin_id>
uv run pytest plugin/plugins/<plugin_id>/tests
\`\`\`
（Hosted TSX UI を含む場合は \`frontend/plugin-manager\` で \`npm run check-hosted-tsx -- plugin/plugins/<plugin_id>\` も実行。パッケージ化や公開前は \`uv run neko-plugin precheck <plugin_id>\` を実行。）

## 5. 動作確認とパッケージ出力の案内
チェック通過後、次の確認手順を案内してください：
- ソース版でそのまま起動：\`N.E.K.O\` ルートで \`uv run python launcher.py\`（自動ホットリロードは \`NEKO_PLUGIN_HOT_RELOAD=true\`）を実行し、Plugins 画面で「Refresh → Start」（変更後は「Reload」）。
- \`.neko-plugin\` パッケージ出力（Steam / デスクトップ版 N.E.K.O への導入や配布用）：\`uv run neko-plugin build <plugin_id> --out target/<plugin_id>.neko-plugin\` を実行し、N.E.K.O の Plugin Center からインポートするよう案内する（無効化されている \`neko-plugin install\` は使わない）。

## 6. 作りたいプラグインの要件
${requirementBlock}`
  }

  const requirementBlock = trimmed
    ? `Here is the plugin I want to build:\n"${trimmed}"\n\nPlease complete Step 1 first (verify git/uv, prepare the N.E.K.O repository, and confirm \`uv run neko-plugin --help\` works), propose a valid lowercase snake_case \`plugin_id\`, scaffold it with \`uv run neko-plugin init\`, implement the feature, and pass all checks in Step 4.`
    : 'I have not typed my plugin idea yet. Please complete Step 1 first (verify git/uv, prepare the N.E.K.O repository, and confirm `uv run neko-plugin --help` works), then ask me in 2-3 friendly plain-English sentences what plugin I want to build for N.E.K.O (share 3 fun beginner-friendly examples for inspiration) before running `uv run neko-plugin init`.'

  return `You are my N.E.K.O plugin development assistant. Use the official \`neko-plugin\` CLI end-to-end to scaffold, implement, validate, and package my plugin, keeping me updated in plain, concise language (ask before installing system tools or running long downloads).

## 1. Check prerequisites and prepare the N.E.K.O repository
1. Verify \`git --version\` and \`uv --version\` in the terminal:
   - If either tool is missing, stop and give me the official install command for my OS (Git: https://git-scm.com/downloads , uv: https://docs.astral.sh/uv/getting-started/installation/ ). Never bypass \`uv\` with bare system \`pip\`.
2. Locate the \`N.E.K.O\` repository root:
   - If the current directory already contains \`launcher.py\` and \`plugin/neko_plugin_cli/\`, use the current directory as the repo root.
   - If \`./N.E.K.O\` already exists, enter and reuse it — NEVER delete or overwrite an existing \`N.E.K.O\` directory.
   - Otherwise, confirm with me and run:
     \`git clone --filter=blob:none https://github.com/Project-N-E-K-O/N.E.K.O.git\`
     then \`cd N.E.K.O\`.
3. Sync dependencies and verify the CLI from the \`N.E.K.O\` repository root:
   \`\`\`bash
   uv sync
   uv run neko-plugin --help
   \`\`\`
   (App startup logs before help output are normal; exit code 0 without \`[FAIL]\` means ready. Run all \`uv run neko-plugin ...\` commands from the \`N.E.K.O\` repository root.)

## 2. Always scaffold with \`neko-plugin init\` (never hand-create boilerplate)
- Pick a Market-valid \`<plugin_id>\` matching \`^[a-z][a-z0-9_]*$\` (lowercase letters, digits, and underscores, starting with a letter).
- Run from the \`N.E.K.O\` repository root:
  \`\`\`bash
  uv run neko-plugin init <plugin_id> --type plugin --name "<Display Name>"
  \`\`\`
  (Use \`--type adapter\` only when bridging external protocols like MCP or NoneBot.)
- This creates \`plugin/plugins/<plugin_id>/\` with its own \`.git/\`, \`plugin.toml\`, \`config.example.toml\`, \`__init__.py\`, \`pyproject.toml\`, \`ruff.toml\`, \`tests/test_smoke.py\`, and \`.github/workflows/\`.
- Write boundary: only edit files inside \`plugin/plugins/<plugin_id>/\`. Do not modify N.E.K.O host code, copy plugins into user-data folders, or create symlinks.

## 3. Follow current SDK contracts and local canonical docs
Read the local docs and public SDK exports inside the repo before implementing features instead of guessing internal APIs:
- Local docs: \`docs/plugins/plugin-toml.md\`, \`entries.md\`, \`sdk-reference.md\`, \`push-message.md\`, \`decorators.md\`, \`tool-calling.md\`, \`hosted-ui.md\`, \`chat-cards.md\`, \`cli.md\`
- Public SDK surface: \`plugin/sdk/plugin/__init__.py\`
- Hard rules:
  1. Import normal plugins exclusively from \`plugin.sdk.plugin\`. Never use removed \`extension\` / \`script\` package types, \`plugin.sdk.extension\`, \`self.memory\`, or internal modules (\`plugin.core.*\`, \`plugin.server.*\`).
  2. Every runtime handler (\`@plugin_entry\`, \`@llm_tool\`, \`@lifecycle\`, \`@timer_interval\`, etc.) must be \`async def\`, accept \`**_\`, and return \`Ok(data={...})\` or \`Err(code="...", message="...")\`.
  3. Distinguish \`[plugin].entry\` (class path \`plugin.plugins.<plugin_id>:<ClassName>\`), \`@plugin_entry\` (manual / Task Agent operations), and \`@llm_tool\` (conversation-time LLM tools). Set both \`description\` and a one-line \`short_description\` under \`[plugin]\` in \`plugin.toml\`.
  4. Treat packaged plugin files as read-only: store persistent data under \`self.data_path(...)\`, cache under \`self.cache_path(...)\`, and config via \`self.config\` (with defaults in \`config.example.toml\`).
  5. If third-party Python packages are needed, declare them in \`plugin/plugins/<plugin_id>/pyproject.toml\` and run \`uv run neko-plugin sync <plugin_id> --clean\` from the repo root to populate \`vendor/\`.

## 4. Validate every change with \`neko-plugin\` CLI
Before reporting completion, run these from the \`N.E.K.O\` repo root and fix any errors or \`[FAIL]\`:
\`\`\`bash
uv run neko-plugin check <plugin_id>
uv run neko-plugin lint <plugin_id>
uv run pytest plugin/plugins/<plugin_id>/tests
\`\`\`
(If Hosted TSX UI is used, also run \`npm run check-hosted-tsx -- plugin/plugins/<plugin_id>\` inside \`frontend/plugin-manager\`. Before packaging or publishing, run \`uv run neko-plugin precheck <plugin_id>\`.)

## 5. Run or package for delivery
Once checks pass, explain both ways I can try the plugin:
- Run directly from source: run \`uv run python launcher.py\` from \`N.E.K.O\` root (add \`NEKO_PLUGIN_HOT_RELOAD=true\` for automatic hot reload on save), then click Refresh -> Start on the Plugins page (and Reload after edits).
- Build a \`.neko-plugin\` package (for Steam / desktop N.E.K.O installs or sharing): run \`uv run neko-plugin build <plugin_id> --out target/<plugin_id>.neko-plugin\` and tell me to import that file in the N.E.K.O Plugin Center (never use the disabled \`neko-plugin install\` command).

## 6. My plugin request
${requirementBlock}`
}

const zhAgents: AgentConfig[] = [
  {
    id: 'dsh',
    label: 'DeepSeek Harness',
    title: 'DeepSeek Harness (dsh)',
    eyebrow: '开源本地 Agent · 浏览器可视化界面',
    tagline: 'DeepSeek 开源的插件化编程智能体，一行命令启动本地网页端，适合直观操作。',
    websiteUrl: 'https://github.com/deepseek-ai/deepseek-harness',
    websiteLabel: '官方网站',
    walkthroughAgent: 'dsh',
    steps: [
      {
        text: '确保本机已安装 Node.js (>= 24.9)，在终端运行下方命令自动打开 dsh 网页端：',
        code: 'npx @deepseek-ai/dsh web',
      },
      {
        text: '在网页右上角打开「Settings → Models」，填入你的 DeepSeek API Key 并保存。',
      },
      {
        text: '点击「Choose workspace」，选择任意空工作文件夹（或已有的 N.E.K.O 源码目录）。',
      },
      {
        text: '复制下方 Setup Prompt 粘贴进会话框发送，在弹窗询问执行命令权限时点击批准：',
        isPromptStep: true,
      },
      {
        text: '等 dsh 自动跑通 neko-plugin check 后，按它给出的提示启动 N.E.K.O 或导入 .neko-plugin 安装包。',
      },
    ],
  },
  {
    id: 'codex',
    label: 'Codex',
    title: 'OpenAI Codex',
    eyebrow: 'OpenAI 官方 · 桌面端 / CLI',
    tagline: '支持 Codex 桌面客户端与终端 CLI，直接登录 ChatGPT 账号即可接管本地仓库与命令行。',
    websiteUrl: 'https://openai.com/codex/',
    websiteLabel: '官方网站',
    walkthroughAgent: 'codex',
    steps: [
      {
        text: '打开 Codex 桌面端，或在终端安装并启动 Codex CLI（首次运行按提示用 ChatGPT 登录）：',
        code: 'npm install -g @openai/codex && codex',
      },
      {
        text: '在 Codex 中打开一个空工作文件夹（或你电脑上已有的 N.E.K.O 源码目录）。',
      },
      {
        text: '一键复制下方 Setup Prompt 粘贴给 Codex 发送，让它自动拉取环境并调用 neko-plugin CLI：',
        isPromptStep: true,
      },
      {
        text: '当 Codex 请求运行 git / uv 命令时允许执行，等待它自动写完代码并通过 neko-plugin check。',
      },
      {
        text: '按 Codex 给出的说明运行 launcher.py 刷新启动，或把打包好的 .neko-plugin 导入插件中心。',
      },
    ],
  },
  {
    id: 'trae',
    label: 'Trae',
    title: 'Trae',
    eyebrow: '字节跳动 · 免费 AI IDE · 国内直连',
    tagline: '图形化代码编辑器，内置 Builder / SOLO 全流程模式，对零命令行经验的新手非常友好。',
    websiteUrl: 'https://www.trae.cn/',
    websiteLabel: '官方网站',
    steps: [
      { text: '从官网下载并安装 Trae 客户端，登录后点击「打开文件夹」选一个空文件夹（或 N.E.K.O 目录）。' },
      { text: '在右侧 AI 对话面板顶部切换到「Builder」或「SOLO」智能体模式（具备终端与多文件读写权限）。' },
      {
        text: '复制下方 Setup Prompt 粘贴进右侧输入框发送：',
        isPromptStep: true,
      },
      { text: '遇到终端运行确认时点击「运行 / 允许」，等待 Trae 调用 neko-plugin init 与 check 完成自检。' },
      { text: '在 N.E.K.O 插件管理页面点击「刷新 → 启动」，或导入生成的 .neko-plugin 安装包体验效果。' },
    ],
  },
  {
    id: 'claude-code',
    label: 'Claude Code',
    title: 'Claude Code',
    eyebrow: 'Anthropic 官方 · 终端 / IDE 插件',
    tagline: '擅长多步工具链执行与自主排错，能非常稳定地跑完 neko-plugin 初始化、单测与打包全流程。',
    websiteUrl: 'https://docs.anthropic.com/en/docs/claude-code/overview',
    websiteLabel: '官方文档',
    steps: [
      {
        text: '进入你的工作文件夹，安装并启动 Claude Code（也可在 VS Code / JetBrains 装同名扩展）：',
        code: 'npm install -g @anthropic-ai/claude-code && claude',
      },
      {
        text: '复制下方 Setup Prompt 粘贴到终端会话中回车：',
        isPromptStep: true,
      },
      { text: '确认允许执行 git、uv sync 与 uv run neko-plugin 系列命令，按提示用大白话补充你的插件想法。' },
      { text: '等待 Claude Code 跑通 check / lint / pytest 后，在 N.E.K.O 中启动插件或导入打包产物。' },
    ],
  },
  {
    id: 'cursor',
    label: 'Cursor',
    title: 'Cursor',
    eyebrow: '主流 AI 原生编辑器',
    tagline: '可视化文件树配合右侧 Agent 面板，可直观查看 AI 生成的 plugin.toml 与 Python 源码。',
    websiteUrl: 'https://www.cursor.com/',
    websiteLabel: '官方网站',
    steps: [
      { text: '打开 Cursor，点击「Open Folder」打开一个空工作文件夹（或已有的 N.E.K.O 源码目录）。' },
      { text: '按 Ctrl+I（macOS 按 Cmd+I）唤出右侧面板，确认左下角模式已设为「Agent」。' },
      {
        text: '复制下方 Setup Prompt 粘贴进输入框发送：',
        isPromptStep: true,
      },
      { text: '允许终端执行 uv run neko-plugin 命令并在完成后点击「Keep / Accept」保存文件改动。' },
      { text: '按提示启动源码版 N.E.K.O 刷新加载，或将 build 出的 .neko-plugin 包导入插件中心。' },
    ],
  },
  {
    id: 'qoder',
    label: 'Qoder / 通义灵码',
    title: 'Qoder / 通义灵码',
    eyebrow: '阿里通义 · 独立 IDE 与编辑器插件',
    tagline: '国内网络开箱即用；既可用独立 Qoder IDE，也可在已有 VS Code / PyCharm 中装通义灵码插件。',
    websiteUrl: 'https://qoder.com/',
    websiteLabel: '官方网站',
    steps: [
      { text: '打开 Qoder IDE（或在 VS Code / PyCharm 中安装并登录「通义灵码」扩展），打开你的工作文件夹。' },
      { text: '在对话侧边栏切换到「智能体 / Agent（或 Quest）」模式，确保允许读写文件与执行终端命令。' },
      {
        text: '复制下方 Setup Prompt 粘贴发送：',
        isPromptStep: true,
      },
      { text: '等待智能体通过 neko-plugin init 创建项目并跑过 neko-plugin check 后，在 N.E.K.O 中加载验证。' },
    ],
  },
  {
    id: 'codebuddy',
    label: 'CodeBuddy',
    title: '腾讯 CodeBuddy',
    eyebrow: '腾讯 · Craft 智能体 · 国内直连',
    tagline: '支持混元与 DeepSeek 模型协同，提供独立 IDE 及 VS Code / JetBrains 插件形态。',
    websiteUrl: 'https://copilot.tencent.com/',
    websiteLabel: '官方网站',
    steps: [
      { text: '下载安装 CodeBuddy IDE（或在现有编辑器安装腾讯云代码助手 CodeBuddy 插件），打开工作文件夹。' },
      { text: '进入侧边栏「Craft 智能体」对话面板。' },
      {
        text: '复制下方 Setup Prompt 粘贴进对话框发送：',
        isPromptStep: true,
      },
      { text: '批准终端运行 uv 与 neko-plugin 命令，待检查全部通过后在 N.E.K.O 中启动或导入插件包。' },
    ],
  },
  {
    id: 'vscode',
    label: 'VS Code (Cline / Copilot)',
    title: 'VS Code + Cline / Roo Code / Copilot',
    eyebrow: '复用现有 VS Code · 自由接入任意模型',
    tagline: '如果你电脑里已经装了 VS Code，装一个开源 Cline 扩展或开启 Copilot Agent 模式即可直接上手。',
    websiteUrl: 'https://code.visualstudio.com/',
    websiteLabel: '官方网站',
    steps: [
      { text: '在 VS Code 扩展市场安装「Cline」（填任意模型 API Key）或打开内置「GitHub Copilot Chat」。' },
      { text: '打开空工作文件夹，将 Cline 设为「Act」模式（或将 Copilot Chat 切换为「Agent」模式）。' },
      {
        text: '复制下方 Setup Prompt 粘贴进侧边栏聊天框发送：',
        isPromptStep: true,
      },
      { text: '依次批准终端命令执行，等待 neko-plugin check 显示 0 error(s) 后在 N.E.K.O 中体验。' },
    ],
  },
  {
    id: 'other',
    label: '其他 Agent',
    title: '其他 Coding Agent（OpenCode / Windsurf / ZCode / Aider 等）',
    eyebrow: '通用兼容 · 只要能读写文件并跑终端即可',
    tagline: '无论是智谱 ZCode、Windsurf、OpenCode、Aider 还是 OpenClaw / Hermes，接入方式完全相同。',
    websiteUrl: '/zh-CN/plugins/quick-start',
    websiteLabel: 'CLI 手动教程',
    steps: [
      { text: '在你的 AI 编程工具中打开一个空文件夹（或已有的 N.E.K.O 仓库目录），开启文件读写与终端执行权限。' },
      {
        text: '一键复制下方通用 Setup Prompt 粘贴给 Agent，让它接管环境准备与 neko-plugin CLI 操作：',
        isPromptStep: true,
      },
      { text: '告诉 Agent 你的插件点子，等待它完成编码并通过 uv run neko-plugin check / pytest。' },
      { text: '启动源码版 N.E.K.O 测试，或让 Agent 运行 neko-plugin build 生成安装包导入插件中心。' },
    ],
  },
]

const enAgents: AgentConfig[] = [
  {
    id: 'codex',
    label: 'Codex',
    title: 'OpenAI Codex',
    eyebrow: 'Official OpenAI Agent · Desktop App & CLI',
    tagline: 'Sign in with ChatGPT in the Codex app or CLI and let it drive git, uv, and neko-plugin for you.',
    websiteUrl: 'https://openai.com/codex/',
    websiteLabel: 'Official site',
    walkthroughAgent: 'codex',
    steps: [
      {
        text: 'Open the Codex desktop app, or install and launch the Codex CLI in your terminal:',
        code: 'npm install -g @openai/codex && codex',
      },
      {
        text: 'Point Codex at an empty workspace folder (or your existing N.E.K.O repository folder).',
      },
      {
        text: 'Copy the Setup Prompt below and paste it into Codex so it prepares the repo and neko-plugin CLI:',
        isPromptStep: true,
      },
      {
        text: 'Approve git and uv commands when prompted, and wait for Codex to pass neko-plugin check.',
      },
      {
        text: 'Start N.E.K.O from source to test live, or import the built .neko-plugin file in Plugin Center.',
      },
    ],
  },
  {
    id: 'dsh',
    label: 'DeepSeek Harness',
    title: 'DeepSeek Harness (dsh)',
    eyebrow: 'Open-Source Agent Runtime · Browser Web UI',
    tagline: 'An MIT-licensed local coding agent from DeepSeek with a clean browser UI launched in one command.',
    websiteUrl: 'https://github.com/deepseek-ai/deepseek-harness',
    websiteLabel: 'Official repo',
    walkthroughAgent: 'dsh',
    steps: [
      {
        text: 'With Node.js (>= 24.9) installed, start the local Web UI from your terminal:',
        code: 'npx @deepseek-ai/dsh web',
      },
      {
        text: 'Open Settings -> Models in the browser UI, enter your DeepSeek or compatible API key, and save.',
      },
      {
        text: 'Click Choose workspace and select an empty workspace folder (or an existing N.E.K.O checkout).',
      },
      {
        text: 'Paste the Setup Prompt below into the session box and approve command execution when asked:',
        isPromptStep: true,
      },
      {
        text: 'Once dsh finishes neko-plugin check with 0 errors, launch N.E.K.O or import the .neko-plugin package.',
      },
    ],
  },
  {
    id: 'claude-code',
    label: 'Claude Code',
    title: 'Claude Code',
    eyebrow: 'Anthropic Official · Terminal & IDE Extension',
    tagline: 'Strong multi-step terminal execution that reliably scaffolds, tests, and lints plugins end-to-end.',
    websiteUrl: 'https://docs.anthropic.com/en/docs/claude-code/overview',
    websiteLabel: 'Official docs',
    steps: [
      {
        text: 'Open a terminal in your workspace folder, then install and start Claude Code:',
        code: 'npm install -g @anthropic-ai/claude-code && claude',
      },
      {
        text: 'Copy and paste the Setup Prompt below into the session:',
        isPromptStep: true,
      },
      { text: 'Approve git, uv sync, and uv run neko-plugin commands, then describe your plugin idea in plain English.' },
      { text: 'After check, lint, and pytest pass, test the plugin in N.E.K.O or import the built .neko-plugin archive.' },
    ],
  },
  {
    id: 'cursor',
    label: 'Cursor',
    title: 'Cursor',
    eyebrow: 'AI-First Code Editor',
    tagline: 'Visual file tree plus Agent mode so you can watch plugin.toml and Python entries take shape.',
    websiteUrl: 'https://www.cursor.com/',
    websiteLabel: 'Official site',
    steps: [
      { text: 'Open Cursor and click Open Folder on an empty workspace directory (or your N.E.K.O checkout).' },
      { text: 'Press Ctrl+I (or Cmd+I on macOS) and make sure the chat mode is set to Agent.' },
      {
        text: 'Copy the Setup Prompt below and send it in the Agent panel:',
        isPromptStep: true,
      },
      { text: 'Allow terminal commands for uv run neko-plugin and click Keep / Accept when checks pass.' },
      { text: 'Refresh and start the plugin in N.E.K.O, or import the packaged .neko-plugin in Plugin Center.' },
    ],
  },
  {
    id: 'trae',
    label: 'Trae',
    title: 'Trae',
    eyebrow: 'ByteDance · Free AI IDE with SOLO / Builder Mode',
    tagline: 'Beginner-friendly visual IDE with autonomous Builder and SOLO modes that run terminal commands for you.',
    websiteUrl: 'https://www.trae.ai/',
    websiteLabel: 'Official site',
    steps: [
      { text: 'Install Trae, sign in, and open an empty workspace folder (or your N.E.K.O checkout).' },
      { text: 'Switch the right-hand AI panel to Builder or SOLO mode.' },
      {
        text: 'Copy and send the Setup Prompt below:',
        isPromptStep: true,
      },
      { text: 'Approve terminal execution so Trae runs neko-plugin init and neko-plugin check, then test in N.E.K.O.' },
    ],
  },
  {
    id: 'vscode',
    label: 'VS Code (Cline / Copilot)',
    title: 'VS Code + Cline / Roo Code / GitHub Copilot',
    eyebrow: 'Use Your Existing VS Code',
    tagline: 'Install the open-source Cline extension (bring any API key) or switch GitHub Copilot Chat to Agent mode.',
    websiteUrl: 'https://code.visualstudio.com/',
    websiteLabel: 'Official site',
    steps: [
      { text: 'In VS Code, install Cline (or open GitHub Copilot Chat) and open an empty workspace folder.' },
      { text: 'Set Cline to Act mode, or switch GitHub Copilot Chat to Agent mode.' },
      {
        text: 'Paste the Setup Prompt below into the chat sidebar:',
        isPromptStep: true,
      },
      { text: 'Approve terminal commands until neko-plugin check reports 0 errors, then run or import in N.E.K.O.' },
    ],
  },
  {
    id: 'qoder',
    label: 'Qoder',
    title: 'Qoder / Tongyi Lingma',
    eyebrow: 'Alibaba · Agentic IDE & JetBrains / VS Code Plugin',
    tagline: 'Repo-aware agentic IDE (Qoder) and IDE plugin with autonomous terminal & multi-file editing.',
    websiteUrl: 'https://qoder.com/',
    websiteLabel: 'Official site',
    steps: [
      { text: 'Open your workspace folder in Qoder IDE (or enable the Lingma Agent panel in VS Code / PyCharm).' },
      { text: 'Switch the assistant to Agent / Quest mode with file and terminal permissions enabled.' },
      {
        text: 'Copy and send the Setup Prompt below:',
        isPromptStep: true,
      },
      { text: 'Wait for neko-plugin init and neko-plugin check to succeed, then start or import the plugin in N.E.K.O.' },
    ],
  },
  {
    id: 'codebuddy',
    label: 'CodeBuddy',
    title: 'CodeBuddy',
    eyebrow: 'Tencent · Craft Agent IDE & Plugin',
    tagline: 'Standalone AI IDE and editor extension featuring Craft autonomous agent workflows.',
    websiteUrl: 'https://www.codebuddy.ai/',
    websiteLabel: 'Official site',
    steps: [
      { text: 'Open your workspace folder in CodeBuddy IDE (or open the CodeBuddy extension in your editor).' },
      { text: 'Switch to the Craft agent panel.' },
      {
        text: 'Copy and send the Setup Prompt below:',
        isPromptStep: true,
      },
      { text: 'Approve uv and neko-plugin commands, then verify your new plugin inside N.E.K.O.' },
    ],
  },
  {
    id: 'other',
    label: 'Other Agents',
    title: 'Other Coding Agents (Windsurf, OpenCode, Aider, ZCode, etc.)',
    eyebrow: 'Works with Any File + Terminal Agent',
    tagline: 'Any coding agent that can edit local files and run shell commands works with the exact same prompt.',
    websiteUrl: '/plugins/quick-start',
    websiteLabel: 'Manual CLI guide',
    steps: [
      { text: 'Open an empty folder (or an existing N.E.K.O checkout) in your agent with file and shell access enabled.' },
      {
        text: 'Copy and paste the universal Setup Prompt below so the agent drives neko-plugin CLI:',
        isPromptStep: true,
      },
      { text: 'Describe what you want your plugin to do and let the agent run neko-plugin check and pytest.' },
      { text: 'Run N.E.K.O from source or import the built .neko-plugin package in Plugin Center.' },
    ],
  },
]

const jaAgents: AgentConfig[] = [
  {
    id: 'codex',
    label: 'Codex',
    title: 'OpenAI Codex',
    eyebrow: 'OpenAI 公式 · デスクトップ版 & CLI',
    tagline: 'ChatGPT アカウントでログインするだけで、git・uv・neko-plugin の実行まで自動で任せられます。',
    websiteUrl: 'https://openai.com/codex/',
    websiteLabel: '公式サイト',
    walkthroughAgent: 'codex',
    steps: [
      {
        text: 'Codex デスクトップアプリを開くか、ターミナルで Codex CLI をインストールして起動します：',
        code: 'npm install -g @openai/codex && codex',
      },
      { text: '空の作業フォルダ（または既存の N.E.K.O リポジトリ）をワークスペースとして開きます。' },
      {
        text: '下の Setup Prompt をコピーして Codex に貼り付け、環境準備と neko-plugin CLI 操作を任せます：',
        isPromptStep: true,
      },
      { text: 'git / uv コマンドの実行を許可し、neko-plugin check が 0 error(s) で通るのを待ちます。' },
      { text: 'ソース版 N.E.K.O で起動確認するか、生成された .neko-plugin を Plugin Center から読み込みます。' },
    ],
  },
  {
    id: 'dsh',
    label: 'DeepSeek Harness',
    title: 'DeepSeek Harness (dsh)',
    eyebrow: 'オープンソース Agent · ブラウザ Web UI',
    tagline: 'DeepSeek 製のローカル Coding Agent。コマンド 1 行でブラウザ画面が立ち上がり直感的に操作できます。',
    websiteUrl: 'https://github.com/deepseek-ai/deepseek-harness',
    websiteLabel: '公式リポジトリ',
    walkthroughAgent: 'dsh',
    steps: [
      {
        text: 'Node.js (>= 24.9) を用意し、ターミナルからローカル Web UI を起動します：',
        code: 'npx @deepseek-ai/dsh web',
      },
      { text: 'ブラウザ画面の「Settings → Models」で DeepSeek などの API キーを入力して保存します。' },
      { text: '「Choose workspace」をクリックし、空の作業フォルダ（または既存の N.E.K.O フォルダ）を選択します。' },
      {
        text: '下の Setup Prompt をチャット欄に貼り付けて送信し、コマンド実行の確認が出たら承認します：',
        isPromptStep: true,
      },
      { text: 'dsh が neko-plugin check を通したら、案内に従って N.E.K.O で起動またはパッケージを導入します。' },
    ],
  },
  {
    id: 'claude-code',
    label: 'Claude Code',
    title: 'Claude Code',
    eyebrow: 'Anthropic 公式 · ターミナル / IDE 拡張',
    tagline: '複数ステップの CLI 操作や自動テスト修正に強く、neko-plugin ワークフローを安定して完走できます。',
    websiteUrl: 'https://docs.anthropic.com/en/docs/claude-code/overview',
    websiteLabel: '公式ドキュメント',
    steps: [
      {
        text: '作業フォルダでターミナルを開き、Claude Code をインストールして起動します：',
        code: 'npm install -g @anthropic-ai/claude-code && claude',
      },
      {
        text: '下の Setup Prompt をコピーして貼り付けます：',
        isPromptStep: true,
      },
      { text: 'git・uv sync・uv run neko-plugin の実行を許可し、作りたいプラグインを普通の言葉で伝えます。' },
      { text: 'check / lint / pytest が通ったら、N.E.K.O で起動するか .neko-plugin パッケージをインポートします。' },
    ],
  },
  {
    id: 'cursor',
    label: 'Cursor',
    title: 'Cursor',
    eyebrow: '定番 AI コードエディタ',
    tagline: 'ファイルツリーを見ながら右側の Agent パネルで開発を進められます。',
    websiteUrl: 'https://www.cursor.com/',
    websiteLabel: '公式サイト',
    steps: [
      { text: 'Cursor で「Open Folder」から空の作業フォルダ（または既存の N.E.K.O フォルダ）を開きます。' },
      { text: 'Ctrl+I（macOS は Cmd+I）でパネルを開き、モードが「Agent」になっていることを確認します。' },
      {
        text: '下の Setup Prompt をコピーして送信します：',
        isPromptStep: true,
      },
      { text: 'uv run neko-plugin の実行を許可し、チェック通過後に「Keep / Accept」で変更を保存して N.E.K.O で試します。' },
    ],
  },
  {
    id: 'trae',
    label: 'Trae',
    title: 'Trae',
    eyebrow: '無料 AI IDE · Builder / SOLO モード搭載',
    tagline: 'コマンド操作に不慣れな初心者でも使いやすい GUI ベースのエディタです。',
    websiteUrl: 'https://www.trae.ai/',
    websiteLabel: '公式サイト',
    steps: [
      { text: 'Trae をインストールし、空の作業フォルダ（または N.E.K.O フォルダ）を開きます。' },
      { text: '右側の AI パネルを「Builder」または「SOLO」モードに切り替えます。' },
      {
        text: '下の Setup Prompt を貼り付けて送信します：',
        isPromptStep: true,
      },
      { text: 'ターミナル実行を許可し、neko-plugin init と check が完了したら N.E.K.O で動作確認します。' },
    ],
  },
  {
    id: 'vscode',
    label: 'VS Code (Cline / Copilot)',
    title: 'VS Code + Cline / Roo Code / Copilot',
    eyebrow: '既存の VS Code をそのまま活用',
    tagline: 'Cline 拡張機能や GitHub Copilot の Agent モードですぐに始められます。',
    websiteUrl: 'https://code.visualstudio.com/',
    websiteLabel: '公式サイト',
    steps: [
      { text: 'VS Code に「Cline」を入れるか「GitHub Copilot Chat」を開き、空の作業フォルダを開きます。' },
      { text: 'Cline を「Act」モード（または Copilot Chat を「Agent」モード）にします。' },
      {
        text: '下の Setup Prompt をチャット欄に貼り付けて送信します：',
        isPromptStep: true,
      },
      { text: 'コマンド実行を承認し、neko-plugin check が通ったら N.E.K.O で起動またはインポートします。' },
    ],
  },
  {
    id: 'qoder',
    label: 'Qoder',
    title: 'Qoder',
    eyebrow: 'Alibaba · Agentic IDE',
    tagline: 'リポジトリ理解と自律タスク実行を備えた AI IDE です。',
    websiteUrl: 'https://qoder.com/',
    websiteLabel: '公式サイト',
    steps: [
      { text: 'Qoder で作業フォルダを開き、アシスタントを Agent / Quest モードに切り替えます。' },
      {
        text: '下の Setup Prompt をコピーして送信します：',
        isPromptStep: true,
      },
      { text: 'neko-plugin init と check の完了後、N.E.K.O で起動またはパッケージを読み込みます。' },
    ],
  },
  {
    id: 'codebuddy',
    label: 'CodeBuddy',
    title: 'CodeBuddy',
    eyebrow: 'Tencent · Craft エージェント搭載',
    tagline: 'スタンドアロン IDE とエディタ拡張の両方で使える自律型エージェントです。',
    websiteUrl: 'https://www.codebuddy.ai/',
    websiteLabel: '公式サイト',
    steps: [
      { text: 'CodeBuddy で作業フォルダを開き、「Craft」エージェントパネルを開きます。' },
      {
        text: '下の Setup Prompt をコピーして送信します：',
        isPromptStep: true,
      },
      { text: 'uv と neko-plugin コマンドの実行を承認し、完了後に N.E.K.O で確認します。' },
    ],
  },
  {
    id: 'other',
    label: 'その他の Agent',
    title: 'その他の Coding Agent（Windsurf / OpenCode / Aider など）',
    eyebrow: 'ファイル編集とシェル実行ができれば対応',
    tagline: 'ローカルファイルの読み書きとコマンド実行ができる AI ツールなら同じ手順で動きます。',
    websiteUrl: '/ja/plugins/quick-start',
    websiteLabel: '手動 CLI ガイド',
    steps: [
      { text: 'お使いの Agent で空のフォルダ（または既存の N.E.K.O リポジトリ）を開きます。' },
      {
        text: '下の共通 Setup Prompt をコピーして貼り付け、neko-plugin CLI の実行を任せます：',
        isPromptStep: true,
      },
      { text: '作りたい機能を伝え、neko-plugin check と pytest が通ったら N.E.K.O で試します。' },
    ],
  },
]

export const ONBOARDING_COPY: Record<DocsLocale, OnboardingCopy> = {
  'zh-CN': {
    prepBadge: '零基础推荐',
    prepText:
      '不需要提前学架构或手动敲一串命令：随便新建一个空文件夹，选好你顺手的 Coding Agent，粘贴下方「Setup Prompt」，AI 就会自动拉取环境并用官方 neko-plugin CLI 完成脚手架、写代码与自检。',
    tabHeading: '选择你正在使用的 Coding Agent',
    tabHint: '不局限于以下工具，任意能读写本地文件并执行终端命令的编程智能体均可使用',
    stepsHeading: '开始步骤',
    walkthroughBadge: '含完整截图演示',
    promptStepTitle: 'N.E.K.O 插件 Setup Prompt（专为 neko-plugin CLI 定制）',
    promptStepDesc: '内置环境检查、neko-plugin init/check/build 规范与最新 SDK 防踩坑护栏。',
    ideaLabel: '可选：用一句话写下你想做的插件（留空也能直接复制，粘进去后再跟 AI 聊）',
    ideaPlaceholder: '例如：做一个 water_reminder 喝水提醒插件，支持手动测试和让猫娘在对话里提醒我喝水',
    copyPrompt: '一键复制 Setup Prompt',
    copiedPrompt: '已复制 Setup Prompt',
    copyCode: '复制',
    copiedCode: '已复制',
    showPrompt: '查看完整 Prompt 内容',
    hidePrompt: '收起 Prompt 内容',
    walkthroughCtaTitle: '第一次用 Coding Agent，不确定界面怎么点？',
    walkthroughCtaDesc: '我们准备了 Codex 与 DeepSeek Harness (dsh) 从空文件夹到插件跑起来的完整分步图文演示。',
    walkthroughCtaCodex: '查看 Codex 图文演示',
    walkthroughCtaDsh: '查看 DeepSeek Harness 图文演示',
    fallbacks: [
      {
        title: '电脑上还没有安装 Git 或 uv？点此查看一行安装命令',
        intro: 'neko-plugin CLI 需要 Git 拉取仓库、用 uv 自动管理独立的 Python 环境（无需手动配 Python 版本）：',
        items: [
          {
            label: 'Windows (PowerShell) 安装 uv',
            description: '打开 PowerShell 粘贴回车，装完重启一次终端即可：',
            code: 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"',
          },
          {
            label: 'macOS / Linux 安装 uv',
            description: '打开终端粘贴回车：',
            code: 'curl -LsSf https://astral.sh/uv/install.sh | sh',
          },
          {
            label: 'Windows 安装 Git（或前往 git-scm.com 下载安装包）',
            description: 'Windows 10/11 可直接在终端用 winget 安装：',
            code: 'winget install --id Git.Git -e --source winget',
          },
        ],
      },
      {
        title: '我平时用的是 Steam 版 / 桌面安装版 N.E.K.O，怎么加载 AI 做好的插件？',
        intro: 'Agent 在 N.E.K.O 源码目录用 neko-plugin 开发校验完后，你有两种方式放进日常使用的桌面版里：',
        items: [
          {
            label: '方式一：打包成 .neko-plugin 导入（最省心）',
            description: '让 Agent 执行下方打包命令，然后在桌面版 N.E.K.O 的「插件中心」导入生成的 .neko-plugin 文件：',
            code: 'uv run neko-plugin build <plugin_id> --out target/<plugin_id>.neko-plugin',
          },
          {
            label: '方式二：在「插件制作工作台」直接挂载目录（适合反复改代码实时重载）',
            description:
              '打开桌面版 N.E.K.O 插件管理器左侧的「插件制作工作台」→ 点击「添加已有项目」→ 选中 Agent 创建的 N.E.K.O/plugin/plugins/<plugin_id> 文件夹，之后每次让 AI 改完代码点一下「重载」即可生效。',
          },
        ],
      },
    ],
    agents: zhAgents,
  },
  en: {
    prepBadge: 'Beginner Friendly',
    prepText:
      'No need to memorize SDK internals or run scaffolding commands by hand: open an empty folder in your favorite coding agent, paste the Setup Prompt below, and let the agent drive git, uv, and the official neko-plugin CLI end-to-end.',
    tabHeading: 'Choose your coding agent',
    tabHint: 'Works with any coding agent capable of editing local files and running terminal commands',
    stepsHeading: 'Setup steps',
    walkthroughBadge: 'Step-by-step screenshots available',
    promptStepTitle: 'N.E.K.O Plugin Setup Prompt (built around neko-plugin CLI)',
    promptStepDesc: 'Includes repo setup, neko-plugin init/check/build commands, and current SDK guardrails.',
    ideaLabel: 'Optional: describe your plugin idea in one sentence (or leave blank and chat after pasting)',
    ideaPlaceholder: 'e.g. Build a water_reminder plugin with a test entry and a conversation tool for hydration reminders',
    copyPrompt: 'Copy Setup Prompt',
    copiedPrompt: 'Copied Setup Prompt',
    copyCode: 'Copy',
    copiedCode: 'Copied',
    showPrompt: 'Preview full prompt',
    hidePrompt: 'Hide full prompt',
    walkthroughCtaTitle: 'New to coding agents and want to see every screen?',
    walkthroughCtaDesc: 'Follow our visual walkthroughs for OpenAI Codex and DeepSeek Harness (dsh) from an empty folder to a running plugin.',
    walkthroughCtaCodex: 'Open Codex Walkthrough',
    walkthroughCtaDsh: 'Open DeepSeek Harness Walkthrough',
    fallbacks: [
      {
        title: 'Missing Git or uv on your computer? One-line install commands',
        intro: 'neko-plugin CLI uses Git for repositories and uv to manage an isolated Python environment automatically:',
        items: [
          {
            label: 'Install uv on Windows (PowerShell)',
            description: 'Run in PowerShell, then reopen your terminal:',
            code: 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"',
          },
          {
            label: 'Install uv on macOS / Linux',
            description: 'Run in your terminal:',
            code: 'curl -LsSf https://astral.sh/uv/install.sh | sh',
          },
          {
            label: 'Install Git on Windows (or download from git-scm.com)',
            description: 'Install directly with winget on Windows 10/11:',
            code: 'winget install --id Git.Git -e --source winget',
          },
        ],
      },
      {
        title: 'Using the Steam or desktop installer build of N.E.K.O? How to load your plugin',
        intro: 'Once your agent validates the plugin with neko-plugin CLI, load it into your desktop app in either way:',
        items: [
          {
            label: 'Option 1: Build and import a .neko-plugin package (easiest)',
            description: 'Ask the agent to build the package, then import it from N.E.K.O Plugin Center:',
            code: 'uv run neko-plugin build <plugin_id> --out target/<plugin_id>.neko-plugin',
          },
          {
            label: 'Option 2: Register the folder in Plugin studio for fast reload',
            description:
              'In N.E.K.O Plugin Manager, open Plugin studio -> Add existing project -> select N.E.K.O/plugin/plugins/<plugin_id>. Click Reload whenever your agent saves new changes.',
          },
        ],
      },
    ],
    agents: enAgents,
  },
  ja: {
    prepBadge: '初心者おすすめ',
    prepText:
      '事前に SDK の詳細やコマンドを覚える必要はありません。空のフォルダをお好みの Coding Agent で開き、下の「Setup Prompt」を貼り付けるだけで、AI が git・uv・公式 neko-plugin CLI を使って雛形作成から実装・検証まで進めます。',
    tabHeading: '使用する Coding Agent を選択',
    tabHint: 'ここにあるツール以外でも、ファイル編集とターミナル実行ができる Agent なら利用できます',
    stepsHeading: '導入ステップ',
    walkthroughBadge: '画面付きガイドあり',
    promptStepTitle: 'N.E.K.O プラグイン Setup Prompt（neko-plugin CLI 対応）',
    promptStepDesc: '環境確認、neko-plugin init / check / build 手順、最新 SDK ルールを含んでいます。',
    ideaLabel: '任意：作りたいプラグインを 1 文で入力（空欄のままコピーして後で伝えても OK）',
    ideaPlaceholder: '例：手動テストと会話中のリマインドに対応した水分補給プラグイン water_reminder を作って',
    copyPrompt: 'Setup Prompt をコピー',
    copiedPrompt: 'コピーしました',
    copyCode: 'コピー',
    copiedCode: 'コピー済み',
    showPrompt: 'Prompt 全文を表示',
    hidePrompt: 'Prompt 全文を閉じる',
    walkthroughCtaTitle: '実際の操作画面を順番に見たい方へ',
    walkthroughCtaDesc: '空のフォルダから最初のプラグインが動くまでを、Codex と DeepSeek Harness (dsh) の画面付きで解説しています。',
    walkthroughCtaCodex: 'Codex 画面付きガイドへ',
    walkthroughCtaDsh: 'DeepSeek Harness ガイドへ',
    fallbacks: [
      {
        title: 'Git または uv がまだ入っていない場合（1 行インストールコマンド）',
        intro: 'neko-plugin CLI はリポジトリ取得に Git、独立した Python 環境の自動構築に uv を使用します：',
        items: [
          {
            label: 'Windows (PowerShell) で uv をインストール',
            description: 'PowerShell で実行後、ターミナルを開き直してください：',
            code: 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"',
          },
          {
            label: 'macOS / Linux で uv をインストール',
            description: 'ターミナルで実行します：',
            code: 'curl -LsSf https://astral.sh/uv/install.sh | sh',
          },
          {
            label: 'Windows で Git をインストール（または git-scm.com から取得）',
            description: 'Windows 10/11 なら winget で導入できます：',
            code: 'winget install --id Git.Git -e --source winget',
          },
        ],
      },
      {
        title: 'Steam 版 / デスクトップ版 N.E.K.O で作成したプラグインを使うには？',
        intro: 'Agent が neko-plugin CLI で検証を終えたあと、次のどちらかの方法で導入できます：',
        items: [
          {
            label: '方法 1：.neko-plugin パッケージを出力して読み込む（一番かんたん）',
            description: 'Agent に下のコマンドでパッケージ化してもらい、N.E.K.O の Plugin Center からインポートします：',
            code: 'uv run neko-plugin build <plugin_id> --out target/<plugin_id>.neko-plugin',
          },
          {
            label: '方法 2：Plugin studio にフォルダを登録して即リロードする',
            description:
              'プラグインマネージャー左側の「Plugin studio」→「Add existing project」から N.E.K.O/plugin/plugins/<plugin_id> を選択すると、編集後に Reload だけですぐ反映されます。',
          },
        ],
      },
    ],
    agents: jaAgents,
  },
}
