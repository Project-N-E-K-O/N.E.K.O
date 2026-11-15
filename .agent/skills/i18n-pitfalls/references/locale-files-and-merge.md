# `static/locales`：同步门、缓存版本、合并冲突

## 1. 同步门的判法（`scripts/check_i18n_sync.py`）

CI 里只在 PR 上跑（`.github/workflows/analyze.yml` 的 Prompts & i18n 任务）。两组文件各自一条规则：`static/locales/*.json` 八个文件；`frontend/plugin-manager/src/i18n/locales/*.ts` 八个文件（`yuiGuide.ts` 不在组里）。

规则：组里任何一个文件有改动，就要求**组里所有文件都有改动，并且每个文件的 diff 块头（`@@ -旧起点,行数 +新起点,行数 @@`）完全一致**。

由此得到的几条实操结论：

- **一个 key 一行，数组内联成一行。** 某种语言把数组展开成多行，行号就和别的语言错开了。另外 `tests/unit/test_window_pin_static_contracts.py` 还要求八个 JSON 的总行数相等。
- **diff 是相对 merge-base 算的**（`git diff <base>...HEAD`，三个点）。自查对齐时要用同样的口径：

  ```bash
  git diff --unified=0 $(git merge-base origin/main HEAD) HEAD -- static/locales/es.json   # 带上 HEAD：只比已提交的内容，和同步门一致
  ```

  逐个文件比块头。⚠️ 不要用两点的 `git diff origin/main`：main 在你分叉之后有人改过某个 locale 的某一行时，两点 diff 看到的是 main 的最新值，你会以为那一行「本来就是这样」而把它改回去，结果相对 merge-base 多出一个只有这个语言才有的块。
- **门只看已提交的内容。** 工作区里没提交的改动它看不到，要先 commit 再跑。
- **批量改文案后要逐文件复核全部改动行。** 让多个执行者分语言并行改文案是合适的（各语言的地道程度可以各自把关），但有人会顺手改掉范围外「看着像笔误」的行，那一行就破坏了对齐。
- 本地跑：`uv run python scripts/check_i18n_sync.py --base origin/main`。

### 什么时候不修、请维护者手工批准

下面两种情况结构上就没法让八个文件对齐：

- 一次性的 locale 结构重排；
- 只有个别语言译错了要修，其它语言本来就是对的。

做法：这项检查保持红，PR 描述里写明「i18n-sync 预期红，请维护者手工批准」，其余检查照常要绿。**不要**改 `GROUPS`，**不要**为了对齐去动本来正确的翻译。

不要把这条当成通则：纯改文案、八种语言都动、每个 key 原行替换的 PR，门本来就是绿的，不需要任何人批准。

## 2. 缓存版本（`LOCALE_VERSION`）

机制和每条断言的理由写在 `tests/unit/test_locale_cache_bust_contract.py` 的模块 docstring 里。要点：

- 指纹只对 **key 结构**敏感（数组按下标展开），对译文内容不敏感。所以改 key、加 key、给数组加元素要换版本；只改文案不用。
- 换版本三步：`static/i18n-i18next.js` 里换一个新的 `YYYY-MM-DD-slug`；旧值追加进测试文件的 `RETIRED_LOCALE_VERSIONS`；把 `LOCALE_KEY_SIGNATURE` 更新成新指纹。
- 用过的版本号（包括只在某个分支上出现过的）不能再用，回退到旧值也不行。
- 除了这个测试，还有别的静态守卫会读 `LOCALE_VERSION`（查格式或日期前缀）。动这个常量前先找全：

  ```bash
  git grep -l LOCALE_VERSION -- tests static scripts
  ```

  以前有测试把具体的版本字符串钉死，现在都只要求非空或符合格式。新写测试不要钉具体值。

## 3. 分支和 main 都改过 locale 时的合并冲突

只要分支和 main **都**改过 `static/locales/*.json` 的 key，双方就各自换过 `LOCALE_VERSION`，合并时下面三处必然冲突：

- `static/i18n-i18next.js` 的 `LOCALE_VERSION`
- `tests/unit/test_locale_cache_bust_contract.py` 的 `RETIRED_LOCALE_VERSIONS`
- 同文件的 `LOCALE_KEY_SIGNATURE`

固定四步：

1. **`LOCALE_VERSION` 起一个全新的 `YYYY-MM-DD-slug`**，不能保留任何一边的值。合并后的 key 集是两边的并集，两边的旧值都不再描述现状。slug 用描述性的词（比如带上 `merge`），不加数字后缀。
2. **`RETIRED_LOCALE_VERSIONS` 取两边的并集，再把两边各自的现役值都加进去**，然后去重（同一个值经常两边都有）。漏掉任何一边的旧值，缓存着那个版本的客户端就识别不出来。
3. **`LOCALE_KEY_SIGNATURE` 重新算，不要手敲，也不要挑一边。** 算法就是测试文件里的 `_locale_key_signature`：按排序后的语言依次喂「语言码、NUL、排序后的 key 路径用换行连接、NUL」进 sha256，key 路径里数组展开成 `[i]`。最省事的办法是先跑一次测试，或者写几行脚本导入这个函数算出来。
4. ⚠️ **八个 JSON 文件 git 是按行自动合并的，没报冲突不代表合对了，必须做三方 key 集核对。** 对每种语言验证：

   ```text
   合并结果的 key 集 == (base ∪ (分支 − base) ∪ (main − base)) − (base − 分支) − (base − main)
   ```

   其中 base 是**这一次合并的 merge-base**。公式有一种情况不能照搬：一边删了某个 key、另一边改了它的译文，公式会按删除算，但这其实是冲突。先人工定这个 key 留不留，再按定下来的结果核对 key 集。key 集相等能证明没有丢键、多键，证明不了译文没有串到别的 key 上（两个 key 的译文互换，key 集不变）。所以还要逐个 key 比值：两边都没改过的 key，值等于 base；只有一边改过的，值等于那一边；两边都改过的逐条人工看。

合并后跑：

```bash
uv run pytest tests/unit/test_locale_cache_bust_contract.py tests/unit/test_i18n_locale_keys.py tests/unit/test_theater_selector_locale.py
uv run pytest tests/unit/test_drawing_guess_page_contract.py tests/unit/test_voice_identity_ui_static_contract.py tests/unit/test_window_pin_static_contracts.py
uv run python scripts/check_i18n_sync.py --base origin/main
```

第二行是另外几个会读 `LOCALE_VERSION` 或 locale 行数的静态守卫。node 这边还有 `static/yui-guide-day1-systray-intro.test.cjs` 和 `tests/frontend/voice_recovery.test.cjs`。

### 两个坑

- ⚠️ **在 Windows 上用 Python 的 `read_text()` 加 `write_text()` 改文件，会把整个文件的行尾变成 CRLF。** `write_text` 默认会把 `\n` 翻译成系统换行符。仓库的 `.gitattributes` 规定这些文件是 LF。写的时候传 `newline="\n"`，或者用 `read_bytes()` / `write_bytes()`。已经写脏的文件，提交之后删掉再从 git 检出一次才能把工作区归一，否则工作区和索引会一直不一致。这个坑不限于 locale 文件，任何用脚本批量改仓库文件的场合都一样。
- ⚠️ 判断「这次合并里两边真正都改了哪些文件」要用**这一次的 merge-base**，不是分支最初的分叉点。分支以前合过 main 的话，用最初的分叉点会把「上次合并带进来的 main 的改动」也算成分支自己的改动，看起来两边交集很大，其实是假象。

## 4. 其它和 locale 文件有关的约定

- 版本问卷 / 公告的多语言文件不在 `static/locales`，规则见 `config/surveys/README.md`（回退链最后落到简体，只发部分语言必须写 `locales` 白名单）。
- 个别体量大的小游戏有自己的 i18n 模块，只读总语言包里自己的子树，仍然受 `LOCALE_VERSION` 管。
