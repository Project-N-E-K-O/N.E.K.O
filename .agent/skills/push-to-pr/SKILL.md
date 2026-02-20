---
name: push-to-pr
description: Push commits to an existing GitHub PR's source branch. NEVER create new branches. Use when the user says "push to PR #N", "push到PR", or any variation meaning to push local commits onto an existing pull request. Covers finding the PR's head branch via GitHub API, setting up the correct remote, and pushing with HEAD:<branch> syntax.
---

# Push to PR

Push local commits to an existing PR's source branch. **Never create new branches.**

## Workflow

### 1. Find PR head branch

```bash
curl -s https://api.github.com/repos/{OWNER}/{REPO}/pulls/{N} \
  | python3 -c "import sys,json; p=json.load(sys.stdin); print(f\"branch={p['head']['ref']}\nclone_url={p['head']['repo']['clone_url']}\ncan_modify={p['maintainer_can_modify']}\")"
```

- `head.ref` = branch name (e.g. `active_visual_chat`)
- `head.repo.clone_url` = fork URL
- `maintainer_can_modify` must be `true`

### 2. Ensure remote exists

```bash
git remote -v  # check if clone_url already listed
git remote add <name> <clone_url>  # only if missing
```

### 3. Fetch + rebase if needed

```bash
git fetch <remote> <branch>
# If local commits not on top:
git rebase <remote>/<branch>
```

### 4. Push

```bash
git push <remote> HEAD:<branch>
```

## Critical Rules

1. **NEVER** create new branches on any remote
2. **NEVER** `git push <remote> <local_branch>` if local branch differs from PR branch — always use `HEAD:<pr_branch>`
3. Branch name comes **only** from `head.ref` in API response
4. If `maintainer_can_modify` is `false`, inform user — cannot push
5. If push rejected (non-fast-forward), fetch and rebase first
