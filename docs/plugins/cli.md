# Publish a plugin to the N.E.K.O Plugin Market

This guide continues from the [quick start](/plugins/quick-start). It takes the plugin that already runs in a N.E.K.O source checkout through uploading its source to GitHub, passing its first review, publishing its first installable version, and releasing later updates.

The examples use the `hello_world` plugin created in the quick start. Its source stays in:

```text
N.E.K.O/plugin/plugins/hello_world/
```

This directory is both the plugin's own Git repository and the source N.E.K.O runs during development. You do not need to copy the plugin, create a symbolic link, or import an installation package into your development environment.

The complete journey is:

```text
Develop the source
  → Push it to GitHub
  → Submit the plugin for its first Market review
  → Pass review
  → Publish a GitHub Release
  → Make an installable version available in the Market
  → Keep editing the source and publish later versions
```

::: info Before you start
Unless a step explicitly uses `cd`, run the `uv run neko-plugin ...` commands below from the root of the N.E.K.O source checkout.
:::

## 1. Check that the plugin is ready to publish

Make sure the plugin directory contains at least these files:

```text
plugin/plugins/hello_world/
├── .git/
├── .github/workflows/
│   ├── verify.yml
│   └── release.yml
├── plugin.toml
├── config.example.toml
├── __init__.py
└── tests/
```

The `.git/` directory means that the plugin directory is its own Git repository. The `neko-plugin init` command in the quick start already created it. Although the plugin lives inside the N.E.K.O source tree, the plugin's commits, remote repository, and version tags belong to this nested repository.

Run the check:

```bash
uv run neko-plugin check hello_world
```

If the plugin uses third-party Python libraries, refresh its own `vendor/` directory first:

```bash
uv run --with pip neko-plugin sync hello_world --clean
uv run neko-plugin check hello_world
```

Fix every error or `[FAIL]` result before continuing. You do not need to run `sync` when the plugin has no third-party libraries.

Finally, open `plugin/plugins/hello_world/plugin.toml` and check the plugin ID and first version number:

```toml
[plugin]
id = "hello_world"
version = "0.1.0"
```

Keep the plugin ID unchanged after publication. Never reuse a version number that has already been published.

## 2. Create the GitHub repository and push the source

Create a public, empty GitHub repository named:

```text
n.e.k.o_plugin_hello_world
```

The required naming pattern is `n.e.k.o_plugin_<plugin ID>`. Do not ask GitHub to add a README, `.gitignore`, or license when creating the repository, because the plugin directory already contains the files you need to commit.

Then enter the plugin directory, commit the source, and push it:

```bash
cd plugin/plugins/hello_world
git add .
git commit -m "feat: first release"
git remote add origin https://github.com/your-name/n.e.k.o_plugin_hello_world.git
git push -u origin main
cd ../../..
```

If `origin` already exists, do not run `git remote add` again. Run this command inside the plugin directory to confirm that it points to the correct GitHub repository:

```bash
git remote -v
```

After pushing, open the repository's **Actions** page and wait for **Verify N.E.K.O Plugin** to pass. It checks the code, dependencies, plugin configuration, tests, and build output.

::: warning Commit the plugin repository
Run Git commands inside `plugin/plugins/hello_world/`. Do not push the entire N.E.K.O repository as your plugin repository.
:::

## 3. Submit the plugin for its first review

Open the [N.E.K.O Plugin Market submission page](https://market.project-neko.cn/#/upload), sign in, and follow these steps:

1. Enter the GitHub repository URL, such as `https://github.com/your-name/n.e.k.o_plugin_hello_world`.
2. Ask the page to read the repository information.
3. Check the plugin name, choose a category and one to five tags, and add a description if needed.
4. Submit the review application.
5. Open the application under your plugins to see the check results and reviewer comments.

This submits a specific revision from the GitHub repository. The Market resolves the selected branch to a full commit SHA and reviews that fixed snapshot. At this point, you have not submitted a `.neko-plugin` package or published a downloadable version.

Submit only one first-review application for a plugin. While that application is still open, do not create another application for the same repository.

### What to do when the reviewer asks for changes

If the reviewer requests changes:

1. Continue editing the source in `plugin/plugins/hello_world/`.
2. Run `neko-plugin check hello_world` again.
3. Commit and push the changes in the plugin's own repository.
4. Run `git rev-parse HEAD` and copy the new full commit SHA.
5. Return to the original application, enter the fix summary and the new commit in its revision update area.
6. Submit the new Revision.

The new Revision replaces the code snapshot waiting for review. It is not a new plugin version and does not create a Market Version.

Authors cannot submit another Revision after an application has been closed. If an application was rejected, a reviewer must reopen it before the author can submit further changes.

## 4. Wait for approval

Once the review passes, the plugin appears in the Market catalog, but it may still have no download button.

These two events are different:

| Event | Result |
| --- | --- |
| First review passes | A plugin entry exists in the Market |
| First version is published | Users can install a version of the plugin |

After approval, continue to the next step to publish the first GitHub Release and Market Version.

## 5. Publish the first installable version

First, confirm that all release changes have been committed and pushed:

```bash
cd plugin/plugins/hello_world
git status --short
git push
cd ../../..
```

`git status --short` should print nothing. Then run this command from the N.E.K.O source root:

```bash
uv run neko-plugin publish hello_world
```

The command completes these steps in order:

1. Confirms that the plugin directory has its own `.git/` directory and no uncommitted changes.
2. Confirms that the standard `release.yml` is current.
3. Runs release checks, tests, the package build, and package verification.
4. Confirms that the current commit has been pushed to `origin`.
5. Creates and pushes tag `v0.1.0` from the `0.1.0` version in `plugin.toml`.
6. Waits for GitHub Actions to create the GitHub Release and confirms that `hello_world.neko-plugin`, `hello_world.market-release-check.txt`, and `market-evidence.json` have all finished uploading and can be downloaded.
7. Tells the Market to read that Release and publish it to the `stable` channel.

When the command succeeds, the terminal first reports that the GitHub Release is ready and then that the corresponding Market version has been published.

`publish` uses your GitHub credentials when it pushes the version tag. It does not push your plugin code. You do not enter a Market password or token for the Market notification. The Market accepts only plugins that have passed review, match the registered repository, and pass the standard release verification.

::: warning Pushing the tag alone does not finish publication
`release.yml` creates the GitHub Release, but it does not create a Market version by itself. Normally, let `neko-plugin publish` continue until it reports that publication to the Market succeeded.
:::

## 6. Confirm that users can install the plugin

After publication succeeds, check both places:

1. Open the GitHub repository's **Releases** page and confirm that `v0.1.0` contains `hello_world.neko-plugin`, the check report, and the release evidence.
2. Open the plugin details in the Market and confirm that `0.1.0` appears in the version list and that the latest stable version is no longer empty.

The Market stores the package URL and checksum from the GitHub Release. Developers do not upload the `.neko-plugin` file to the Market separately.

Only now is the first version fully published.

## 7. Publish later versions

After the first review has passed, normal feature updates do not require another review application. For each later version:

1. Edit the source in `plugin/plugins/hello_world/`.
2. Change the version in `plugin.toml` to a number that has never been published.
3. If dependencies changed, run `uv run --with pip neko-plugin sync hello_world --clean`.
4. Run `neko-plugin check hello_world`.
5. Commit and push in the plugin repository.
6. Run `neko-plugin publish hello_world`.

For example, after changing the version from `0.1.0` to `0.1.1`:

```bash
uv run neko-plugin check hello_world

cd plugin/plugins/hello_world
git add .
git commit -m "fix: improve the greeting"
git push
cd ../../..

uv run neko-plugin publish hello_world
```

The Market makes the newly published stable version the latest version. It chooses `latest` by the time a version was published to the Market, not by comparing version numbers. Do not publish an older version after publishing a newer one.

## 8. Publish a beta or add release notes

By default, `neko-plugin publish hello_world` publishes to `stable` without a Changelog.

To choose the beta channel or add release notes, create only the GitHub Release first:

```bash
uv run neko-plugin publish github hello_world
```

After the GitHub Release is ready:

1. Sign in to the Market and open the plugin's version page.
2. Choose the action to publish a new version.
3. Select the GitHub Release you just created.
4. Choose `stable` or `beta`.
5. Add a Changelog if needed and confirm the publication.

Version numbers are unique across the whole plugin, not separately within the stable and beta channels. A version already published as beta cannot be published again as stable with the same version number. Use a new version number and a new Release for the stable publication.

## 9. Continue after an interrupted publication

First, identify the step where the command stopped.

### The GitHub Release succeeded

Rerun the complete command:

```bash
uv run neko-plugin publish hello_world
```

If the remote tag still points to the current commit, the CLI continues waiting for the Release or notifying the Market instead of creating another version.

You can also copy the GitHub Release page URL and retry only the Market notification:

```bash
uv run neko-plugin publish market \
  https://github.com/your-name/n.e.k.o_plugin_hello_world/releases/tag/v0.1.0
```

This mode still publishes only stable versions that pass the standard verification.

### GitHub Actions failed

Open the plugin repository's **Actions** page and inspect the failed step.

- If the failure was only a temporary network problem and you did not change the code, rerun the failed workflow on GitHub and then run `publish` again.
- If you must change the code or release configuration, fix it, choose a new version number, commit, push, and publish a new tag. Do not point the same version number at different code.

### The standard release configuration is outdated

Preview the changes the CLI plans to make:

```bash
uv run neko-plugin setup-repo hello_world \
  --upgrade-github-actions \
  --dry-run
```

Apply the update after checking that there are no conflicts:

```bash
uv run neko-plugin setup-repo hello_world \
  --upgrade-github-actions
```

Then commit and push the changes to `.github/workflows/` and `ruff.toml` in the plugin repository before publishing again. The CLI stops instead of overwriting custom workflow content it does not recognize.

### The Market cannot find a publishable plugin

Confirm all three points:

- The first review has passed; merely submitting the application is not enough.
- The repository registered in the Market is the repository currently referenced by `origin`.
- The GitHub Release was generated successfully by the standard `release.yml` and passed verification.

`publish` does not replace the first submission and cannot publish an unapproved repository directly to the Market.

## 10. Withdraw an incorrect version

If a published version has a serious problem, enter a reason and withdraw it from the Market's version management page.

Withdrawal cannot be undone. You also cannot publish the same version number or GitHub Release again. Fix the source, create a new Release with a new version number, and publish that new version.

## One-page checklist

### First publication

```text
check
  → Git commit
  → push GitHub main
  → wait for Verify to pass
  → submit the plugin for its first Market review
  → submit a Revision when the reviewer requests changes
  → pass review
  → neko-plugin publish
  → GitHub Release
  → Market stable Version
```

### Later releases

```text
Edit the source
  → update the version in plugin.toml
  → check
  → Git commit / push
  → neko-plugin publish
```

## Requirements for an existing plugin

This guide assumes that the plugin directory itself contains `.git/` and the standard `verify.yml` and `release.yml`. A plugin created with the current `neko-plugin init` command from the quick start already meets these requirements.

If an existing plugin is tracked only by the outer N.E.K.O repository and has no `.git/` directory of its own, the current `publish` command stops. The CLI does not currently provide a command that converts such a directory into a publishable plugin repository. Do not use directory copies, package imports, or symbolic links to imitate the publication workflow.
