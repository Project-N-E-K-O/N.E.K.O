export type DocsLocale = 'en' | 'zh-CN' | 'ja'

export interface AgentStep {
  text: string
  isPromptStep?: boolean
}

export interface AgentTutorial {
  label: string
  url: string
  kind: 'docs' | 'video'
}

export interface AgentConfig {
  id: string
  label: string
  title: string
  eyebrow: string
  tagline: string
  websiteUrl: string
  websiteLabel: string
  tutorials?: AgentTutorial[]
  steps: AgentStep[]
}

export interface FallbackSection {
  title: string
  intro: string
  items: Array<{
    label: string
    description: string
  }>
}

export interface OnboardingCopy {
  prepBadge: string
  prepText: string
  tabHeading: string
  tabHint: string
  stepsHeading: string
  tutorialsHeading: string
  promptStepTitle: string
  promptStepDesc: string
  ideaLabel: string
  ideaPlaceholder: string
  copyPrompt: string
  copiedPrompt: string
  showPrompt: string
  hidePrompt: string
  tutorialsCtaTitle: string
  tutorialsCtaDesc: string
  tutorialsCtaLabel: string
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

    return `【重要规则】我完全不会使用终端/命令行。所有命令都由你自己在你的环境里执行；绝对不要让我打开终端、复制或运行任何命令。需要我做的只能是在你的界面里点「允许」，或在 N.E.K.O 界面上点按钮。汇报时不要贴命令让我执行。

你现在是我的 N.E.K.O 插件开发助手。请全程通过 N.E.K.O 官方 \`neko-plugin\` CLI 帮我创建、编写、校验并打包插件，并用简短通俗的话向我同步每一步进展（遇到需要安装的软件或耗时命令先告诉我）。

## 1. 环境检查与 N.E.K.O 源码仓库就绪
1. 在你自己的环境里检查 \`git --version\` 和 \`uv --version\`：
   - 若缺少任一工具，先用一句话告诉我你要安装什么，然后由你自己完成安装：Windows 上用 \`winget install --id Git.Git -e --source winget\` 安装 Git、用 \`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"\` 安装 uv；macOS / Linux 上用 \`curl -LsSf https://astral.sh/uv/install.sh | sh\` 安装 uv，Git 用 \`xcode-select --install\`（macOS）或系统包管理器安装。装完后在你自己的会话里刷新 PATH 并重新检查。不要跳过 \`uv\` 直接使用系统 \`pip\`。
   - 若确实无法自动安装，用大白话向我解释卡在哪里、我需要在系统设置里点什么——绝对不要把安装命令丢给我执行。
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

## 5. 运行验证与打包交付（全部由你完成，我只负责点按钮）
检查全部通过后，由你直接完成验证与交付——给我的说明只能是「在哪个界面点哪个按钮」级别的操作：
- 打包导入（默认推荐，适合 Steam / 桌面安装版 N.E.K.O 或分享给他人）：你自行运行 \`uv run neko-plugin build <plugin_id> --out target/<plugin_id>.neko-plugin\`，然后告诉我生成的文件在哪个文件夹，以及在 N.E.K.O「插件管理」页面点哪个按钮（「导入」→ 选择该文件）就能加载它（不要使用已禁用的 \`neko-plugin install\`）。
- 源码版直接运行（如果我用的是源码版 N.E.K.O）：你自行在后台启动 \`uv run python launcher.py\`（带上 \`NEKO_PLUGIN_HOT_RELOAD=true\` 开启自动热重载），然后告诉我在「插件管理」页面点「刷新 → 启动」（后续修改点「重载」）。

## 6. 我的插件需求
${requirementBlock}`
  }

  if (locale === 'ja') {
    const requirementBlock = trimmed
      ? `作成したいプラグインの要件は次のとおりです：\n「${trimmed}」\n\nまずステップ 1（git / uv の確認、N.E.K.O リポジトリの準備、\`uv run neko-plugin --help\` の確認）を完了し、規約に合った英小文字スネークケースの \`plugin_id\` を提案したうえで、\`uv run neko-plugin init\` でプロジェクトを作成し、実装とステップ 4 の検証まで進めてください。`
      : '具体的な要件はまだ入力していません。まずステップ 1（git / uv の確認、N.E.K.O リポジトリの準備、\`uv run neko-plugin --help\` の確認）を完了してから、作りたいプラグインの内容を 2〜3 文でやさしく質問してください（初心者向けの簡単なアイデア例を 3 つ挙げてください）。私の回答後に \`uv run neko-plugin init\` で作成を始めてください。'

    return `【重要ルール】私はターミナル / コマンドラインがまったく使えません。すべてのコマンドはあなた自身の環境で実行してください。私にターミナルを開かせたり、コマンドをコピー・実行させたりしないでください。私ができるのは、あなたの画面で「許可」を押すことと、N.E.K.O の画面でボタンを押すことだけです。報告のときに実行用のコマンドを貼り付けないでください。

あなたは私の N.E.K.O プラグイン開発アシスタントです。公式の \`neko-plugin\` CLI を使ってプラグインの作成・実装・検証・パッケージ化まで一貫してサポートし、各ステップをわかりやすく簡潔に共有してください（ツールのインストールや時間のかかるコマンドの前に一言知らせてください）。

## 1. 環境確認と N.E.K.O ソースリポジトリの準備
1. あなた自身の環境で \`git --version\` と \`uv --version\` を確認します：
   - 未インストールのものがあれば、何をインストールするか一言伝えてから、あなた自身でインストールを完了してください：Windows では \`winget install --id Git.Git -e --source winget\` で Git、\`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"\` で uv を導入。macOS / Linux では \`curl -LsSf https://astral.sh/uv/install.sh | sh\` で uv を導入し、Git は \`xcode-select --install\`（macOS）または OS のパッケージマネージャーで導入します。完了後、あなた自身のセッションで PATH を更新して再確認してください。\`uv\` を飛ばしてシステムの \`pip\` を直接使わないでください。
   - どうしても自動インストールできない場合は、何がブロックされているか、私が設定画面で何を押せばよいかをやさしい言葉で説明してください。インストールコマンドを私に渡さないでください。
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

## 5. 動作確認とパッケージ出力（すべてあなたが実行し、私はボタンを押すだけ）
チェック通過後は、あなた自身で検証と引き渡しまで完了してください。私への案内は「どの画面のどのボタンを押すか」だけにしてください：
- パッケージを出力して導入（既定のおすすめ。Steam / デスクトップ版 N.E.K.O や配布用）：あなた自身で \`uv run neko-plugin build <plugin_id> --out target/<plugin_id>.neko-plugin\` を実行し、生成されたファイルの場所と、N.E.K.O の「プラグイン管理」画面で押すべきボタンの順番（「インポート」→ ファイル選択）を教えてください（無効化されている \`neko-plugin install\` は使わない）。
- ソース版でそのまま起動（私がソース版 N.E.K.O を使っている場合）：あなた自身で \`uv run python launcher.py\` をバックグラウンド起動（自動ホットリロード用に \`NEKO_PLUGIN_HOT_RELOAD=true\` を付ける）し、「プラグイン管理」画面で「再読み込み → 起動」（変更後は「リロード」）を押すよう案内してください。

## 6. 作りたいプラグインの要件
${requirementBlock}`
  }

  const requirementBlock = trimmed
    ? `Here is the plugin I want to build:\n"${trimmed}"\n\nPlease complete Step 1 first (verify git/uv, prepare the N.E.K.O repository, and confirm \`uv run neko-plugin --help\` works), propose a valid lowercase snake_case \`plugin_id\`, scaffold it with \`uv run neko-plugin init\`, implement the feature, and pass all checks in Step 4.`
    : 'I have not typed my plugin idea yet. Please complete Step 1 first (verify git/uv, prepare the N.E.K.O repository, and confirm `uv run neko-plugin --help` works), then ask me in 2-3 friendly plain-English sentences what plugin I want to build for N.E.K.O (share 3 fun beginner-friendly examples for inspiration) before running `uv run neko-plugin init`.'

  return `[IMPORTANT RULE] I cannot use a terminal or command line at all. Run every command yourself inside your own environment; NEVER ask me to open a terminal, copy, or run any command. The only things you may ask me to do are clicking "Allow" in your own interface or clicking buttons in the N.E.K.O UI. Do not paste commands for me to execute in your reports.

You are my N.E.K.O plugin development assistant. Use the official \`neko-plugin\` CLI end-to-end to scaffold, implement, validate, and package my plugin, keeping me updated in plain, concise language (ask before installing system tools or running long downloads).

## 1. Check prerequisites and prepare the N.E.K.O repository
1. Verify \`git --version\` and \`uv --version\` in your own environment:
   - If either tool is missing, tell me in one sentence what you are about to install, then install it yourself: on Windows use \`winget install --id Git.Git -e --source winget\` for Git and \`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"\` for uv; on macOS / Linux use \`curl -LsSf https://astral.sh/uv/install.sh | sh\` for uv and \`xcode-select --install\` (macOS) or the system package manager for Git. Afterwards refresh PATH in your own session and re-check. Never bypass \`uv\` with bare system \`pip\`.
   - If you genuinely cannot install something yourself, explain in plain words what is blocked and which buttons I would need to click in system settings — never hand me install commands.
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

## 5. Run or package for delivery (you do everything — I only click buttons)
Once checks pass, finish verification and delivery yourself — any instructions you give me must be click-level only (which screen, which button):
- Build and import a package (recommended default; for Steam / desktop N.E.K.O installs or sharing): run \`uv run neko-plugin build <plugin_id> --out target/<plugin_id>.neko-plugin\` yourself, then tell me which folder the file is in and which buttons to click on the Plugins page in N.E.K.O Plugin Manager (Import, then pick the file) to load it (never use the disabled \`neko-plugin install\` command).
- Run directly from source (if I use a source checkout of N.E.K.O): start \`uv run python launcher.py\` yourself in the background (with \`NEKO_PLUGIN_HOT_RELOAD=true\` for automatic hot reload on save), then tell me to click Refresh -> Start on the Plugins page (and Reload after edits).

## 6. My plugin request
${requirementBlock}`
}

const zhAgents: AgentConfig[] = [
  {
    id: 'dsh',
    label: 'DeepSeek Harness',
    title: 'DeepSeek Harness 桌面版',
    eyebrow: 'DeepSeek 开源 · 官方桌面应用',
    tagline: 'DeepSeek 开源的插件化编程智能体，官方桌面应用开箱即用，界面直观好上手。',
    websiteUrl: 'https://github.com/deepseek-ai/deepseek-harness/releases',
    websiteLabel: '下载桌面版',
    tutorials: [
      {
        label: '官方 GitHub 仓库（README 与桌面版指南）',
        url: 'https://github.com/deepseek-ai/deepseek-harness',
        kind: 'docs',
      },
    ],
    steps: [
      { text: '从官方发布页下载并安装 DeepSeek Harness 桌面版，打开应用。' },
      { text: '打开「Settings → Models」，填入你的 DeepSeek API Key 并保存。' },
      { text: '点击「Choose workspace」，选择任意空工作文件夹（或已有的 N.E.K.O 源码目录）。' },
      {
        text: '复制下方 Setup Prompt 粘贴进会话框发送，AI 请求执行操作时点击「Approve / 允许」：',
        isPromptStep: true,
      },
      { text: '等 AI 自动跑通检查后，按它给出的提示在 N.E.K.O 界面上点按钮加载插件。' },
    ],
  },
  {
    id: 'codex',
    label: 'Codex',
    title: 'OpenAI Codex',
    eyebrow: 'OpenAI 官方 · 桌面应用',
    tagline: '登录 ChatGPT 账号即可让 AI 自动完成环境准备、代码编写与自检全流程。',
    websiteUrl: 'https://openai.com/codex/',
    websiteLabel: '官方网站',
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
    steps: [
      { text: '从官网下载安装 Codex 桌面应用，用 ChatGPT 账号登录。' },
      { text: '在 Codex 中打开一个空工作文件夹（或你电脑上已有的 N.E.K.O 源码目录）。' },
      {
        text: '一键复制下方 Setup Prompt 粘贴给 Codex 发送，让它自动准备环境并调用官方 neko-plugin 工具：',
        isPromptStep: true,
      },
      { text: '当 Codex 请求执行操作时点击允许，等待它自动写完代码并通过检查。' },
      { text: '按 Codex 给出的说明在 N.E.K.O「插件管理」页面点「刷新 → 启动」，或点「导入」选择 .neko-plugin 安装包。' },
    ],
  },
  {
    id: 'trae',
    label: 'Trae',
    title: 'Trae',
    eyebrow: '字节跳动 · 免费 AI IDE · 国内直连',
    tagline: '图形化代码编辑器，内置 Builder / SOLO 全流程模式，对零基础新手非常友好。',
    websiteUrl: 'https://www.trae.cn/',
    websiteLabel: '官方网站',
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
    steps: [
      { text: '从官网下载并安装 Trae 客户端，登录后点击「打开文件夹」选一个空文件夹（或 N.E.K.O 目录）。' },
      { text: '在右侧 AI 对话面板顶部切换到「Builder」或「SOLO」智能体模式。' },
      {
        text: '复制下方 Setup Prompt 粘贴进右侧输入框发送：',
        isPromptStep: true,
      },
      { text: 'AI 请求执行操作时点击「运行 / 允许」，等待 Trae 自动完成插件初始化与自检。' },
      { text: '在 N.E.K.O「插件管理」页面点「刷新 → 启动」，或点「导入」选择生成的 .neko-plugin 安装包体验效果。' },
    ],
  },
  {
    id: 'claude-code',
    label: 'Claude Code',
    title: 'Claude Code',
    eyebrow: 'Anthropic 官方 · Claude 桌面应用',
    tagline: '擅长多步工具链执行与自主排错，能非常稳定地跑完插件初始化、测试与打包全流程。',
    websiteUrl: 'https://claude.ai/download',
    websiteLabel: '官方网站',
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
    steps: [
      { text: '下载安装 Claude 桌面应用并登录，切换到「Code」标签页打开你的工作文件夹。' },
      {
        text: '复制下方 Setup Prompt 粘贴到对话框中发送：',
        isPromptStep: true,
      },
      { text: 'AI 请求执行操作时点击允许，按提示用大白话补充你的插件想法。' },
      { text: '等待它跑完检查与测试后，在 N.E.K.O 中启动插件或导入打包产物。' },
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
    tutorials: [
      {
        label: '官方 Quickstart（英文）',
        url: 'https://cursor.com/docs/get-started/quickstart',
        kind: 'docs',
      },
    ],
    steps: [
      { text: '打开 Cursor，点击「Open Folder」打开一个空工作文件夹（或已有的 N.E.K.O 源码目录）。' },
      { text: '按 Ctrl+I（macOS 按 Cmd+I）唤出右侧面板，确认左下角模式已设为「Agent」。' },
      {
        text: '复制下方 Setup Prompt 粘贴进输入框发送：',
        isPromptStep: true,
      },
      { text: 'AI 请求执行操作时点击允许，完成后点击「Keep / Accept」保存文件改动。' },
      { text: '按提示在 N.E.K.O「插件管理」页点「刷新 → 启动」，或点「导入」选择生成的 .neko-plugin 包。' },
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
    steps: [
      { text: '打开 Qoder IDE（或在 VS Code / PyCharm 中安装并登录「通义灵码」扩展），打开你的工作文件夹。' },
      { text: '在对话侧边栏切换到「智能体 / Agent（或 Quest）」模式，允许它读写文件并执行操作。' },
      {
        text: '复制下方 Setup Prompt 粘贴发送：',
        isPromptStep: true,
      },
      { text: '等待智能体自动创建项目并跑完检查后，在 N.E.K.O 中加载验证。' },
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
    steps: [
      { text: '下载安装 CodeBuddy IDE（或在现有编辑器安装腾讯云代码助手 CodeBuddy 插件），打开工作文件夹。' },
      { text: '进入侧边栏「Craft 智能体」对话面板。' },
      {
        text: '复制下方 Setup Prompt 粘贴进对话框发送：',
        isPromptStep: true,
      },
      { text: 'AI 请求执行操作时点击允许，待检查全部通过后在 N.E.K.O 中启动或导入插件包。' },
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
    steps: [
      { text: '在 VS Code 扩展市场安装「Cline」（填任意模型 API Key）或打开内置「GitHub Copilot Chat」。' },
      { text: '打开空工作文件夹，将 Cline 设为「Act」模式（或将 Copilot Chat 切换为「Agent」模式）。' },
      {
        text: '复制下方 Setup Prompt 粘贴进侧边栏聊天框发送：',
        isPromptStep: true,
      },
      { text: 'AI 每次请求执行操作时点击允许，等待检查全部通过后在 N.E.K.O 中体验。' },
    ],
  },
  {
    id: 'other',
    label: '其他 Agent',
    title: '其他 Coding Agent（OpenCode / Windsurf / ZCode / Aider 等）',
    eyebrow: '通用兼容 · 能在你的电脑上读写文件并执行操作即可',
    tagline: '无论是智谱 ZCode、Windsurf、OpenCode、Aider 还是 OpenClaw / Hermes，接入方式完全相同。',
    websiteUrl: '/zh-CN/plugins/quick-start',
    websiteLabel: '手动开发教程',
    steps: [
      { text: '在你的 AI 编程工具中打开一个空文件夹（或已有的 N.E.K.O 仓库目录），允许它读写文件并执行操作。' },
      {
        text: '一键复制下方通用 Setup Prompt 粘贴给 Agent，让它接管环境准备与 neko-plugin 工具调用：',
        isPromptStep: true,
      },
      { text: '告诉 Agent 你的插件点子，等待它完成编码并通过检查。' },
      { text: '按 Agent 的提示在 N.E.K.O「插件管理」页点按钮加载，或让它生成 .neko-plugin 安装包后点「导入」。' },
    ],
  },
]

const enAgents: AgentConfig[] = [
  {
    id: 'codex',
    label: 'Codex',
    title: 'OpenAI Codex',
    eyebrow: 'Official OpenAI Agent · Desktop App',
    tagline: 'Sign in with ChatGPT in the Codex desktop app and let it handle setup, coding, and checks for you.',
    websiteUrl: 'https://openai.com/codex/',
    websiteLabel: 'Official site',
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
    steps: [
      { text: 'Download the Codex desktop app from the official site and sign in with your ChatGPT account.' },
      { text: 'Point Codex at an empty workspace folder (or your existing N.E.K.O repository folder).' },
      {
        text: 'Copy the Setup Prompt below and paste it into Codex so it prepares the repo and the neko-plugin tool:',
        isPromptStep: true,
      },
      { text: 'Click Allow whenever Codex asks to run something, and wait for it to pass the plugin checks.' },
      { text: 'Follow Codex\'s clicks-only instructions to start the plugin in N.E.K.O or click Import on the Plugins page to load the .neko-plugin package.' },
    ],
  },
  {
    id: 'dsh',
    label: 'DeepSeek Harness',
    title: 'DeepSeek Harness Desktop',
    eyebrow: 'Open-Source Agent · Official Desktop App',
    tagline: 'An MIT-licensed local coding agent from DeepSeek, now available as a ready-to-use desktop app.',
    websiteUrl: 'https://github.com/deepseek-ai/deepseek-harness/releases',
    websiteLabel: 'Download app',
    tutorials: [
      {
        label: 'Official GitHub repo (README & desktop app guide)',
        url: 'https://github.com/deepseek-ai/deepseek-harness',
        kind: 'docs',
      },
    ],
    steps: [
      { text: 'Download and install DeepSeek Harness Desktop from the official releases page, then open it.' },
      { text: 'Open Settings -> Models, enter your DeepSeek or compatible API key, and save.' },
      { text: 'Click Choose workspace and select an empty workspace folder (or an existing N.E.K.O checkout).' },
      {
        text: 'Paste the Setup Prompt below into the session box and click Approve when the AI asks to run something:',
        isPromptStep: true,
      },
      { text: 'Once the AI finishes the plugin checks, follow its clicks-only instructions to load the plugin in N.E.K.O.' },
    ],
  },
  {
    id: 'claude-code',
    label: 'Claude Code',
    title: 'Claude Code',
    eyebrow: 'Anthropic Official · Claude Desktop App',
    tagline: 'Strong multi-step execution that reliably scaffolds, tests, and packages plugins end-to-end.',
    websiteUrl: 'https://claude.ai/download',
    websiteLabel: 'Official site',
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
    steps: [
      { text: 'Install the Claude desktop app, sign in, and open your workspace folder from the Code tab.' },
      {
        text: 'Copy and paste the Setup Prompt below into the chat:',
        isPromptStep: true,
      },
      { text: 'Click Allow when the AI asks to run something, then describe your plugin idea in plain English.' },
      { text: 'After all checks and tests pass, follow its clicks-only steps to load the plugin in N.E.K.O.' },
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
    tutorials: [
      {
        label: 'Official Cursor Quickstart',
        url: 'https://cursor.com/docs/get-started/quickstart',
        kind: 'docs',
      },
    ],
    steps: [
      { text: 'Open Cursor and click Open Folder on an empty workspace directory (or your N.E.K.O checkout).' },
      { text: 'Press Ctrl+I (or Cmd+I on macOS) and make sure the chat mode is set to Agent.' },
      {
        text: 'Copy the Setup Prompt below and send it in the Agent panel:',
        isPromptStep: true,
      },
      { text: 'Click Allow when the AI asks to run something, and click Keep / Accept when it finishes.' },
      { text: 'Refresh and start the plugin in N.E.K.O, or click Import on the Plugins page to load the packaged .neko-plugin.' },
    ],
  },
  {
    id: 'trae',
    label: 'Trae',
    title: 'Trae',
    eyebrow: 'ByteDance · Free AI IDE with SOLO / Builder Mode',
    tagline: 'Beginner-friendly visual IDE with autonomous Builder and SOLO modes that handle everything for you.',
    websiteUrl: 'https://www.trae.ai/',
    websiteLabel: 'Official site',
    tutorials: [
      {
        label: 'Official docs: Get started with TRAE Desktop',
        url: 'https://docs.trae.ai/ide/get-started-with-trae-desktop',
        kind: 'docs',
      },
    ],
    steps: [
      { text: 'Install Trae, sign in, and open an empty workspace folder (or your N.E.K.O checkout).' },
      { text: 'Switch the right-hand AI panel to Builder or SOLO mode.' },
      {
        text: 'Copy and send the Setup Prompt below:',
        isPromptStep: true,
      },
      { text: 'Click Allow when Trae asks to run something, then load the finished plugin in N.E.K.O.' },
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
    steps: [
      { text: 'In VS Code, install Cline (or open GitHub Copilot Chat) and open an empty workspace folder.' },
      { text: 'Set Cline to Act mode, or switch GitHub Copilot Chat to Agent mode.' },
      {
        text: 'Paste the Setup Prompt below into the chat sidebar:',
        isPromptStep: true,
      },
      { text: 'Click Allow each time the AI asks to run something, then start or import the plugin in N.E.K.O.' },
    ],
  },
  {
    id: 'qoder',
    label: 'Qoder',
    title: 'Qoder / Tongyi Lingma',
    eyebrow: 'Alibaba · Agentic IDE & JetBrains / VS Code Plugin',
    tagline: 'Repo-aware agentic IDE (Qoder) and IDE plugin with autonomous multi-step editing.',
    websiteUrl: 'https://qoder.com/',
    websiteLabel: 'Official site',
    tutorials: [
      {
        label: 'Official docs: Qoder Quick Start',
        url: 'https://docs.qoder.com/product-series/quick-start',
        kind: 'docs',
      },
    ],
    steps: [
      { text: 'Open your workspace folder in Qoder IDE (or enable the Lingma Agent panel in VS Code / PyCharm).' },
      { text: 'Switch the assistant to Agent / Quest mode and allow it to edit files and run actions.' },
      {
        text: 'Copy and send the Setup Prompt below:',
        isPromptStep: true,
      },
      { text: 'Wait for the agent to finish scaffolding and checks, then start or import the plugin in N.E.K.O.' },
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
    steps: [
      { text: 'Open your workspace folder in CodeBuddy IDE (or open the CodeBuddy extension in your editor).' },
      { text: 'Switch to the Craft agent panel.' },
      {
        text: 'Copy and send the Setup Prompt below:',
        isPromptStep: true,
      },
      { text: 'Click Allow when the agent asks to run something, then verify your new plugin inside N.E.K.O.' },
    ],
  },
  {
    id: 'other',
    label: 'Other Agents',
    title: 'Other Coding Agents (Windsurf, OpenCode, Aider, ZCode, etc.)',
    eyebrow: 'Any AI coding tool that can edit files and run actions on your computer',
    tagline: 'Windsurf, OpenCode, Aider, ZCode, OpenClaw / Hermes — they all follow the exact same flow.',
    websiteUrl: '/plugins/quick-start',
    websiteLabel: 'Manual dev guide',
    steps: [
      { text: 'Open an empty folder (or an existing N.E.K.O checkout) in your agent and allow it to edit files and run actions.' },
      {
        text: 'Copy and paste the universal Setup Prompt below so the agent handles setup and the neko-plugin tool:',
        isPromptStep: true,
      },
      { text: 'Describe what you want your plugin to do and let the agent finish coding and checks.' },
      { text: 'Follow the agent\'s clicks-only instructions to start the plugin in N.E.K.O or import the .neko-plugin package.' },
    ],
  },
]

const jaAgents: AgentConfig[] = [
  {
    id: 'codex',
    label: 'Codex',
    title: 'OpenAI Codex',
    eyebrow: 'OpenAI 公式 · デスクトップアプリ',
    tagline: 'ChatGPT アカウントでログインするだけで、環境準備から実装・チェックまで自動で任せられます。',
    websiteUrl: 'https://openai.com/codex/',
    websiteLabel: '公式サイト',
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
    steps: [
      { text: '公式サイトから Codex デスクトップアプリをインストールし、ChatGPT アカウントでログインします。' },
      { text: '空の作業フォルダ（または既存の N.E.K.O リポジトリ）をワークスペースとして開きます。' },
      {
        text: '下の Setup Prompt をコピーして Codex に貼り付け、環境準備と neko-plugin ツールの操作を任せます：',
        isPromptStep: true,
      },
      { text: 'AI が操作の実行を求めてきたら「許可」を押し、チェック完了を待ちます。' },
      { text: '案内に従って N.E.K.O の「プラグイン管理」でプラグインを起動するか、「インポート」から .neko-plugin を読み込みます。' },
    ],
  },
  {
    id: 'dsh',
    label: 'DeepSeek Harness',
    title: 'DeepSeek Harness デスクトップ版',
    eyebrow: 'DeepSeek 製オープンソース · 公式デスクトップアプリ',
    tagline: 'DeepSeek 製のローカル Coding Agent。公式デスクトップアプリをインストールするだけで使えます。',
    websiteUrl: 'https://github.com/deepseek-ai/deepseek-harness/releases',
    websiteLabel: 'アプリをダウンロード',
    tutorials: [
      {
        label: '公式 GitHub リポジトリ（README とデスクトップ版ガイド）',
        url: 'https://github.com/deepseek-ai/deepseek-harness',
        kind: 'docs',
      },
    ],
    steps: [
      { text: '公式リリースページから DeepSeek Harness デスクトップ版をダウンロード・インストールして開きます。' },
      { text: '「Settings → Models」で DeepSeek などの API キーを入力して保存します。' },
      { text: '「Choose workspace」をクリックし、空の作業フォルダ（または既存の N.E.K.O フォルダ）を選択します。' },
      {
        text: '下の Setup Prompt をチャット欄に貼り付けて送信し、AI が操作の実行を求めてきたら「Approve」を押します：',
        isPromptStep: true,
      },
      { text: 'チェックが終わったら、案内に従って N.E.K.O の画面でボタンを押してプラグインを読み込みます。' },
    ],
  },
  {
    id: 'claude-code',
    label: 'Claude Code',
    title: 'Claude Code',
    eyebrow: 'Anthropic 公式 · Claude デスクトップアプリ',
    tagline: '複数ステップの自律実行や自動テスト修正に強く、プラグイン開発を安定して完走できます。',
    websiteUrl: 'https://claude.ai/download',
    websiteLabel: '公式サイト',
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
    steps: [
      { text: 'Claude デスクトップアプリをインストールしてログインし、「Code」タブで作業フォルダを開きます。' },
      {
        text: '下の Setup Prompt をコピーして貼り付けます：',
        isPromptStep: true,
      },
      { text: 'AI が操作の実行を求めてきたら許可し、作りたいプラグインを普通の言葉で伝えます。' },
      { text: 'チェックとテストが通ったら、N.E.K.O で起動するか .neko-plugin パッケージをインポートします。' },
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
    tutorials: [
      {
        label: '公式 Quickstart（英語）',
        url: 'https://cursor.com/docs/get-started/quickstart',
        kind: 'docs',
      },
    ],
    steps: [
      { text: 'Cursor で「Open Folder」から空の作業フォルダ（または既存の N.E.K.O フォルダ）を開きます。' },
      { text: 'Ctrl+I（macOS は Cmd+I）でパネルを開き、モードが「Agent」になっていることを確認します。' },
      {
        text: '下の Setup Prompt をコピーして送信します：',
        isPromptStep: true,
      },
      { text: 'AI が操作の実行を求めてきたら許可し、完了後に「Keep / Accept」で変更を保存して N.E.K.O で試します。' },
    ],
  },
  {
    id: 'trae',
    label: 'Trae',
    title: 'Trae',
    eyebrow: '無料 AI IDE · Builder / SOLO モード搭載',
    tagline: '初心者でも使いやすい GUI ベースのエディタです。',
    websiteUrl: 'https://www.trae.ai/',
    websiteLabel: '公式サイト',
    tutorials: [
      {
        label: '公式ドキュメント：TRAE Desktop をはじめる（英語）',
        url: 'https://docs.trae.ai/ide/get-started-with-trae-desktop',
        kind: 'docs',
      },
    ],
    steps: [
      { text: 'Trae をインストールし、空の作業フォルダ（または N.E.K.O フォルダ）を開きます。' },
      { text: '右側の AI パネルを「Builder」または「SOLO」モードに切り替えます。' },
      {
        text: '下の Setup Prompt を貼り付けて送信します：',
        isPromptStep: true,
      },
      { text: 'AI が操作の実行を求めてきたら許可し、初期化とチェックが完了したら N.E.K.O で動作確認します。' },
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
    steps: [
      { text: 'VS Code に「Cline」を入れるか「GitHub Copilot Chat」を開き、空の作業フォルダを開きます。' },
      { text: 'Cline を「Act」モード（または Copilot Chat を「Agent」モード）にします。' },
      {
        text: '下の Setup Prompt をチャット欄に貼り付けて送信します：',
        isPromptStep: true,
      },
      { text: 'AI が操作の実行を求めてきたら許可し、チェックが通ったら N.E.K.O で起動またはインポートします。' },
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
    tutorials: [
      {
        label: '公式ドキュメント：Qoder Quick Start（英語）',
        url: 'https://docs.qoder.com/product-series/quick-start',
        kind: 'docs',
      },
    ],
    steps: [
      { text: 'Qoder で作業フォルダを開き、アシスタントを Agent / Quest モードに切り替えて操作を許可します。' },
      {
        text: '下の Setup Prompt をコピーして送信します：',
        isPromptStep: true,
      },
      { text: 'プラグイン作成とチェックの完了後、N.E.K.O で起動またはパッケージを読み込みます。' },
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
    steps: [
      { text: 'CodeBuddy で作業フォルダを開き、「Craft」エージェントパネルを開きます。' },
      {
        text: '下の Setup Prompt をコピーして送信します：',
        isPromptStep: true,
      },
      { text: 'AI が操作の実行を求めてきたら許可し、完了後に N.E.K.O で確認します。' },
    ],
  },
  {
    id: 'other',
    label: 'その他の Agent',
    title: 'その他の Coding Agent（Windsurf / OpenCode / Aider など）',
    eyebrow: 'ファイル編集と操作の実行ができる AI ツールなら対応',
    tagline: 'ローカルファイルの読み書きと操作の実行ができる AI ツールなら同じ手順で動きます。',
    websiteUrl: '/ja/plugins/quick-start',
    websiteLabel: '手動開発ガイド',
    steps: [
      { text: 'お使いの Agent で空のフォルダ（または既存の N.E.K.O リポジトリ）を開き、ファイル編集と操作の実行を許可します。' },
      {
        text: '下の共通 Setup Prompt をコピーして貼り付け、環境準備と neko-plugin ツールの操作を任せます：',
        isPromptStep: true,
      },
      { text: '作りたい機能を伝え、コーディングとチェックが済んだら N.E.K.O で試します。' },
    ],
  },
]

export const ONBOARDING_COPY: Record<DocsLocale, OnboardingCopy> = {
  'zh-CN': {
    prepBadge: '零基础推荐',
    prepText:
      '不需要提前学架构或装环境：随便新建一个空文件夹，选好你顺手的 Coding Agent，粘贴下方「Setup Prompt」，AI 就会自动准备环境并用官方 neko-plugin 工具完成脚手架、写代码与自检。',
    tabHeading: '选择你正在使用的 Coding Agent',
    tabHint: '不局限于以下工具，任意能在你的电脑上读写文件并执行操作的 AI 编程工具均可使用',
    stepsHeading: '开始步骤',
    tutorialsHeading: '官方上手教程',
    promptStepTitle: 'N.E.K.O 插件 Setup Prompt（专为 neko-plugin CLI 定制）',
    promptStepDesc: '内置环境检查、neko-plugin init/check/build 规范与最新 SDK 防踩坑护栏。',
    ideaLabel: '可选：用一句话写下你想做的插件（留空也能直接复制，粘进去后再跟 AI 聊）',
    ideaPlaceholder: '例如：做一个 water_reminder 喝水提醒插件，支持手动测试和让猫娘在对话里提醒我喝水',
    copyPrompt: '一键复制 Setup Prompt',
    copiedPrompt: '已复制 Setup Prompt',
    showPrompt: '查看完整 Prompt 内容',
    hidePrompt: '收起 Prompt 内容',
    tutorialsCtaTitle: '想看每个 Agent 的官方上手教程？',
    tutorialsCtaDesc: '我们汇总了全部支持工具的官方入门文档，并附上 N.E.K.O 插件管理的真实界面截图。',
    tutorialsCtaLabel: '打开各 Agent 上手教程',
    fallbacks: [
      {
        title: '我平时用的是 Steam 版 / 桌面安装版 N.E.K.O，怎么加载 AI 做好的插件？',
        intro: 'AI 在校验通过后可以直接帮你生成 .neko-plugin 安装包，你在日常使用的桌面版里点几下按钮就能加载：',
        items: [
          {
            label: '方式一：导入 .neko-plugin 安装包（最省心）',
            description:
              '让 AI 生成 .neko-plugin 安装包并告诉你文件位置，然后打开桌面版 N.E.K.O 的「插件管理」，点右上「导入」选中该文件即可。',
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
      'No need to memorize SDK internals or set anything up yourself: open an empty folder in your favorite coding agent, paste the Setup Prompt below, and let the agent drive git, uv, and the official neko-plugin tool end-to-end.',
    tabHeading: 'Choose your coding agent',
    tabHint: 'Works with any AI coding tool that can edit files and run actions on your computer',
    stepsHeading: 'Setup steps',
    tutorialsHeading: 'Official tutorials',
    promptStepTitle: 'N.E.K.O Plugin Setup Prompt (built around neko-plugin CLI)',
    promptStepDesc: 'Includes repo setup, neko-plugin init/check/build commands, and current SDK guardrails.',
    ideaLabel: 'Optional: describe your plugin idea in one sentence (or leave blank and chat after pasting)',
    ideaPlaceholder: 'e.g. Build a water_reminder plugin with a test entry and a conversation tool for hydration reminders',
    copyPrompt: 'Copy Setup Prompt',
    copiedPrompt: 'Copied Setup Prompt',
    showPrompt: 'Preview full prompt',
    hidePrompt: 'Hide full prompt',
    tutorialsCtaTitle: 'Want the official getting-started guides?',
    tutorialsCtaDesc: 'Official tutorials for every supported agent, plus real screenshots of the N.E.K.O plugin manager.',
    tutorialsCtaLabel: 'Open agent tutorials',
    fallbacks: [
      {
        title: 'Using the Steam or desktop installer build of N.E.K.O? How to load your plugin',
        intro: 'Once the AI finishes its checks it can produce a .neko-plugin package for you — loading it into your desktop app is just a few clicks:',
        items: [
          {
            label: 'Option 1: Import the .neko-plugin package (easiest)',
            description:
              'Let the agent build the .neko-plugin package and tell you where the file is, then open the Plugins page in N.E.K.O Plugin Manager, click Import, and pick that file.',
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
      '事前に SDK の詳細や環境構築を覚える必要はありません。空のフォルダをお好みの Coding Agent で開き、下の「Setup Prompt」を貼り付けるだけで、AI が環境準備から公式 neko-plugin ツールを使った雛形作成・実装・検証まで進めます。',
    tabHeading: '使用する Coding Agent を選択',
    tabHint: 'ここにあるツール以外でも、パソコン上でファイル編集と操作の実行ができる AI ツールなら利用できます',
    stepsHeading: '導入ステップ',
    tutorialsHeading: '公式チュートリアル',
    promptStepTitle: 'N.E.K.O プラグイン Setup Prompt（neko-plugin CLI 対応）',
    promptStepDesc: '環境確認、neko-plugin init / check / build 手順、最新 SDK ルールを含んでいます。',
    ideaLabel: '任意：作りたいプラグインを 1 文で入力（空欄のままコピーして後で伝えても OK）',
    ideaPlaceholder: '例：手動テストと会話中のリマインドに対応した水分補給プラグイン water_reminder を作って',
    copyPrompt: 'Setup Prompt をコピー',
    copiedPrompt: 'コピーしました',
    showPrompt: 'Prompt 全文を表示',
    hidePrompt: 'Prompt 全文を閉じる',
    tutorialsCtaTitle: '各 Agent の公式チュートリアルを見たい方へ',
    tutorialsCtaDesc: '対応ツールすべての公式入門ガイドと、N.E.K.O プラグイン管理の実際の画面スクリーンショットをまとめています。',
    tutorialsCtaLabel: 'エージェント別チュートリアルを開く',
    fallbacks: [
      {
        title: 'Steam 版 / デスクトップ版 N.E.K.O で作成したプラグインを使うには？',
        intro: 'AI がチェックを終えたあと .neko-plugin パッケージを作ってくれるので、お使いのデスクトップ版ではボタンを押すだけで導入できます：',
        items: [
          {
            label: '方法 1：.neko-plugin パッケージを読み込む（一番かんたん）',
            description:
              'AI に .neko-plugin パッケージを作ってもらい、場所を教えてもらったら、N.E.K.O の「プラグイン管理」で「インポート」を押してそのファイルを選ぶだけです。',
          },
          {
            label: '方法 2：「プラグイン制作」にフォルダを登録して即リロードする',
            description:
              'プラグインマネージャー左側の「プラグイン制作」→「既存のプロジェクトを追加」から N.E.K.O/plugin/plugins/<plugin_id> を選択すると、編集後に「リロード」を押すだけですぐ反映されます。',
          },
        ],
      },
    ],
    agents: jaAgents,
  },
}

export interface NekoShotStep {
  title: string
  description: string
  image: string
  alt: string
}

export interface TutorialsCopy {
  pageIntro: string
  nekoHeading: string
  nekoIntro: string
  nekoSteps: NekoShotStep[]
}

export const TUTORIALS_COPY: Record<DocsLocale, TutorialsCopy> = {
  'zh-CN': {
    pageIntro:
      '下面汇总了每个 Coding Agent 的官方上手指南。任选一个顺手的工具按官方教程装好、登录并打开一个空文件夹，然后回到「选择你的 Coding Agent」页复制 Setup Prompt 发给 AI 即可。',
    nekoHeading: '最后一步：在 N.E.K.O 里加载插件',
    nekoIntro:
      '插件写好后，AI 会在自己的环境里完成打包或后台启动，你需要做的只是在 N.E.K.O 插件管理里点按钮。以下是真实界面截图：',
    nekoSteps: [
      {
        title: '在「插件管理」页刷新并启动插件',
        description:
          '左侧进入「插件管理」，点上方「刷新」找到新插件，再点插件卡片上的「启动」。之后 AI 每改完一版代码，点「重载」即可生效。',
        image: 'neko-plugins-list.png',
        alt: 'N.E.K.O 插件管理页面截图：左侧导航选中「插件管理」，右上角有「刷新」与「导入」按钮，下方是插件卡片列表',
      },
      {
        title: '导入 .neko-plugin 安装包',
        description:
          'AI 打包后会告诉你 .neko-plugin 文件在哪个文件夹。点「插件管理」页右上「导入」，把文件拖进弹窗（或点「选择文件」选中），再点「导入」即完成安装。',
        image: 'neko-import-dialog.png',
        alt: '「导入插件包」弹窗截图：可将 .neko-plugin 或 .neko-bundle 文件拖入虚线框，或点击选择文件后导入',
      },
    ],
  },
  en: {
    pageIntro:
      'Below are the official getting-started guides for every supported coding agent. Pick one, install it, sign in, and open an empty folder — then head back to Choose Your Coding Agent and paste the Setup Prompt.',
    nekoHeading: 'Final step: load the plugin in N.E.K.O',
    nekoIntro:
      'Once the agent finishes, it packages the plugin or starts N.E.K.O itself. All you do is click buttons in the plugin manager — here is the real UI:',
    nekoSteps: [
      {
        title: 'Refresh and start the plugin on the Plugins page',
        description:
          'Open Plugins in the sidebar, click Refresh to see the new plugin, then click Start on its card. After the agent makes new edits, click Reload to apply them.',
        image: 'neko-plugins-list-en.png',
        alt: 'N.E.K.O Plugin Manager Plugins page: sidebar shows Plugins selected, the toolbar has Refresh and Import buttons, and plugin cards fill the grid',
      },
      {
        title: 'Import a .neko-plugin package',
        description:
          'The agent tells you which folder holds the .neko-plugin file. Click Import on the Plugins page, drop the file into the dialog (or click Choose File), then click Import again to install it.',
        image: 'neko-import-dialog-en.png',
        alt: 'Import plugin package dialog: drop a .neko-plugin or .neko-bundle file into the dashed area or click to choose a file, then Import',
      },
    ],
  },
  ja: {
    pageIntro:
      '各 Coding Agent の公式入門ガイドをまとめました。お好みのツールを公式チュートリアルどおりにインストール・ログインして空のフォルダを開いたら、「Coding Agent を選ぶ」ページに戻って Setup Prompt を AI に貼り付けてください。',
    nekoHeading: '最後のステップ：N.E.K.O でプラグインを読み込む',
    nekoIntro:
      'プラグインが完成したら、パッケージ作成や N.E.K.O の起動は AI が自分で行います。あなたはプラグイン管理画面でボタンを押すだけです。実際の画面はこちら：',
    nekoSteps: [
      {
        title: '「プラグイン管理」で再読み込みして起動',
        description:
          '左側の「プラグイン管理」を開き、上の「再読み込み」で新しいプラグインを表示して、カードの「起動」を押します。AI がコードを更新した後は「リロード」を押すだけで反映されます。',
        image: 'neko-plugins-list-ja.png',
        alt: 'N.E.K.O プラグイン管理画面のスクリーンショット：左ナビで「プラグイン管理」を選択、右上に「再読み込み」と「インポート」ボタン、下にプラグインカード一覧',
      },
      {
        title: '.neko-plugin パッケージをインポート',
        description:
          'AI が .neko-plugin ファイルの場所を教えてくれます。「プラグイン管理」の「インポート」を押し、ファイルをダイアログにドラッグ（または「ファイルを選択」）して、「インポート」を押せば完了です。',
        image: 'neko-import-dialog-ja.png',
        alt: '「プラグインパッケージをインポート」ダイアログ：.neko-plugin / .neko-bundle ファイルを点線枠にドロップするか、クリックしてファイルを選択',
      },
    ],
  },
}
