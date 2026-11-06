import type { PageGuide } from '@/components/authoring/publishGuide'
import type { PublishPageId } from '@/components/authoring/publishFlow'

/** The beginner guide of each publish page (English; other locales without their own use it). */
export const enPages: Record<PublishPageId, PageGuide> = {
  toolchain: {
    summary:
      'Before publishing, make sure this computer has the tools it needs and that you have a GitHub account. This page only checks; it changes nothing. Once everything is Ready, click Next at the bottom right.',
    sections: [
      {
        title: 'What does publishing involve?',
        paragraphs: [
          'Your plugin code → a public repository under your own GitHub account → automatic checks pass → a Market submission and human review → after approval, a version tag, from which GitHub builds the install files (a Release) → the Market is notified and lists that version.',
          'This tab splits that path into pages 0 to 5. Work through them in order. You can open any page to look around without risk; every action that makes something public asks you to confirm first.',
        ],
      },
      {
        title: 'Required tools',
        steps: [
          'Git: records and uploads your code.',
          'Git name and email: every commit is signed with them.',
          'uv: runs the same code checks as the Market.',
          'N.E.K.O from source: runs the checks and the publish in the background.',
        ],
      },
      {
        title: 'Recommended',
        steps: [
          'GitHub CLI (gh): creates the repository in one click and makes GitHub reads more reliable.',
          'A GitHub account signed in through gh: the repository lives under your account. Without gh you still need the account, and use the website instead.',
        ],
      },
      {
        title: 'How do I open a terminal?',
        paragraphs: [
          'The install commands below run in a terminal. Windows: press the Windows key, type PowerShell, press Enter. macOS: press Command + Space, type Terminal, press Enter. Paste a command and press Enter.',
          'After installing a tool, close and reopen N.E.K.O and the terminal, or they will not find it.',
        ],
      },
    ],
  },
  repository: {
    summary:
      'The plugin code must be in a public repository under your own GitHub account, with every local change committed and pushed. The Workspace below lists files not committed yet and the latest commits.',
    sections: [
      {
        title: 'A few words',
        steps: [
          'Repository: all the code of a project and its history. One copy is on this computer, one on GitHub.',
          'Commit: saves the current changes as a checkpoint with a short message.',
          'Push: uploads your commits to GitHub.',
          'origin: the address of the GitHub copy, as recorded in the local repository.',
        ],
      },
      {
        title: 'No GitHub repository yet',
        paragraphs: [
          'With gh installed and signed in, click Create GitHub repository below: it creates a public repository under your account and connects it. Without gh, create an empty repository on github.com (do not add a README), then run these with your repository address:',
        ],
        commands: [
          { label: 'Connect the GitHub repository', command: 'git remote add origin https://github.com/<your-name>/n.e.k.o_plugin_{pluginId}.git' },
          { label: 'First upload', command: 'git push -u origin {branch}' },
        ],
      },
      {
        title: 'Every time: commit and push your changes',
        paragraphs: [
          'After changing code, open a terminal in the plugin folder (click Open folder at the top, then type powershell in the address bar and press Enter) and run:',
        ],
        commands: [
          { label: '1. Stage every change for the next commit', command: 'git add -A' },
          { label: '2. Commit with a short message', command: 'git commit -m "What this change does"' },
          { label: '3. Upload to GitHub', command: 'git push' },
        ],
      },
      {
        title: 'Common problems',
        steps: [
          '"Uncommitted changes": some files changed but are not committed; do the three steps above. Files that must not be uploaded (temporary files, secrets) belong in .gitignore.',
          '"HEAD is not pushed": committed but not uploaded; run git push.',
          '"Branch has no upstream": the first upload needs git push -u origin {branch}.',
          '"release.yml is not current": in the N.E.K.O source folder run uv run neko-plugin setup-repo "{dir}" --upgrade-github-actions, then commit and push.',
          '"Repository is not public": on the repository page open Settings → Danger Zone at the bottom → Change visibility → Public.',
        ],
      },
    ],
  },
  verify: {
    summary:
      'After every push, GitHub runs the same checks as the Market (the verify workflow). The local check on this page runs them on this computer before you push, so problems show up earlier.',
    sections: [
      {
        title: 'Local check or GitHub check?',
        paragraphs: [
          'Both run the same rules: code style (ruff), dependency sync, and the release check (tests, package, hash). The local check uses the files on this computer, including uncommitted ones; GitHub checks the pushed commit. This step\'s status follows GitHub\'s result.',
          'A local check takes from tens of seconds to a few minutes; the first run downloads tools and takes longer.',
        ],
      },
      {
        title: 'The check failed',
        steps: [
          'Expand the item marked Failed and read its output; the last lines usually name the file and line.',
          'Lint failed: often import order or an unused variable. uvx ruff==0.12.4 check --fix . in the plugin folder fixes part of it.',
          'Dependency sync failed: check the dependency names and versions in pyproject.toml.',
          'Release check failed: usually a failing test or missing plugin information; follow the output.',
          'Commit and push the fix; GitHub checks again on its own. Click Refresh status here to see the result.',
        ],
      },
      {
        title: 'See the check on GitHub',
        paragraphs: [
          'Open https://github.com/{repo}/actions; the top entry is the latest check. A green check mark is a pass, a red cross a failure; open it for the full log.',
        ],
      },
    ],
  },
  submission: {
    summary:
      'The first time a plugin is published, it is submitted to the Market and a reviewer checks your repository by hand. Only after approval can a version be published. Later versions need no new submission.',
    sections: [
      {
        title: 'How to submit',
        steps: [
          'Sign in to the Market (the sign-in page can create an account). The Market requires a verified email.',
          'Click Create a draft. It is pre-filled with the plugin name and description, which you can edit.',
          'Choose a zone and at least one tag; a new plugin cannot be approved without them.',
          'Click Submit for review. Reviewers see your public repository address.',
        ],
      },
      {
        title: 'After submitting',
        steps: [
          'Submitted / In review: wait; click Refresh status any time to check.',
          'Needs your action: the reviewer left feedback, shown here. Change the code, commit and push, then send a new revision.',
          'Approved: go on to the next step.',
          'Rejected / Withdrawn: fix it and create a new draft to submit again.',
        ],
      },
      {
        title: 'Why publish the Release only after approval?',
        paragraphs: [
          'The Market approves one specific commit. If the plugin\'s first version is built from that commit, it is listed at once; built from another commit, it goes to human review again. So avoid changing code during review, and go straight to step 4 once approved.',
        ],
      },
    ],
  },
  release: {
    summary:
      'A Release is a published version on GitHub. This step tags the current commit with the version {tag}; GitHub then builds the package, the check report and the release evidence and attaches them to the Release.',
    sections: [
      {
        title: 'Which button?',
        steps: [
          'Publish to GitHub + Market (recommended): creates the Release and, once its files are built, notifies the Market to list it.',
          'GitHub Release only: creates the Release without notifying the Market; notify it from step 5 afterwards.',
        ],
      },
      {
        title: 'Where does the version come from?',
        paragraphs: [
          'From version in the plugin\'s plugin.toml (now {version}); the tag adds a v in front: {tag}. Each version can be published once. For a new version, edit it on the Overview tab (say 1.0.0 to 1.0.1), commit and push, then publish here.',
        ],
      },
      {
        title: 'What happens while publishing',
        steps: [
          'This computer runs dependency sync and the code check again.',
          'The tag {tag} is pushed to GitHub.',
          'GitHub builds the three files, usually in a few minutes. Progress and the log show here; you can leave and come back.',
          'With the Market option, the Market is notified once the build finishes.',
        ],
        paragraphs: ['Canceling the task does not take back a pushed tag. To start over, the simplest way is a new version.'],
      },
      {
        title: 'The publish failed',
        steps: [
          'Expand Command output and read the [FAIL] lines near the end.',
          '"Tag points at another commit": this version was used already; bump the version.',
          '"Release is missing assets": the build is not done or failed; open https://github.com/{repo}/actions for the Release workflow log.',
        ],
      },
    ],
  },
  market: {
    summary:
      'The last step tells the Market this version is ready. The Market downloads and verifies the Release files; then the version appears on the Market and anyone can install it.',
    sections: [
      {
        title: 'When do I notify by hand?',
        paragraphs: [
          'Publish to GitHub + Market in step 4 usually does this for you. Notify here only after GitHub Release only, or when the automatic notification failed.',
        ],
      },
      {
        title: 'The Market\'s answers',
        steps: [
          'Listed: done! Your plugin can be found on the Market.',
          'Sent to human review: this version was not built from the approved commit, so a reviewer looks again. Please wait.',
          'Rejected: the reason is shown. Fix it, bump the version and publish again from step 4.',
        ],
      },
      {
        title: 'Publishing a new version later',
        steps: [
          'Change the code, commit and push (step 1).',
          'Wait for the GitHub check to pass (step 2).',
          'Bump the version on the Overview tab, commit and push.',
          'Publish from step 4. The submission is only needed the first time.',
        ],
      },
    ],
  },
}
