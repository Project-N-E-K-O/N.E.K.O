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
