# 版本问卷定义（config/surveys/）

与 `config/changelog/` 同构：一个文件对应一个版本，主程序后端 `GET /api/survey`
按当前 `APP_VERSION` 下发。**当前版本若存在 `<APP_VERSION>.json`**，前端会在
changelog 确认弹窗走完后，向**老玩家**（本地存过 `neko_last_notified_version` 的用户，
全新用户跳过）弹出一次；用户填完点提交或点跳过后，本地记 `neko_last_survey_version`
不再重复弹。答卷经主后端 `POST /api/survey/submit` 上报到远程 survey_server。

## 文件位置与本地化

- 中文基准：`config/surveys/<version>.json`
- 其它语言：`config/surveys/<locale>/<version>.json`（如 `en/0.8.2.json`、`ja/0.8.2.json`）

回退链与 changelog 一致：用户语言 → `en` → 中文原文。某语言缺文件时整份回退，
**问题 id 必须逐语言保持一致**（答案按 id 上报，id 漂移会把同一题拆成两题）。

### ⚠️ 只发给部分语言时必须写 `locales`

回退链**最终一定落到简体 base 文件**。所以只放 `<version>.json`（简体）而不做限制，
英语 / 日语 / 繁体用户同样会收到——他们看到的是简体原文。要把一份问卷或公告限定给
某些语言，在文件里加 `locales` 白名单：

```jsonc
"locales": ["zh-CN"]   // 只有请求 lang 恰好等于列表中某项才下发
```

判定是**精确匹配**，且空 locale（i18n 尚未就绪等）一律不下发——定向内容宁可漏发给
自己人，也不能发给不该收的人。不写该字段时行为不变（所有语言按回退链下发）。

各语言的 locale 码见 `static/i18n-i18next.js` 的 `SUPPORTED_LANGUAGES`：
`zh-CN`（简体）、`zh-TW`、`en`、`ja`、`ko`、`ru`、`es`、`pt`。

### ⚠️ 带截止日期的内容必须写 `expires_at`

安装包会被用户在任意时间首次启动，所以"8月20日前投票"这类内容如果不设时效，
半年后新装的用户照样会收到一个早已结束的活动通知。加：

```jsonc
"expires_at": "2026-08-20"   // ISO 日期，含当天；之后一律不下发
```

按 UTC 日期比较。格式写错时**同样不下发**（并打 warning 日志）——与 `locales`
一样，宁可漏发也不发错。不写该字段表示永不过期。

### 只发给老玩家

这是问卷渠道的既有行为，无需额外配置：前端只在 `neko_last_notified_version` 已存在
且不等于当前版本时才写 `neko_survey_eligible_for`（见 `static/app/app.js`），所以
**全新安装的用户永远不会看到问卷**，只有从旧版升级上来的老玩家会。

### 纯公告（没有题目）

`questions` 可以是空数组：弹窗只渲染 `title` + `intro` 和「跳过 / 提交」两个按钮，
用户点任一按钮即记 `neko_last_survey_version`、不再重复弹。`intro` 走 `textContent`
渲染（不解析 HTML/Markdown，链接不可点击），`\n` 会保留为换行。

## Schema

```jsonc
{
  "survey_version": "0.8.2",          // 与文件名一致，原样上报
  "title": "问卷标题",                 // 弹窗标题
  "intro": "一句话引导语（可选）",
  "questions": [
    {
      "id": "usage_freq",             // 稳定 id，跨语言/跨改版不要换
      "type": "single",              // single | multi | text
      "label": "题干",
      "required": false,             // 仅对 submit 生效；跳过不校验
      "options": [                    // single / multi 必填
        { "value": "daily", "label": "每天" },
        { "value": "weekly", "label": "每周几次" }
      ]
    },
    {
      "id": "suggestion",
      "type": "text",
      "label": "还有什么想对我们说的？",
      "placeholder": "选填",                       // 未联动 / 来源题未选时的提示
      "placeholder_from": "keep_one",              // 可选：placeholder 跟随此单选题的选择
      "placeholder_template": "「{label}」往哪个方向打磨？",  // 联动模板，{label} 替换为所选项文案
      "max_length": 500               // text 可选，默认 500
    }
  ]
}
```

`value` 是低基数稳定枚举（上报与统计用），`label` 是展示文案（可随本地化变）。

`placeholder_from` + `placeholder_template`（仅 `text` 题）：填空提示随来源单选题的选择实时
变化，引导用户对刚选的项写具体想法；来源题未选时回退到 `placeholder`。模板里的 `{label}` 会
替换成所选项的本地化 `label`，因此各语言文件都要保留 `{label}` 占位符。**两者需成对提供才启用
联动**——任一缺失（或来源题未选）都退回静态 `placeholder`。
