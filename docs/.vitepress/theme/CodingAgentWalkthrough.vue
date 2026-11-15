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
      copied: '已复制',
      copyExamplePrompt: '一键复制本演示完整 Prompt（含喝水插件需求）',
      prereqLabel: '准备条件：',
      tipLabel: '新手提示：',
    }
  }
  if (locale.value === 'ja') {
    return {
      switcherLabel: '表示する画面付きガイドを選択',
      copied: 'コピー済み',
      copyExamplePrompt: 'このサンプル用の Setup Prompt を一括コピー',
      prereqLabel: '事前準備：',
      tipLabel: 'ポイント：',
    }
  }
  return {
    switcherLabel: 'Select a visual walkthrough',
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
        tabLabel: 'DeepSeek Harness 图文演示',
        badge: '国内开源推荐 · 官方桌面应用',
        summary:
          '使用 DeepSeek 开源的 Harness 桌面应用，从空文件夹出发，让 AI 自动准备仓库并用官方 neko-plugin 工具做出「喝水提醒」插件。',
        prereq: '只需一个 DeepSeek API Key，其余环境 AI 会自己安装。',
        steps: [
          {
            title: '下载安装 DeepSeek Harness 桌面版',
            body: '在电脑上新建一个空文件夹（例如 neko-work），然后从官方发布页下载并安装 DeepSeek Harness 桌面版，打开应用进入主界面。',
            image: '/images/plugins/onboarding/dsh-1-launch.svg',
            imageAlt: 'DeepSeek Harness 桌面应用欢迎界面示意图',
            caption: '桌面应用打开即用，界面与网页版一致',
          },
          {
            title: '在 Settings → Models 配置 DeepSeek API Key',
            body: '首次打开应用时，点击右上角「Settings → Models」，填入你的 DeepSeek API Key 并点击「Save」，无需重启即可生效。',
            image: '/images/plugins/onboarding/dsh-2-models.svg',
            imageAlt: '在 DeepSeek Harness Settings Models 页面保存 DeepSeek API Key 示意图',
            caption: '填入 API Key 保存后，模型路由立即就绪',
          },
          {
            title: '点击 Choose workspace 选中工作目录',
            body: '回到主界面点击「Choose workspace」，把刚才新建的空文件夹（或已有的 N.E.K.O 源码目录）添加进来并选中，底部输入框即会解锁。',
            image: '/images/plugins/onboarding/dsh-3-workspace.svg',
            imageAlt: '在 DeepSeek Harness 中点击 Choose workspace 选中本地工作文件夹示意图',
            caption: '选中工作区后，AI 会在该文件夹内完成仓库准备与插件开发',
          },
          {
            title: '一键复制粘贴 Setup Prompt，允许 AI 执行操作',
            body: '点击下方按钮复制包含「喝水提醒插件」需求的完整 Setup Prompt，粘贴进会话框发送；当 AI 请求执行操作时点击「Approve / 允许」：',
            includeExamplePrompt: true,
            image: '/images/plugins/onboarding/dsh-4-prompt.svg',
            imageAlt: '向 DeepSeek Harness 发送 Setup Prompt 并点击 Approve 允许 AI 执行操作示意图',
            caption: 'AI 自动准备 N.E.K.O 仓库并创建 water_reminder 插件',
            tip: '如果当前目录里还没有 N.E.K.O 仓库，首次准备需要下载依赖，耐心等待 1～3 分钟即可。',
          },
          {
            title: '等待 AI 自动编码并通过检查',
            body: 'AI 会在 plugin/plugins/water_reminder/ 下写好异步入口和单元测试，并自动调用官方 neko-plugin 工具完成检查与测试直到全部通过。',
            image: '/images/plugins/onboarding/dsh-5-check.svg',
            imageAlt: 'AI 在 DeepSeek Harness 界面中运行检查并全部通过示意图',
            caption: '看到 [OK] water_reminder: check found 0 error(s) 代表插件已符合运行规范',
            tip: '新项目出现 4 warning(s)（未配置远程 Git 仓库与未提交文件）属于未发布阶段的正常现象，完全不影响本地运行。',
          },
          {
            title: '在 N.E.K.O 中启动体验（或导入安装包）',
            body: '让 AI 生成 .neko-plugin 安装包后，打开 N.E.K.O「插件中心」点「导入」选中该文件即可；若你使用源码版，可让 AI 在后台帮你启动 N.E.K.O，你在插件页面点「刷新 → 启动」就能看到效果。',
            image: '/images/plugins/onboarding/neko-run-verify.svg',
            imageAlt: '在 N.E.K.O 插件管理页刷新启动 water_reminder 并触发入口示意图',
            caption: '在插件详情「入口」页点击触发，立即看到返回结果',
          },
        ],
      },
      {
        id: 'codex',
        tabLabel: 'OpenAI Codex 图文演示',
        badge: '国际主流代表 · 官方桌面应用',
        summary:
          '演示如何用 OpenAI Codex 桌面应用打开空文件夹，粘贴一段 Setup Prompt 让它全自动调用官方 neko-plugin 工具完成「喝水提醒」插件。',
        prereq: '只需一个可登录的 ChatGPT 账号，其余环境 AI 会自己安装。',
        steps: [
          {
            title: '打开 Codex 并选择空工作文件夹',
            body: '下载安装 Codex 桌面应用，用 ChatGPT 账号登录后，选择新建的空文件夹（或已有 N.E.K.O 目录）作为工作区。',
            image: '/images/plugins/onboarding/codex-1-open.svg',
            imageAlt: '在 Codex 桌面应用中选择本地工作文件夹示意图',
            caption: '登录 ChatGPT 账号后，将工作目录指向你的插件开发文件夹',
          },
          {
            title: '一键粘贴 Setup Prompt 发送给 Codex',
            body: '点击下方按钮复制预设好「喝水提醒插件」需求的 Setup Prompt，直接粘贴到 Codex 输入框发送：',
            includeExamplePrompt: true,
            image: '/images/plugins/onboarding/codex-2-prompt.svg',
            imageAlt: '在 Codex 输入框粘贴 N.E.K.O Setup Prompt 示意图',
            caption: '无需手写任何内容，Prompt 已包含完整的 neko-plugin 开发规范',
          },
          {
            title: '允许 Codex 准备环境并创建插件',
            body: '当 Codex 请求执行操作（准备仓库、同步依赖、创建 water_reminder 插件）时，点击允许。',
            image: '/images/plugins/onboarding/codex-3-init.svg',
            imageAlt: 'Codex 自动准备环境并创建 water_reminder 插件示意图',
            caption: 'Codex 使用官方工具在 plugin/plugins/water_reminder/ 下生成标准结构',
          },
          {
            title: 'Codex 完成代码编写并通过自检',
            body: 'Codex 会自动查阅本地 docs/plugins/ 文档，编写符合 SDK 契约的 async def 入口，并自动运行检查、代码风格校验与测试确保零报错。',
            image: '/images/plugins/onboarding/codex-4-check.svg',
            imageAlt: 'Codex 自动完成检查、校验和测试全部通过示意图',
            caption: '若有语法或契约问题，Codex 会根据报错自行修复后再向你汇报',
          },
          {
            title: '在 N.E.K.O 中刷新启动或导入 .neko-plugin 包',
            body: '让 Codex 生成 .neko-plugin 安装包后，在 N.E.K.O 插件中心点「导入」选中该文件；源码版则可让 Codex 在后台帮你启动 N.E.K.O，你在插件页点「刷新 → 启动」即可。',
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
        badge: '定番エージェント · 公式デスクトップアプリ',
        summary:
          '空のフォルダを Codex デスクトップアプリで開き、Setup Prompt を貼り付けて公式 neko-plugin ツールで「水分補給リマインダー」プラグインを完成させる流れです。',
        prereq: 'ChatGPT アカウントでログインできること。あとの環境は AI が自分で用意します。',
        steps: [
          {
            title: 'Codex を起動し、作業フォルダを開く',
            body: 'Codex デスクトップアプリをインストールして ChatGPT アカウントでログインし、空のフォルダ（または既存の N.E.K.O フォルダ）を開きます。',
            image: '/images/plugins/onboarding/codex-1-open.svg',
            imageAlt: 'Codex デスクトップアプリで作業フォルダを選択する画面イメージ',
            caption: 'ChatGPT でログインし、作業ディレクトリを指定します',
          },
          {
            title: 'Setup Prompt を一括コピーして送信する',
            body: '下のボタンからサンプル要件入りの Setup Prompt をコピーし、Codex の入力欄に貼り付けて送信します：',
            includeExamplePrompt: true,
            image: '/images/plugins/onboarding/codex-2-prompt.svg',
            imageAlt: 'Codex に N.E.K.O Setup Prompt を貼り付ける画面イメージ',
            caption: 'neko-plugin の手順と SDK ルールがすべて含まれています',
          },
          {
            title: '環境準備とプラグイン作成を許可する',
            body: 'Codex がリポジトリの準備・依存関係の同期・water_reminder プラグインの作成を求めてきたら、許可を押します。',
            image: '/images/plugins/onboarding/codex-3-init.svg',
            imageAlt: 'Codex が自動で環境準備とプラグイン作成を進める画面イメージ',
            caption: 'plugin/plugins/water_reminder/ に標準構成が自動生成されます',
          },
          {
            title: '実装と自動チェックの完了を確認する',
            body: 'Codex がローカルの docs/plugins/ を参照して非同期エントリーを実装し、チェック・lint・テストがすべて通るまで自動で調整します。',
            image: '/images/plugins/onboarding/codex-4-check.svg',
            imageAlt: 'Codex がチェックとテストをすべて通過させる画面イメージ',
            caption: '[OK] water_reminder: check found 0 error(s) が表示されれば準備完了です',
          },
          {
            title: 'N.E.K.O で起動確認、またはパッケージを導入する',
            body: 'Codex に .neko-plugin パッケージを作ってもらい、Plugin Center の「インポート」からそのファイルを選びます。ソース版なら Codex にバックグラウンドで N.E.K.O を起動してもらい、Plugins 画面で「Refresh → Start」を押します。',
            image: '/images/plugins/onboarding/neko-run-verify.svg',
            imageAlt: 'N.E.K.O のプラグイン画面で water_reminder を起動して実行する画面イメージ',
            caption: '機能を追加したいときはチャットで指示し、Reload を押すだけですぐ反映されます',
          },
        ],
      },
      {
        id: 'dsh',
        tabLabel: 'DeepSeek Harness 画面付きガイド',
        badge: 'オープンソース · 公式デスクトップアプリ',
        summary:
          'DeepSeek 製の Harness デスクトップアプリを使い、空のフォルダから公式 neko-plugin ツールでプラグインを作成する手順です。',
        prereq: 'DeepSeek API キーのみ。あとの環境は AI が自分で用意します。',
        steps: [
          {
            title: 'DeepSeek Harness デスクトップ版をインストールする',
            body: '空の作業フォルダを用意し、公式リリースページから DeepSeek Harness デスクトップ版をダウンロード・インストールして開きます。',
            image: '/images/plugins/onboarding/dsh-1-launch.svg',
            imageAlt: 'DeepSeek Harness デスクトップアプリの起動画面イメージ',
            caption: 'デスクトップアプリを開くだけですぐ使えます',
          },
          {
            title: 'Settings → Models で API キーを保存する',
            body: '右上の「Settings → Models」を開き、DeepSeek API キーを入力して「Save」をクリックします。',
            image: '/images/plugins/onboarding/dsh-2-models.svg',
            imageAlt: 'Settings Models 画面で API キーを保存するイメージ',
            caption: '再起動なしでそのままモデルが利用可能になります',
          },
          {
            title: 'Choose workspace で作業フォルダを選択する',
            body: '「Choose workspace」から作業フォルダを追加・選択すると、チャット入力欄が有効になります。',
            image: '/images/plugins/onboarding/dsh-3-workspace.svg',
            imageAlt: 'Choose workspace からフォルダを選択するイメージ',
            caption: '選択したフォルダ内で AI がプラグイン開発を進めます',
          },
          {
            title: 'Setup Prompt を貼り付け、AI の操作を許可する',
            body: '下のボタンで Setup Prompt をコピーして送信し、AI が操作の実行を求めてきたら「Approve」をクリックします：',
            includeExamplePrompt: true,
            image: '/images/plugins/onboarding/dsh-4-prompt.svg',
            imageAlt: 'Setup Prompt を送信して Approve を押すイメージ',
            caption: 'AI が N.E.K.O の準備と water_reminder の作成を自動で進めます',
          },
          {
            title: 'チェックとテストの完了を待つ',
            body: 'AI がコードを編集し、公式 neko-plugin ツールのチェックとテストをすべてパスさせます。',
            image: '/images/plugins/onboarding/dsh-5-check.svg',
            imageAlt: 'チェックが 0 error(s) で完了した画面イメージ',
            caption: '未公開段階の 4 warning(s) はローカル動作に影響しません',
          },
          {
            title: 'N.E.K.O で起動・動作確認する',
            body: 'AI に .neko-plugin パッケージを作ってもらい Plugin Center からインポートするか、ソース版なら AI にバックグラウンドで起動してもらい Plugins 画面で「Refresh → Start」を押します。',
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
      badge: 'Official Desktop App · ChatGPT Sign-In',
      summary:
        'Start from an empty folder in the OpenAI Codex desktop app, paste one Setup Prompt, and watch it scaffold, code, and validate a Water Reminder plugin with the official neko-plugin tool.',
      prereq: 'Just a ChatGPT account to sign in — the AI installs everything else itself.',
      steps: [
        {
          title: 'Open Codex on an empty workspace folder',
          body: 'Install the Codex desktop app, sign in with your ChatGPT account, and open a new empty folder (for example neko-work) or your existing N.E.K.O checkout as the workspace.',
          image: '/images/plugins/onboarding/codex-1-open.svg',
          imageAlt: 'Opening an empty workspace folder in the Codex desktop app',
          caption: 'Point Codex at your empty workspace or existing N.E.K.O checkout',
        },
        {
          title: 'Copy and send the Setup Prompt',
          body: 'Click the button below to copy the Setup Prompt pre-filled with the Water Reminder example, then paste it into Codex:',
          includeExamplePrompt: true,
          image: '/images/plugins/onboarding/codex-2-prompt.svg',
          imageAlt: 'Pasting the N.E.K.O Setup Prompt into OpenAI Codex',
          caption: 'The prompt teaches Codex the whole neko-plugin workflow and SDK rules automatically',
        },
        {
          title: 'Approve repo setup and plugin scaffolding',
          body: 'Click Allow when Codex asks to prepare the repository, sync dependencies, and create the water_reminder plugin.',
          image: '/images/plugins/onboarding/codex-3-init.svg',
          imageAlt: 'Codex preparing the environment and scaffolding water_reminder',
          caption: 'Codex generates the official scaffold inside plugin/plugins/water_reminder/',
        },
        {
          title: 'Let Codex implement features and pass checks',
          body: 'Codex reads the local docs/plugins/ files, writes async @plugin_entry and @llm_tool handlers, and runs the plugin checks, lint, and tests.',
          image: '/images/plugins/onboarding/codex-4-check.svg',
          imageAlt: 'Codex passing plugin checks, lint, and tests with 0 errors',
          caption: '0 error(s) confirms the manifest, entry class, and tests are ready',
        },
        {
          title: 'Test live in N.E.K.O or import the .neko-plugin package',
          body: 'Let Codex build the .neko-plugin package, then click Import in N.E.K.O Plugin Center and pick that file. On a source checkout, let Codex launch N.E.K.O in the background and click Refresh -> Start on the Plugins page.',
          image: '/images/plugins/onboarding/neko-run-verify.svg',
          imageAlt: 'Starting and triggering water_reminder on the N.E.K.O Plugins page',
          caption: 'Want another feature? Just ask Codex in plain English and click Reload!',
        },
      ],
    },
    {
      id: 'dsh',
      tabLabel: 'DeepSeek Harness Walkthrough',
      badge: 'Open-Source Agent · Official Desktop App',
      summary:
        'Drive the entire neko-plugin workflow from the official DeepSeek Harness desktop app, starting from an empty folder.',
      prereq: 'Just a DeepSeek API key — the AI installs everything else itself.',
      steps: [
        {
          title: 'Install DeepSeek Harness Desktop',
          body: 'Create an empty folder, download DeepSeek Harness Desktop from the official releases page, install it, and open the app.',
          image: '/images/plugins/onboarding/dsh-1-launch.svg',
          imageAlt: 'DeepSeek Harness Desktop welcome window',
          caption: 'The desktop app is ready to use as soon as it opens',
        },
        {
          title: 'Save your API key in Settings -> Models',
          body: 'Open Settings -> Models in the top bar, paste your DeepSeek API key, and click Save.',
          image: '/images/plugins/onboarding/dsh-2-models.svg',
          imageAlt: 'Configuring a DeepSeek API key in Settings -> Models',
          caption: 'The model route becomes active immediately without restarting',
        },
        {
          title: 'Select your folder with Choose workspace',
          body: 'Click Choose workspace, add your empty folder (or existing N.E.K.O checkout), and select it to unlock the composer.',
          image: '/images/plugins/onboarding/dsh-3-workspace.svg',
          imageAlt: 'Selecting the local folder in Choose workspace',
          caption: 'Once a workspace is active, the AI develops the plugin inside that folder',
        },
        {
          title: 'Paste the Setup Prompt and approve what the AI runs',
          body: 'Copy the pre-filled Water Reminder Setup Prompt below, send it in the chat box, and click Approve whenever the AI asks to run something:',
          includeExamplePrompt: true,
          image: '/images/plugins/onboarding/dsh-4-prompt.svg',
          imageAlt: 'Sending the Setup Prompt and approving the AI\'s actions',
          caption: 'The AI prepares N.E.K.O and scaffolds water_reminder automatically',
          tip: 'The first repository setup downloads dependencies and may take 1-3 minutes.',
        },
        {
          title: 'Verify 0 errors from checks and tests',
          body: 'The AI edits plugin/plugins/water_reminder/, updates tests, and keeps running the official neko-plugin checks until everything passes.',
          image: '/images/plugins/onboarding/dsh-5-check.svg',
          imageAlt: 'The AI finishing all plugin checks with 0 errors',
          caption: 'The 4 initial git/hook warnings on an unpublished project are expected and harmless',
        },
        {
          title: 'Start the plugin in N.E.K.O',
          body: 'Let the AI build the .neko-plugin package, then click Import in N.E.K.O Plugin Center and pick that file. On a source checkout, let the AI launch N.E.K.O in the background and click Refresh -> Start on the Plugins page.',
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
