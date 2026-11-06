import type { PageGuide } from '@/components/authoring/publishGuide'
import type { PublishPageId } from '@/components/authoring/publishFlow'

/** 发布页每一页的新手说明（简体中文）。 */
export const zhPages: Record<PublishPageId, PageGuide> = {
  toolchain: {
    summary:
      '发布之前，先确认这台电脑装好了需要的工具，并且有一个 GitHub 账号。这一页只做检查，不会修改任何东西。全部变成"已就绪"后，点右下角「下一步」。',
    sections: [
      {
        title: '整个发布流程是什么样的？',
        paragraphs: [
          '把插件发布到 Market，大致是这样一条路：你的插件代码 → 上传到你自己 GitHub 账号下的公开仓库 → 自动检查通过 → 向 Market 投稿、等待人工审核 → 审核通过后打一个版本号，GitHub 自动打包出安装文件（Release）→ 通知 Market 上架这个版本。',
          '本页面把这条路拆成 0 到 5 共六页，按顺序一页一页完成即可。任何一页都可以先点进去看看，不会误操作；真正会对外公开的操作，点击前都会再次确认。',
        ],
      },
      {
        title: '必须的工具和账号',
        steps: [
          'Git：记录和上传代码。',
          'Git 用户名和邮箱：每次提交都要署名。',
          'uv：运行和 Market 一样的代码检查。',
          'N.E.K.O 源码版：检查和发布由它在后台执行。',
        ],
      },
      {
        title: '推荐的工具',
        steps: [
          'GitHub CLI（gh）：一键创建仓库，并让读取 GitHub 更稳定。',
          'GitHub 账号，并用 gh 登录：仓库要建在你的账号下。没装 gh 时也需要账号，只是要在网页上操作。',
        ],
      },
      {
        title: '怎么打开"终端"？',
        paragraphs: [
          '下面的安装命令需要在终端里运行。Windows：按 Win 键，输入 PowerShell，回车。macOS：按 Command + 空格，输入"终端"，回车。把命令复制进去，按回车运行。',
          '装完新工具后，一定要关掉并重新打开 N.E.K.O 和终端，否则它们找不到刚装的程序。',
        ],
      },
    ],
  },
  repository: {
    summary:
      '插件的代码要放在你自己 GitHub 账号下的一个公开仓库里，并且本机最新的改动都已经提交、上传。下面的"工作区"列出了还没提交的文件和最近的提交记录。',
    sections: [
      {
        title: '几个名词',
        steps: [
          '仓库（repository）：一个项目的全部代码和历史记录。本机有一份，GitHub 上有一份。',
          '提交（commit）：把当前的改动存成一个"存档点"，附一句说明。',
          '推送（push）：把本机的提交上传到 GitHub。',
          'origin：本机仓库里记录的"GitHub 上那一份"的地址。',
        ],
      },
      {
        title: '还没有 GitHub 仓库时',
        paragraphs: [
          '装了 gh 并已登录的话，直接点下面的「创建 GitHub 仓库」，会在你的账号下创建一个公开仓库并自动关联。没装 gh 时，按提示在 github.com 网页上新建一个空仓库（不要勾选添加 README），再把下面命令里的地址换成你的仓库地址：',
        ],
        commands: [
          { label: '关联 GitHub 仓库', command: 'git remote add origin https://github.com/你的用户名/n.e.k.o_plugin_{pluginId}.git' },
          { label: '第一次上传', command: 'git push -u origin {branch}' },
        ],
      },
      {
        title: '日常：提交并上传改动',
        paragraphs: [
          '每次改完代码，在插件文件夹里（点页面顶部的「打开文件夹」，在地址栏输入 powershell 回车即可在该目录打开终端）依次运行：',
        ],
        commands: [
          { label: '1. 把所有改动加入下一次提交', command: 'git add -A' },
          { label: '2. 提交，并写一句说明', command: 'git commit -m "说明这次改了什么"' },
          { label: '3. 上传到 GitHub', command: 'git push' },
        ],
      },
      {
        title: '常见问题',
        steps: [
          '"有未提交的改动"：说明有文件改过但没提交，按上面三步操作。若某些文件不该上传（如临时文件、密钥），把它们写进 .gitignore 文件。',
          '"HEAD 还没推送"：已提交但没上传，运行 git push。',
          '"分支没有上游"：第一次上传要用 git push -u origin {branch}。',
          '"release.yml 不是最新"：在 N.E.K.O 源码目录运行 uv run neko-plugin setup-repo "{dir}" --upgrade-github-actions，然后提交并上传。',
          '"仓库不是公开的"：打开 GitHub 仓库页面 → Settings → 最下方 Danger Zone → Change visibility → Public。',
        ],
      },
    ],
  },
  verify: {
    summary:
      '每次上传后，GitHub 会自动运行一遍和 Market 相同的检查（验证工作流）。这一页的"本地检查"可以在上传前先在本机跑一遍，提前发现问题。',
    sections: [
      {
        title: '本地检查和 GitHub 检查有什么区别？',
        paragraphs: [
          '两者运行的是同一套规则：代码风格检查（ruff）、依赖同步、发布前检查（测试、打包、校验）。本地检查用的是你电脑上当前的文件，包括还没提交的；GitHub 检查用的是已经上传的那个提交。这一步的状态以 GitHub 的结果为准。',
          '本地检查通常需要几十秒到几分钟，第一次会下载工具，会更久一些。',
        ],
      },
      {
        title: '检查没通过怎么办？',
        steps: [
          '展开标着"未通过"的那一项，看里面的输出。通常最后几行会说明是哪个文件、哪一行出了问题。',
          '"代码检查"未通过：多数是导入顺序或未使用的变量，可以在插件目录运行 uvx ruff==0.12.4 check --fix . 自动修复一部分。',
          '"依赖同步"未通过：检查 pyproject.toml 里声明的依赖名字和版本是否写对。',
          '"发布检查"未通过：多为测试失败或插件信息不完整，按输出提示修改。',
          '修好后提交并上传，GitHub 会自动重新检查；回到这里点「刷新状态」查看结果。',
        ],
      },
      {
        title: '在 GitHub 上查看检查结果',
        paragraphs: [
          '打开 https://github.com/{repo}/actions ，最上面一条就是最新一次检查。绿色对勾表示通过，红色叉表示失败，点进去可以看到详细日志。',
        ],
      },
    ],
  },
  submission: {
    summary:
      '第一次发布插件时，需要先向 Market 投稿，由审核员人工检查你的仓库。审核通过后，才能发布正式版本。以后发布新版本，不需要再次投稿。',
    sections: [
      {
        title: '怎么投稿？',
        steps: [
          '登录 Market 账号（没有账号的话，登录页可以注册）。Market 要求邮箱已验证。',
          '点「创建草稿」。草稿会自动填好插件名称和简介，你可以修改。',
          '选择一个分区，至少选一个标签。新插件没有分区和标签无法通过审核。',
          '确认无误后点「提交审核」。审核员会看到你的公开仓库地址。',
        ],
      },
      {
        title: '提交之后',
        steps: [
          '"已提交 / 审核中"：耐心等待，可以随时回到这里点「刷新状态」查看。',
          '"需要你处理"：审核员留了修改意见，意见会显示在这里。按意见改代码、提交并上传，然后提交新一稿。',
          '"已通过"：可以进入下一步了。',
          '"已拒绝 / 已撤回"：可以修改后重新创建草稿再次投稿。',
        ],
      },
      {
        title: '为什么建议在审核通过后才发布 Release？',
        paragraphs: [
          'Market 审核的是某一个具体的提交。插件的第一个版本如果正好用这个审核通过的提交打包，会立即上架；如果用的是别的提交，就会再走一次人工审核。所以审核期间尽量不要再改代码，审核通过后直接进入第 4 步。',
        ],
      },
    ],
  },
  release: {
    summary:
      'Release 是 GitHub 上的一个"正式版本"。这一步会给当前提交打上版本号标签 {tag}，GitHub 随后自动构建安装包、检查报告和发布证据三个文件，附在这个 Release 上。',
    sections: [
      {
        title: '两个按钮有什么区别？',
        steps: [
          '「发布到 GitHub + Market」（推荐）：创建 Release，等文件构建完成后，自动通知 Market 上架。一步到位。',
          '「仅创建 GitHub Release」：只创建 Release，不通知 Market。之后需要到第 5 步手动通知。',
        ],
      },
      {
        title: '版本号从哪里来？',
        paragraphs: [
          '版本号来自插件的 plugin.toml 里的 version（当前是 {version}），标签名是在前面加一个 v，即 {tag}。每个版本号只能发布一次。要发新版本时，先在「概览」页编辑版本号（比如从 1.0.0 改成 1.0.1），提交并上传，再回到这里发布。',
        ],
      },
      {
        title: '发布过程中会发生什么？',
        steps: [
          '本机再跑一遍依赖同步和代码检查。',
          '把标签 {tag} 上传到 GitHub。',
          'GitHub 自动构建三个文件，通常需要几分钟。本页会显示进度和日志，可以随时离开，回来后进度仍在。',
          '如果选择了同时通知 Market，构建完成后会自动通知。',
        ],
        paragraphs: ['已经上传的标签不会因为取消任务而撤回。如果需要重来，最简单的办法是提升版本号。'],
      },
      {
        title: '发布失败了？',
        steps: [
          '展开"命令输出"，看最后几行 [FAIL] 开头的说明。',
          '"tag 已指向其他提交"：这个版本号已经用过了，请提升版本号。',
          '"Release 缺少文件"：构建还没完成或失败了，打开 https://github.com/{repo}/actions 查看 Release 工作流的日志。',
        ],
      },
    ],
  },
  market: {
    summary:
      '最后一步：告诉 Market"这个版本已经准备好了"。Market 会下载并校验 Release 里的文件，通过后这个版本就会出现在 Market 上，所有用户都可以安装。',
    sections: [
      {
        title: '什么时候需要手动通知？',
        paragraphs: [
          '如果第 4 步用的是「发布到 GitHub + Market」，通常会自动完成这一步，不需要再点。只有用了「仅创建 GitHub Release」，或者自动通知失败了，才需要在这里点「通知 Market」。',
        ],
      },
      {
        title: 'Market 的几种答复',
        steps: [
          '"已上架"：完成！可以在 Market 里搜索到你的插件了。',
          '"转入人工审核"：这个版本不是用审核通过的提交构建的，需要审核员再看一次，请耐心等待。',
          '"被拒绝"：说明里有原因。修改后提升版本号，从第 4 步重新发布。',
        ],
      },
      {
        title: '以后怎么发布新版本？',
        steps: [
          '改代码，提交并上传（第 1 步）。',
          '等 GitHub 检查通过（第 2 步）。',
          '在「概览」页提升版本号，提交并上传。',
          '回到第 4 步发布。投稿只需要第一次做。',
        ],
      },
    ],
  },
}
