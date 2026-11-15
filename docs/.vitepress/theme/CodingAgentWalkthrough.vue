<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useData, withBase } from 'vitepress'
import {
  buildSetupPrompt,
  copyToClipboard,
  normalizeDocsLocale,
} from './coding-agents-data'

type WalkthroughAgentId = 'codex' | 'dsh'

interface WalkthroughStep {
  title: string
  body: string
  code?: string
  includeExamplePrompt?: boolean
  image: string
  imageAlt: string
  caption: string
  tip?: string
}

interface WalkthroughAgentData {
  id: WalkthroughAgentId
  tabLabel: string
  badge: string
  summary: string
  prereq: string
  steps: WalkthroughStep[]
}

const { lang } = useData()
const locale = computed(() => normalizeDocsLocale(lang.value))
const activeTab = ref<WalkthroughAgentId>('dsh')
const copiedKey = ref<string | null>(null)
let copyTimer: ReturnType<typeof setTimeout> | null = null

const exampleIdeas = {
  'zh-CN':
    '帮我做一个叫 water_reminder 的喝水提醒插件：提供一个可手动触发测试的 remind_now 入口，再提供一个 @llm_tool 让猫娘在对话里帮我记录和播报喝水提醒。',
  en: 'Build a plugin named water_reminder with a manual remind_now @plugin_entry for testing and an @llm_tool so N.E.K.O can send hydration reminders during chat.',
  ja: 'water_reminder という水分補給リマインダープラグインを作ってください。手動テスト用の remind_now エントリーと、会話中に呼び出せる @llm_tool を用意してください。',
} as const

const examplePrompt = computed(() =>
  buildSetupPrompt(locale.value, exampleIdeas[locale.value]),
)

const uiLabels = computed(() => {
  if (locale.value === 'zh-CN') {
    return {
      switcherLabel: '选择要查看的完整图文演示',
      copyCmd: '复制命令',
      copied: '已复制',
      copyExamplePrompt: '一键复制本演示完整 Prompt（含喝水插件需求）',
      prereqLabel: '准备条件：',
      tipLabel: '新手提示：',
    }
  }
  if (locale.value === 'ja') {
    return {
      switcherLabel: '表示する画面付きガイドを選択',
      copyCmd: 'コピー',
      copied: 'コピー済み',
      copyExamplePrompt: 'このサンプル用の Setup Prompt を一括コピー',
      prereqLabel: '事前準備：',
      tipLabel: 'ポイント：',
    }
  }
  return {
    switcherLabel: 'Select a visual walkthrough',
    copyCmd: 'Copy',
    copied: 'Copied',
    copyExamplePrompt: 'Copy full Setup Prompt for this Water Reminder demo',
    prereqLabel: 'Prerequisites:',
    tipLabel: 'Beginner tip:',
  }
})

const walkthroughs = computed<WalkthroughAgentData[]>(() => {
  if (locale.value === 'zh-CN') {
    return [
      {
        id: 'dsh',
        tabLabel: 'DeepSeek Harness (dsh) 图文演示',
        badge: '国内开源推荐 · 浏览器可视化操作',
        summary:
          '使用 DeepSeek 开源的 dsh 网页端，从空文件夹出发，让 AI 自动拉取 N.E.K.O 仓库并用 neko-plugin CLI 做出「喝水提醒」插件。',
        prereq: '已安装 Git、uv 与 Node.js (>= 24.9)，并准备一个 DeepSeek API Key。',
        steps: [
          {
            title: '启动 DeepSeek Harness 本地网页端',
            body: '在电脑上新建一个空文件夹（例如 neko-work），打开终端运行下方命令，浏览器会自动打开 dsh 界面：',
            code: 'npx @deepseek-ai/dsh web',
            image: '/images/plugins/onboarding/dsh-1-launch.svg',
            imageAlt: '终端运行 npx @deepseek-ai/dsh web 启动浏览器界面示意图',
            caption: '终端输出本地访问地址并自动唤起浏览器 Web UI',
          },
          {
            title: '在 Settings → Models 配置 DeepSeek API Key',
            body: '首次打开网页端时，点击右上角「Settings → Models」，填入你的 DeepSeek API Key 并点击「Save」，无需重启即可生效。',
            image: '/images/plugins/onboarding/dsh-2-models.svg',
            imageAlt: '在 dsh Settings Models 页面保存 DeepSeek API Key 示意图',
            caption: '填入 API Key 保存后，模型路由立即就绪',
          },
          {
            title: '点击 Choose workspace 选中工作目录',
            body: '回到主界面点击「Choose workspace」，把刚才新建的空文件夹（或已有的 N.E.K.O 源码目录）添加进来并选中，底部输入框即会解锁。',
            image: '/images/plugins/onboarding/dsh-3-workspace.svg',
            imageAlt: '在 dsh 中点击 Choose workspace 选中本地工作文件夹示意图',
            caption: '选中工作区后，dsh 才能在该目录内执行 git 与 neko-plugin 命令',
          },
          {
            title: '一键复制粘贴 Setup Prompt，批准命令执行',
            body: '点击下方按钮复制包含「喝水提醒插件」需求的完整 Setup Prompt，粘贴进 dsh 对话框发送；当弹出终端执行权限确认时点击「Approve」：',
            includeExamplePrompt: true,
            image: '/images/plugins/onboarding/dsh-4-prompt.svg',
            imageAlt: '向 dsh 发送 Setup Prompt 并点击 Approve 批准执行 neko-plugin 命令示意图',
            caption: 'dsh 自动完成 git clone、uv sync 并调用 uv run neko-plugin init water_reminder',
            tip: '如果当前目录里还没有 N.E.K.O 仓库，首次 git clone 与 uv sync 需要下载依赖，耐心等待 1～3 分钟即可。',
          },
          {
            title: '等待 AI 自动编码并通过 neko-plugin check 校验',
            body: 'dsh 会在 plugin/plugins/water_reminder/ 下写好异步入口和单元测试，并自动运行 check、lint 和 pytest 直到 0 error(s)。',
            image: '/images/plugins/onboarding/dsh-5-check.svg',
            imageAlt: 'dsh 运行 uv run neko-plugin check water_reminder 输出 0 errors 示意图',
            caption: '看到 [OK] water_reminder: check found 0 error(s) 代表插件已符合运行规范',
            tip: '新项目出现 4 warning(s)（未配置远程 Git 仓库与未提交文件）属于未发布阶段的正常现象，完全不影响本地运行。',
          },
          {
            title: '在 N.E.K.O 中启动体验（或打包导入桌面版）',
            body: '在 N.E.K.O 源码目录运行启动命令，打开插件页面点击「刷新 → 启动」即可触发测试；若使用 Steam / 桌面安装版，直接去插件中心导入 target/water_reminder.neko-plugin：',
            code: 'uv run python launcher.py',
            image: '/images/plugins/onboarding/neko-run-verify.svg',
            imageAlt: '在 N.E.K.O 插件管理页刷新启动 water_reminder 并触发入口示意图',
            caption: '在插件详情「入口」页点击触发，立即看到返回结果',
          },
        ],
      },
      {
        id: 'codex',
        tabLabel: 'OpenAI Codex 图文演示',
        badge: '国际主流代表 · 桌面端 / CLI 通用',
        summary:
          '演示如何用 OpenAI Codex 打开空文件夹，粘贴一段 Setup Prompt 让它全自动驱动 neko-plugin CLI 完成「喝水提醒」插件。',
        prereq: '已安装 Git、uv，并可使用 ChatGPT 账号登录 Codex 桌面端或 Codex CLI。',
        steps: [
          {
            title: '打开 Codex 并选择空工作文件夹',
            body: '打开 Codex 桌面端选择新建的空文件夹（或已有 N.E.K.O 目录）；若使用终端 CLI，进入该文件夹后运行：',
            code: 'npm install -g @openai/codex && codex',
            image: '/images/plugins/onboarding/codex-1-open.svg',
            imageAlt: '打开 OpenAI Codex 并选择本地工作文件夹示意图',
            caption: '登录 ChatGPT 账号后，将工作目录指向你的插件开发文件夹',
          },
          {
            title: '一键粘贴 Setup Prompt 发送给 Codex',
            body: '点击下方按钮复制预设好「喝水提醒插件」需求的 Setup Prompt，直接粘贴到 Codex 输入框回车：',
            includeExamplePrompt: true,
            image: '/images/plugins/onboarding/codex-2-prompt.svg',
            imageAlt: '在 Codex 输入框粘贴 N.E.K.O Setup Prompt 示意图',
            caption: '无需手写任何命令行参数，Prompt 已包含完整的 neko-plugin CLI 规范',
          },
          {
            title: '允许 Codex 拉取环境并运行 neko-plugin init',
            body: '当 Codex 提示需要运行 git clone、uv sync 或 uv run neko-plugin init water_reminder 时，确认允许执行。',
            image: '/images/plugins/onboarding/codex-3-init.svg',
            imageAlt: 'Codex 自动运行 uv sync 与 uv run neko-plugin init water_reminder 示意图',
            caption: 'Codex 使用官方 CLI 在 plugin/plugins/water_reminder/ 下生成标准结构',
          },
          {
            title: 'Codex 完成代码编写并通过 CLI 自检',
            body: 'Codex 会自动查阅本地 docs/plugins/ 文档，编写符合 SDK 契约的 async def 入口，并运行 check、lint 与 pytest 确保零报错。',
            image: '/images/plugins/onboarding/codex-4-check.svg',
            imageAlt: 'Codex 自动执行 neko-plugin check、lint 和 pytest 全部通过示意图',
            caption: '若有语法或契约问题，Codex 会根据 CLI 报错自行修复后再向你汇报',
          },
          {
            title: '在 N.E.K.O 中刷新启动或导入 .neko-plugin 包',
            body: '运行下方命令启动源码版 N.E.K.O 并在插件页点「刷新 → 启动」，或让 Codex 运行 neko-plugin build 生成安装包导入你的桌面版插件中心：',
            code: 'uv run python launcher.py',
            image: '/images/plugins/onboarding/neko-run-verify.svg',
            imageAlt: '在 N.E.K.O 插件管理页启动并验证 water_reminder 插件示意图',
            caption: '想继续加新功能？直接在同一个会话里用大白话告诉 Codex，改完点「重载」即可！',
          },
        ],
      },
    ]
  }

  if (locale.value === 'ja') {
    return [
      {
        id: 'codex',
        tabLabel: 'OpenAI Codex 画面付きガイド',
        badge: '定番エージェント · デスクトップ / CLI',
        summary:
          '空のフォルダを Codex で開き、Setup Prompt を貼り付けて neko-plugin CLI で「水分補給リマインダー」プラグインを完成させる流れです。',
        prereq: 'Git・uv がインストール済みで、ChatGPT アカウントで Codex にログインできること。',
        steps: [
          {
            title: 'Codex を起動し、作業フォルダを開く',
            body: 'Codex アプリで空のフォルダ（または既存の N.E.K.O フォルダ）を開くか、ターミナルで次を実行します：',
            code: 'npm install -g @openai/codex && codex',
            image: '/images/plugins/onboarding/codex-1-open.svg',
            imageAlt: 'Codex を開いて作業フォルダを選択する画面イメージ',
            caption: 'ChatGPT でログインし、作業ディレクトリを指定します',
          },
          {
            title: 'Setup Prompt を一括コピーして送信する',
            body: '下のボタンからサンプル要件入りの Setup Prompt をコピーし、Codex の入力欄に貼り付けて送信します：',
            includeExamplePrompt: true,
            image: '/images/plugins/onboarding/codex-2-prompt.svg',
            imageAlt: 'Codex に N.E.K.O Setup Prompt を貼り付ける画面イメージ',
            caption: 'neko-plugin CLI の手順と SDK ルールがすべて含まれています',
          },
          {
            title: 'リポジトリ準備と neko-plugin init の実行を許可する',
            body: 'git clone・uv sync・uv run neko-plugin init water_reminder の実行確認が表示されたら許可します。',
            image: '/images/plugins/onboarding/codex-3-init.svg',
            imageAlt: 'Codex が uv sync と neko-plugin init を実行する画面イメージ',
            caption: 'plugin/plugins/water_reminder/ に標準構成が自動生成されます',
          },
          {
            title: '実装と neko-plugin check / pytest の自動通過を確認する',
            body: 'Codex がローカルの docs/plugins/ を参照して非同期エントリーを実装し、check・lint・pytest が 0 error(s) で通るまで自動調整します。',
            image: '/images/plugins/onboarding/codex-4-check.svg',
            imageAlt: 'Codex が neko-plugin check と pytest を通過させる画面イメージ',
            caption: '[OK] water_reminder: check found 0 error(s) が表示されれば準備完了です',
          },
          {
            title: 'N.E.K.O で起動確認、またはパッケージを導入する',
            body: 'ソース版を起動して Plugins 画面で「Refresh → Start」を押すか、build された .neko-plugin を Plugin Center から読み込みます：',
            code: 'uv run python launcher.py',
            image: '/images/plugins/onboarding/neko-run-verify.svg',
            imageAlt: 'N.E.K.O のプラグイン画面で water_reminder を起動して実行する画面イメージ',
            caption: '機能を追加したいときはチャットで指示し、Reload を押すだけですぐ反映されます',
          },
        ],
      },
      {
        id: 'dsh',
        tabLabel: 'DeepSeek Harness (dsh) 画面付きガイド',
        badge: 'オープンソース · ブラウザ Web UI',
        summary:
          'DeepSeek 製のローカル Web エージェント dsh を使い、ブラウザ画面から neko-plugin CLI でプラグインを作成する手順です。',
        prereq: 'Git・uv・Node.js (>= 24.9) と DeepSeek API キー。',
        steps: [
          {
            title: 'dsh のローカル Web UI を起動する',
            body: '空の作業フォルダを作成し、ターミナルで次のコマンドを実行するとブラウザが自動で開きます：',
            code: 'npx @deepseek-ai/dsh web',
            image: '/images/plugins/onboarding/dsh-1-launch.svg',
            imageAlt: 'npx @deepseek-ai/dsh web で Web UI を起動する画面イメージ',
            caption: 'ローカル URL が出力され、ブラウザ画面が立ち上がります',
          },
          {
            title: 'Settings → Models で API キーを保存する',
            body: '右上の「Settings → Models」を開き、DeepSeek API キーを入力して「Save」をクリックします。',
            image: '/images/plugins/onboarding/dsh-2-models.svg',
            imageAlt: 'dsh の Settings Models 画面で API キーを保存するイメージ',
            caption: '再起動なしでそのままモデルが利用可能になります',
          },
          {
            title: 'Choose workspace で作業フォルダを選択する',
            body: '「Choose workspace」から作業フォルダを追加・選択すると、チャット入力欄が有効になります。',
            image: '/images/plugins/onboarding/dsh-3-workspace.svg',
            imageAlt: 'dsh で Choose workspace からフォルダを選択するイメージ',
            caption: '選択したフォルダ内で git や neko-plugin が実行されます',
          },
          {
            title: 'Setup Prompt を貼り付け、コマンド実行を承認する',
            body: '下のボタンで Setup Prompt をコピーして送信し、承認ダイアログが出たら「Approve」をクリックします：',
            includeExamplePrompt: true,
            image: '/images/plugins/onboarding/dsh-4-prompt.svg',
            imageAlt: 'dsh に Setup Prompt を送信して Approve を押すイメージ',
            caption: 'dsh が N.E.K.O の準備と neko-plugin init water_reminder を自動で進めます',
          },
          {
            title: 'neko-plugin check とテストの完了を待つ',
            body: 'dsh がコードを編集し、check・lint・pytest をすべてパスさせます。',
            image: '/images/plugins/onboarding/dsh-5-check.svg',
            imageAlt: 'dsh で neko-plugin check が 0 error(s) で完了したイメージ',
            caption: '未公開段階の 4 warning(s) はローカル動作に影響しません',
          },
          {
            title: 'N.E.K.O で起動・動作確認する',
            body: 'launcher.py で起動して Plugins 画面から Start するか、target/water_reminder.neko-plugin を Plugin Center にインポートします：',
            code: 'uv run python launcher.py',
            image: '/images/plugins/onboarding/neko-run-verify.svg',
            imageAlt: 'N.E.K.O でプラグインを起動してテストするイメージ',
            caption: 'エントリーを実行して Ok レスポンスが返れば完成です',
          },
        ],
      },
    ]
  }

  return [
    {
      id: 'codex',
      tabLabel: 'OpenAI Codex Walkthrough',
      badge: 'Desktop App & CLI · ChatGPT Sign-In',
      summary:
        'Start from an empty folder in OpenAI Codex, paste one Setup Prompt, and watch it scaffold, code, and validate a Water Reminder plugin via neko-plugin CLI.',
      prereq: 'Git and uv installed, plus a ChatGPT account signed in to the Codex app or CLI.',
      steps: [
        {
          title: 'Open Codex on an empty workspace folder',
          body: 'Create an empty folder (for example neko-work) and open it in the Codex app, or launch the CLI inside that folder:',
          code: 'npm install -g @openai/codex && codex',
          image: '/images/plugins/onboarding/codex-1-open.svg',
          imageAlt: 'Opening an empty workspace folder in OpenAI Codex',
          caption: 'Point Codex at your empty workspace or existing N.E.K.O checkout',
        },
        {
          title: 'Copy and send the Setup Prompt',
          body: 'Click the button below to copy the Setup Prompt pre-filled with the Water Reminder example, then paste it into Codex:',
          includeExamplePrompt: true,
          image: '/images/plugins/onboarding/codex-2-prompt.svg',
          imageAlt: 'Pasting the N.E.K.O Setup Prompt into OpenAI Codex',
          caption: 'The prompt teaches Codex every neko-plugin CLI command and SDK rule automatically',
        },
        {
          title: 'Approve repo setup and neko-plugin init',
          body: 'Allow Codex to run git clone, uv sync, and uv run neko-plugin init water_reminder when prompted.',
          image: '/images/plugins/onboarding/codex-3-init.svg',
          imageAlt: 'Codex running uv sync and neko-plugin init water_reminder',
          caption: 'Codex generates the official scaffold inside plugin/plugins/water_reminder/',
        },
        {
          title: 'Let Codex implement features and pass CLI checks',
          body: 'Codex reads the local docs/plugins/ files, writes async @plugin_entry and @llm_tool handlers, and runs check, lint, and pytest.',
          image: '/images/plugins/onboarding/codex-4-check.svg',
          imageAlt: 'Codex passing neko-plugin check, lint, and pytest with 0 errors',
          caption: '0 error(s) confirms the manifest, entry class, and tests are ready',
        },
        {
          title: 'Test live in N.E.K.O or import the .neko-plugin package',
          body: 'Launch N.E.K.O from source and click Refresh -> Start on the Plugins page, or import target/water_reminder.neko-plugin in Plugin Center:',
          code: 'uv run python launcher.py',
          image: '/images/plugins/onboarding/neko-run-verify.svg',
          imageAlt: 'Starting and triggering water_reminder on the N.E.K.O Plugins page',
          caption: 'Want another feature? Just ask Codex in plain English and click Reload!',
        },
      ],
    },
    {
      id: 'dsh',
      tabLabel: 'DeepSeek Harness (dsh) Walkthrough',
      badge: 'Open-Source Local Agent · Browser Web UI',
      summary:
        'Drive the entire neko-plugin CLI workflow from a clean local browser UI using DeepSeek Harness (dsh).',
      prereq: 'Git, uv, Node.js (>= 24.9), and a DeepSeek API key.',
      steps: [
        {
          title: 'Start the DeepSeek Harness Web UI',
          body: 'Create an empty folder, open a terminal, and run this command to launch the local browser interface:',
          code: 'npx @deepseek-ai/dsh web',
          image: '/images/plugins/onboarding/dsh-1-launch.svg',
          imageAlt: 'Launching DeepSeek Harness Web UI from the terminal',
          caption: 'dsh prints the local URL and opens your default browser automatically',
        },
        {
          title: 'Save your API key in Settings -> Models',
          body: 'Open Settings -> Models in the top bar, paste your DeepSeek API key, and click Save.',
          image: '/images/plugins/onboarding/dsh-2-models.svg',
          imageAlt: 'Configuring a DeepSeek API key in dsh Settings -> Models',
          caption: 'The model route becomes active immediately without restarting dsh',
        },
        {
          title: 'Select your folder with Choose workspace',
          body: 'Click Choose workspace, add your empty folder (or existing N.E.K.O checkout), and select it to unlock the composer.',
          image: '/images/plugins/onboarding/dsh-3-workspace.svg',
          imageAlt: 'Selecting the local folder in dsh Choose workspace',
          caption: 'Once a workspace is active, dsh can run git, uv, and neko-plugin inside it',
        },
        {
          title: 'Paste the Setup Prompt and approve shell commands',
          body: 'Copy the pre-filled Water Reminder Setup Prompt below, send it in the chat box, and click Approve when dsh asks to run commands:',
          includeExamplePrompt: true,
          image: '/images/plugins/onboarding/dsh-4-prompt.svg',
          imageAlt: 'Sending the Setup Prompt in dsh and approving command execution',
          caption: 'dsh clones or reuses N.E.K.O, runs uv sync, and scaffolds water_reminder',
          tip: 'The first clone + uv sync downloads dependencies and may take 1-3 minutes.',
        },
        {
          title: 'Verify 0 errors from neko-plugin check and pytest',
          body: 'dsh edits plugin/plugins/water_reminder/, updates tests, and runs check, lint, and pytest.',
          image: '/images/plugins/onboarding/dsh-5-check.svg',
          imageAlt: 'dsh showing 0 errors from neko-plugin check water_reminder',
          caption: 'The 4 initial git/hook warnings on an unpublished project are expected and harmless',
        },
        {
          title: 'Start the plugin in N.E.K.O',
          body: 'Run launcher.py and click Refresh -> Start on the Plugins page, or import target/water_reminder.neko-plugin into your desktop N.E.K.O:',
          code: 'uv run python launcher.py',
          image: '/images/plugins/onboarding/neko-run-verify.svg',
          imageAlt: 'Running and testing water_reminder inside N.E.K.O Plugin Manager',
          caption: 'Trigger remind_now under Entries to see the live Ok result',
        },
      ],
    },
  ]
})

watch(
  locale,
  (loc) => {
    activeTab.value = loc === 'zh-CN' ? 'dsh' : 'codex'
  },
  { immediate: true },
)

const currentWalkthrough = computed(
  () => walkthroughs.value.find((w) => w.id === activeTab.value) ?? walkthroughs.value[0],
)

async function copyText(text: string, key: string) {
  const ok = await copyToClipboard(text)
  copiedKey.value = ok ? key : null
  if (!ok) return
  if (copyTimer) clearTimeout(copyTimer)
  copyTimer = setTimeout(() => {
    copiedKey.value = null
  }, 2200)
}

function chooseTab(id: WalkthroughAgentId) {
  activeTab.value = id
  if (typeof window !== 'undefined') {
    const url = new URL(window.location.href)
    url.searchParams.set('agent', id)
    window.history.replaceState({}, '', url)
  }
}

onMounted(() => {
  const params = new URLSearchParams(window.location.search)
  const agent = params.get('agent')
  if (agent === 'codex' || agent === 'dsh') {
    activeTab.value = agent
  }
})

onBeforeUnmount(() => {
  if (copyTimer) clearTimeout(copyTimer)
})
</script>

<template>
  <div class="NekoWalkthrough">
    <div class="NekoWalkthrough-switcher" role="tablist" :aria-label="uiLabels.switcherLabel">
      <button
        v-for="item in walkthroughs"
        :key="item.id"
        type="button"
        role="tab"
        class="NekoWalkthrough-tab"
        :class="{ 'is-active': currentWalkthrough.id === item.id }"
        :aria-selected="currentWalkthrough.id === item.id"
        @click="chooseTab(item.id)"
      >
        <span class="NekoWalkthrough-tabName">{{ item.tabLabel }}</span>
        <span class="NekoWalkthrough-tabBadge">{{ item.badge }}</span>
      </button>
    </div>

    <div class="NekoWalkthrough-intro">
      <p class="NekoWalkthrough-summary">{{ currentWalkthrough.summary }}</p>
      <p class="NekoWalkthrough-prereq">
        <strong>{{ uiLabels.prereqLabel }}</strong>{{ currentWalkthrough.prereq }}
      </p>
    </div>

    <ol class="NekoWalkthrough-list">
      <li
        v-for="(step, idx) in currentWalkthrough.steps"
        :key="`${currentWalkthrough.id}-${idx}`"
        class="NekoWalkthrough-step"
      >
        <div class="NekoWalkthrough-stepHeader">
          <span class="NekoWalkthrough-stepBadge">{{ idx + 1 }}</span>
          <h2 class="NekoWalkthrough-stepTitle">{{ step.title }}</h2>
        </div>

        <p class="NekoWalkthrough-stepBody">{{ step.body }}</p>

        <div v-if="step.code" class="NekoWalkthrough-codeRow">
          <code>{{ step.code }}</code>
          <button
            type="button"
            class="NekoWalkthrough-copyBtn"
            :class="{ 'is-copied': copiedKey === `cmd-${currentWalkthrough.id}-${idx}` }"
            @click="copyText(step.code, `cmd-${currentWalkthrough.id}-${idx}`)"
          >
            {{
              copiedKey === `cmd-${currentWalkthrough.id}-${idx}`
                ? `✓ ${uiLabels.copied}`
                : uiLabels.copyCmd
            }}
          </button>
        </div>

        <div v-if="step.includeExamplePrompt" class="NekoWalkthrough-promptRow">
          <button
            type="button"
            class="NekoWalkthrough-primaryPromptBtn"
            :class="{ 'is-copied': copiedKey === `prompt-${currentWalkthrough.id}` }"
            @click="copyText(examplePrompt, `prompt-${currentWalkthrough.id}`)"
          >
            {{
              copiedKey === `prompt-${currentWalkthrough.id}`
                ? `✓ ${uiLabels.copied}`
                : uiLabels.copyExamplePrompt
            }}
          </button>
        </div>

        <figure class="NekoWalkthrough-figure">
          <img
            :src="withBase(step.image)"
            :alt="step.imageAlt"
            loading="lazy"
          />
          <figcaption>{{ step.caption }}</figcaption>
        </figure>

        <div v-if="step.tip" class="NekoWalkthrough-tip">
          <strong>{{ uiLabels.tipLabel }}</strong>{{ step.tip }}
        </div>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.NekoWalkthrough {
  margin: 20px 0 36px;
}

.NekoWalkthrough-switcher {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 10px;
  margin-bottom: 18px;
}

.NekoWalkthrough-tab {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  padding: 14px 16px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 12px;
  background: var(--vp-c-bg-soft);
  color: var(--vp-c-text-1);
  text-align: left;
  cursor: pointer;
  transition: border-color 0.18s ease, background-color 0.18s ease;
}

.NekoWalkthrough-tab:hover {
  border-color: var(--vp-c-brand-1);
}

.NekoWalkthrough-tab.is-active {
  border-color: var(--vp-c-brand-1);
  background: var(--vp-c-brand-soft);
}

.NekoWalkthrough-tabName {
  font-size: 15px;
  font-weight: 700;
  color: var(--vp-c-text-1);
}

.NekoWalkthrough-tab.is-active .NekoWalkthrough-tabName {
  color: var(--vp-c-brand-1);
}

.NekoWalkthrough-tabBadge {
  font-size: 12px;
  color: var(--vp-c-text-2);
}

.NekoWalkthrough-intro {
  padding: 14px 18px;
  margin-bottom: 22px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 12px;
  background: var(--vp-c-bg-soft);
}

.NekoWalkthrough-summary {
  margin: 0 !important;
  color: var(--vp-c-text-1);
  font-size: 14px;
  line-height: 1.6;
}

.NekoWalkthrough-prereq {
  margin: 8px 0 0 !important;
  color: var(--vp-c-text-2);
  font-size: 13px;
}

.NekoWalkthrough-list {
  display: flex;
  flex-direction: column;
  gap: 22px;
  margin: 0 !important;
  padding: 0 !important;
  list-style: none !important;
}

.NekoWalkthrough-step {
  margin: 0 !important;
  padding: 20px 22px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 14px;
  background: var(--vp-c-bg);
}

.NekoWalkthrough-stepHeader {
  display: flex;
  align-items: center;
  gap: 12px;
}

.NekoWalkthrough-stepBadge {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--vp-c-brand-1);
  color: #fff;
  font-size: 14px;
  font-weight: 700;
}

.NekoWalkthrough-stepTitle {
  margin: 0 !important;
  padding: 0 !important;
  border: 0 !important;
  color: var(--vp-c-text-1);
  font-size: 18px !important;
  font-weight: 700;
  line-height: 1.35 !important;
}

.NekoWalkthrough-stepBody {
  margin: 10px 0 0 !important;
  color: var(--vp-c-text-1);
  font-size: 14px;
  line-height: 1.65;
}

.NekoWalkthrough-codeRow {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 12px;
  padding: 9px 14px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 8px;
  background: var(--vp-code-block-bg);
}

.NekoWalkthrough-codeRow code {
  overflow-x: auto;
  padding: 0 !important;
  background: transparent !important;
  color: var(--vp-c-text-1);
  font-size: 13px;
  white-space: nowrap;
}

.NekoWalkthrough-copyBtn {
  flex: 0 0 auto;
  padding: 4px 12px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 6px;
  background: var(--vp-c-bg);
  color: var(--vp-c-text-1);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.NekoWalkthrough-copyBtn:hover,
.NekoWalkthrough-copyBtn.is-copied {
  border-color: var(--vp-c-brand-1);
  color: var(--vp-c-brand-1);
}

.NekoWalkthrough-promptRow {
  margin-top: 12px;
}

.NekoWalkthrough-primaryPromptBtn {
  padding: 9px 16px;
  border: 1px solid var(--vp-c-brand-1);
  border-radius: 8px;
  background: var(--vp-c-brand-1);
  color: #fff;
  font-size: 13.5px;
  font-weight: 700;
  cursor: pointer;
}

.NekoWalkthrough-primaryPromptBtn:hover {
  border-color: var(--vp-c-brand-2);
  background: var(--vp-c-brand-2);
}

.NekoWalkthrough-primaryPromptBtn.is-copied {
  border-color: #16a34a;
  background: #16a34a;
}

.NekoWalkthrough-figure {
  margin: 14px 0 0 !important;
  overflow: hidden;
  border: 1px solid var(--vp-c-divider);
  border-radius: 10px;
  background: var(--vp-c-bg-soft);
}

.NekoWalkthrough-figure img {
  display: block;
  width: 100%;
  height: auto;
}

.NekoWalkthrough-figure figcaption {
  padding: 8px 14px;
  border-top: 1px solid var(--vp-c-divider);
  color: var(--vp-c-text-2);
  font-size: 12.5px;
  text-align: center;
}

.NekoWalkthrough-tip {
  margin-top: 12px;
  padding: 10px 14px;
  border-left: 3px solid var(--vp-c-brand-1);
  border-radius: 6px;
  background: var(--vp-c-bg-soft);
  color: var(--vp-c-text-2);
  font-size: 13px;
  line-height: 1.55;
}
</style>
