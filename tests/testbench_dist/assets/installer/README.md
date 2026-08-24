# 安装向导品牌图

将正式 logo 背景图**同名覆盖**本目录下 PNG 即可，无需改 Inno Setup / create-dmg 脚本。

## 尺寸

| 文件 | 分辨率 | 用途 |
|------|--------|------|
| `win/wizard-sidebar.png` | **164 × 314** | Inno Setup 左侧主背景（最重要） |
| `win/wizard-sidebar@2x.png` | 328 × 628 | HiDPI 可选 |
| `win/wizard-small.png` | **55 × 55** | 右上角小图标 |
| `win/welcome-banner.png` | 497 × 312 | 欢迎页横幅（可选） |
| `mac/dmg-background.png` | **1320 × 800** | DMG @2x 背景 |
| `mac/dmg-background@1x.png` | 660 × 400 | 非 Retina 兜底 |

格式：PNG，sRGB。当前仓库内为占位图（灰底 + 文字），可直接构建安装包。
