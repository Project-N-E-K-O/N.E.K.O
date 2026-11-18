import type { PublishGuide } from '@/components/authoring/publishGuide'
import { enPages } from '@/components/authoring/publishGuide.enPages'

export const enUS: PublishGuide = {
  labels: {
    guide: 'Beginner guide',
    copy: 'Copy',
    copied: 'Copied',
    copyFailed: 'Could not copy. Select the command and copy it by hand.',
    howToInstall: 'How to install',
    afterInstall: 'After installing',
  },
  pages: enPages,
  tools: {
    git: {
      purpose:
        'Git is a version control tool. It records each set of changes as a commit and uploads (pushes) commits to GitHub. Your plugin source, its version tags and the code the Market reviews all travel through Git.',
      install: [
        {
          platform: 'Windows',
          commands: ['winget install --id Git.Git -e'],
          note: 'Or download the installer from git-scm.com and keep the default options.',
        },
        { platform: 'macOS', commands: ['xcode-select --install'], note: 'Or with Homebrew: brew install git' },
        { platform: 'Linux (Debian / Ubuntu)', commands: ['sudo apt install git'] },
      ],
      after: [
        'Close and reopen N.E.K.O (and any terminal) so the new git is found.',
        'Come back to this page and click Refresh status; Git turns Ready.',
      ],
      links: [{ label: 'Git downloads', url: 'https://git-scm.com/downloads' }],
    },
    gitIdentity: {
      purpose:
        'Every commit records who made it. Git needs your name and email once, or committing fails. Both appear publicly in the GitHub history, so use your GitHub username and the email of your GitHub account.',
      install: [
        {
          platform: 'All systems',
          commands: [
            'git config --global user.name "Your Name"',
            'git config --global user.email "you@example.com"',
          ],
          note: 'Replace the quoted values with your own. You only do this once for all projects.',
        },
      ],
      after: ['No restart needed; click Refresh status.'],
    },
    uv: {
      purpose:
        'uv manages Python tools. The pre-release check and the publish both use its uvx command to run the pinned ruff code checker, the same version the Market CI uses. Without uv, step 2 checks and step 4 publishing cannot run.',
      install: [
        {
          platform: 'Windows (PowerShell)',
          commands: ['powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'],
          note: 'Or: winget install --id astral-sh.uv -e',
        },
        {
          platform: 'macOS / Linux',
          commands: ['curl -LsSf https://astral.sh/uv/install.sh | sh'],
          note: 'With Homebrew: brew install uv',
        },
      ],
      after: [
        'Reopen N.E.K.O so uv and uvx are on the PATH.',
        'The first check downloads ruff through uvx; it needs the internet and takes a moment.',
      ],
      links: [{ label: 'uv installation guide', url: 'https://docs.astral.sh/uv/getting-started/installation/' }],
    },
    gh: {
      purpose:
        'The GitHub CLI (command gh) is GitHub\'s official command-line tool. With it, step 1 creates your GitHub repository in one click, and its sign-in lets this page read GitHub beyond the anonymous limit of 60 requests an hour. You can publish without it; you then create the repository on the website.',
      install: [
        { platform: 'Windows', commands: ['winget install --id GitHub.cli -e'] },
        { platform: 'macOS', commands: ['brew install gh'] },
        {
          platform: 'Linux',
          commands: ['sudo apt install gh'],
          note: 'If your package sources lack it, add GitHub\'s source as the official guide shows.',
        },
      ],
      after: ['Reopen N.E.K.O.', 'Then complete "GitHub account" below: sign in with gh.'],
      links: [{ label: 'GitHub CLI', url: 'https://cli.github.com/' }],
    },
    githubAccount: {
      purpose:
        'Your plugin source lives in a public repository under your own GitHub account; the Market reads, reviews and downloads it from there. You need a GitHub account, and gh on this machine signed in to it.',
      install: [
        {
          platform: 'Step 1: create an account',
          commands: [],
          note: 'Open github.com, click Sign up and verify your email. Skip this if you have an account.',
        },
        {
          platform: 'Step 2: sign in on this machine',
          commands: ['gh auth login'],
          note:
            'Choose GitHub.com → HTTPS → Yes to authenticate Git with your GitHub credentials → Login with a web browser. Enter the 8-character code it shows in the browser and authorize.',
        },
        {
          platform: 'Check the sign-in',
          commands: ['gh auth status'],
          note: '"Logged in to github.com account <name>" means it worked.',
        },
      ],
      after: ['Click Refresh status; your GitHub username shows here.'],
      links: [
        { label: 'Sign up for GitHub', url: 'https://github.com/signup' },
        { label: 'gh auth login manual', url: 'https://cli.github.com/manual/gh_auth_login' },
      ],
    },
  },
}
