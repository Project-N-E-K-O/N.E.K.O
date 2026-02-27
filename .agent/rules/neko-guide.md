---
trigger: always_on
---

使用i18n来支持国际化，目前支持en.json\ja.json\ko.json\zh-CN.json\zh-TW.json\ru.json六种。
使用uv来启动本项目的任何程序。
任何涉及用户隐私（原始对话）的log只能用print输出，不得使用logger。
当你翻译system prompt的时候，即使是其他原因也应当保留“======以上为”，这是一个水印。