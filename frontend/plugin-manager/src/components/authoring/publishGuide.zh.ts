import type { PublishGuide } from '@/components/authoring/publishGuide'
import { zhPages } from '@/components/authoring/publishGuide.zhPages'

export const zhCN: PublishGuide = {
  labels: {
    guide: '新手说明',
    copy: '复制',
    copied: '已复制',
    copyFailed: '复制失败，请手动选中命令复制。',
    howToInstall: '怎么安装',
    afterInstall: '装好之后',
  },
  pages: zhPages,
  tools: {
    git: {
      purpose:
        'Git 是"版本管理"工具：它把你每一次改动记录成一个"提交（commit）"，再把这些提交上传（push）到 GitHub。插件的源码、版本号标签（tag）、以及 Market 审核时看的代码，都是通过 Git 传上去的。',
      install: [
        {
          platform: 'Windows',
          commands: ['winget install --id Git.Git -e'],
          note: '也可以去 git-scm.com 下载安装包，一路"下一步"即可。安装时保持默认选项就行。',
        },
        { platform: 'macOS', commands: ['xcode-select --install'], note: '或者用 Homebrew：brew install git' },
        { platform: 'Linux（Debian / Ubuntu）', commands: ['sudo apt install git'] },
      ],
      after: [
        '关闭并重新打开 N.E.K.O（以及所有终端窗口），让新装的 git 生效。',
        '回到这一页，点「刷新状态」，Git 这一项会变成"已就绪"。',
      ],
      links: [{ label: 'Git 官网下载页', url: 'https://git-scm.com/downloads' }],
    },
    gitIdentity: {
      purpose:
        '每个提交都要写明"是谁做的"。Git 需要你先设置一次用户名和邮箱，否则提交会直接失败。这里的名字和邮箱会公开显示在 GitHub 的提交记录里，建议用你的 GitHub 用户名和 GitHub 账号邮箱。',
      install: [
        {
          platform: '所有系统',
          commands: [
            'git config --global user.name "你的名字"',
            'git config --global user.email "you@example.com"',
          ],
          note: '把引号里的内容换成你自己的。只需要设置一次，以后所有项目都会用它。',
        },
      ],
      after: ['设置后不需要重启，直接点「刷新状态」即可。'],
    },
    uv: {
      purpose:
        'uv 是 Python 的工具管理器。发布前的检查和正式发布都会用它自带的 uvx 运行固定版本的代码检查工具 ruff，这和 Market 的自动审核用的是同一个版本。没有 uv，第 2 步的检查和第 4 步的发布都无法运行。',
      install: [
        {
          platform: 'Windows（PowerShell）',
          commands: ['powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'],
          note: '也可以用 winget install --id astral-sh.uv -e',
        },
        {
          platform: 'macOS / Linux',
          commands: ['curl -LsSf https://astral.sh/uv/install.sh | sh'],
          note: '用 Homebrew 的话：brew install uv',
        },
      ],
      after: [
        '重新打开 N.E.K.O，让 uv 和 uvx 出现在系统路径（PATH）里。',
        '第一次运行检查时，uvx 会自动下载 ruff，需要联网，稍等片刻即可。',
      ],
      links: [{ label: 'uv 官方安装说明', url: 'https://docs.astral.sh/uv/getting-started/installation/' }],
    },
    gh: {
      purpose:
        'GitHub CLI（命令是 gh）是 GitHub 官方的命令行工具。有了它，第 1 步可以一键帮你在 GitHub 上创建仓库；它登录后的身份还能让本页读取 GitHub 时不受"每小时 60 次"的匿名访问限制。没有它也能发布，只是创建仓库要你去网页上手动操作。',
      install: [
        { platform: 'Windows', commands: ['winget install --id GitHub.cli -e'] },
        { platform: 'macOS', commands: ['brew install gh'] },
        {
          platform: 'Linux',
          commands: ['sudo apt install gh'],
          note: '如果软件源里没有，按官网说明添加 GitHub 的软件源。',
        },
      ],
      after: ['重新打开 N.E.K.O。', '然后完成下面"GitHub 账号"这一项：用 gh 登录。'],
      links: [{ label: 'GitHub CLI 官网', url: 'https://cli.github.com/' }],
    },
    githubAccount: {
      purpose:
        '插件的源码要放在你自己 GitHub 账号下的一个公开仓库里，Market 会从那里读取代码、审核、下载安装包。所以你需要一个 GitHub 账号，并让本机的 gh 登录这个账号。',
      install: [
        {
          platform: '第一步：注册账号',
          commands: [],
          note: '打开 github.com，点 Sign up 注册，验证邮箱。已经有账号可以跳过。',
        },
        {
          platform: '第二步：在本机登录',
          commands: ['gh auth login'],
          note:
            '在终端运行后依次选择：GitHub.com → HTTPS → 同意用 GitHub 凭据认证 Git（Y）→ Login with a web browser。终端会显示一个 8 位验证码，浏览器打开后输入它并授权。',
        },
        {
          platform: '检查是否登录成功',
          commands: ['gh auth status'],
          note: '看到 "Logged in to github.com account 你的用户名" 就成功了。',
        },
      ],
      after: ['回到这一页点「刷新状态」，这里会显示你登录的 GitHub 用户名。'],
      links: [
        { label: '注册 GitHub', url: 'https://github.com/signup' },
        { label: 'gh auth login 说明', url: 'https://cli.github.com/manual/gh_auth_login' },
      ],
    },
    sourceCheckout: {
      purpose:
        '检查、创建仓库和发布，都是由 N.E.K.O 在后台运行 neko-plugin 命令完成的，这需要 N.E.K.O 以源码方式运行（从 Git 仓库克隆下来、用 uv 启动）。打包好的安装版没有这些工具，因此无法在这里发布。',
      install: [
        {
          platform: '获取 N.E.K.O 源码并运行',
          commands: [
            'git clone https://github.com/Project-N-E-K-O/N.E.K.O.git',
            'cd N.E.K.O',
            'uv sync',
          ],
          note: '然后按仓库 README 的说明，用源码方式启动 N.E.K.O，再回到插件工作台。',
        },
      ],
      links: [{ label: 'N.E.K.O 仓库', url: 'https://github.com/Project-N-E-K-O/N.E.K.O' }],
    },
  },
}
