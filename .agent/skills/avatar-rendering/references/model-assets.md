# 模型资源：内置模型、动作文件、同名判定、离线预录

## 内置 Live2D 模型放在哪

- 内置模型以 `assets/<名字>.tar.gz` 入库，构建时解到 `static/<名字>/`；解出来的目录在 `.gitignore` 里，不在 git 中。目前是 `yui-lolita`（默认）和 `yui-origin`。`static/mao_pro/` 是直接入库的示例模型，测试夹具会用它。
- 名字登记在两处，要一起改：后端 `config/character_defaults.py` 的 `BUILTIN_LIVE2D_MODEL_NAMES`（默认模型是同文件的 `DEFAULT_LIVE2D_MODEL_NAME`），前端 `static/js/character_card_manager/core-and-upload.js` 里同名的常量。
- 加一个内置模型还要同步构建和打包这一串：`build_frontend.sh`、`build_frontend.bat`、`scripts/verify_frontend_build.sh`、`.github/workflows/build-desktop.yml`、`scripts/check_nuitka_dist.py` 的 `_REQUIRED_ASSETS`、`.gitignore`。打包规则见 `docs/contributing/nuitka-packaging.md`。

## ⚠️ 动作文件的处理：不删曲线

新模型上线前说的「瘦身」或「trim」，指的只有下面两个脚本，没有别的：

```bash
uv run python scripts/smooth_yui_mouth.py --root static/<模型目录>
uv run python scripts/slim_live2d_motions.py --root static/<模型目录>
```

- `smooth_yui_mouth.py`：只动 `ParamMouthForm` 和 `ParamMouthOpenY` 两条曲线，做低通平滑，并把每个动作的首尾压回 0。原因是这些动作只当背景动画用、不承担口型，画师给嘴部打的关键帧太抖，会闪。所以它只适用于动作不承担口型的模型（内置的两个 yui 模型是这样）；新模型的嘴部曲线如果是有意做的表演，不要跑它，先问维护者。它会重算 `Meta` 里的曲线数、段数、点数。不带 `--root` 时默认处理 `static/yui-origin`。
- `slim_live2d_motions.py`：只改序列化——去掉缩进和空格，非整数四舍五入到 6 位小数。每条曲线、每个关键帧都保留，`Meta` 计数不变，再跑一遍是空操作。位数不能再少：有些贝塞尔段只有几毫秒长、控制点远超参数范围，时间偏一点求值结果就偏掉约百分之一的量程（脚本文档字符串里有说明）。内置模型的动作文件靠这一步从 34MB 降到 8MB（#3576）。

**规则：不删除任何动作曲线，包括被物理完全覆盖的输出参数。**

理由：动作里缺了某个参数的曲线，上一个动作或上一个状态留在这个参数上的值就没人清掉，会残留。关掉物理的模式下尤其明显——物理不再覆盖这些参数，残留值直接显示出来。「这条曲线反正会被物理覆盖，删了没影响」这个推理是错的；按这个思路删过一次，被否决并恢复了原文件。

想进一步压体积，可选项只有降低动作的采样率，而且要先问维护者，不要自己发明别的办法。

处理前把模型目录备份到仓库之外。处理后核对：除嘴部两条曲线外其余曲线逐值不变（允许 6 位小数的舍入）、曲线条数不变、`Meta` 计数和实际一致。守卫：`tests/unit/test_slim_live2d_motions.py`。

## 内置模型和同名的用户模型

按模型名查找时，两个函数的优先顺序是反的（`utils/frontend_utils.py`）：

- `find_models()` 先扫 `static`，再扫用户目录和创意工坊。后扫到的同名模型会被改名成 `<名字>_<来源>`（例如 `_documents`）。所以列表里用原名的那一个永远是内置的。
- `find_model_directory(name)` 反过来，用户目录优先，然后创意工坊，最后才是 `static`。

目前 main 上「是不是内置模型」是按名字判断的：角色卡导出（`main_routers/characters_router/cards.py`）见到名字在 `BUILTIN_LIVE2D_MODEL_NAMES` 里就不打包模型；前端 `isStaticDefaultLive2DModel` 除了名字还会看模型来源是不是 `static`。

⚠️ 这带来一个已知缺口：**每新增一个内置模型名，此前用户自己导入的同名模型在导出 / 上传时就会被当成内置模型而不打包。** 角色保存的 `_reserved.avatar.asset_source`（`builtin` / `local_imported` / `steam_workshop` / `manual_external`，由 `main_routers/characters_router/live2d_models.py` 按模型路径前缀推出）才能真正区分这两种情况。碰导出、上传、模型解析时按它判，不要再加按名字判断的分支。

## 内置模型的表情名

内置的两个 yui 模型，表情不是按情绪命名的，文件名是拼音缩写。文件名和表情名可以在 `model3.json` 里核对；「含义」一列是实测观察（预录演示时对着画面核对的）。配情绪映射或写演示脚本时用：

| 文件（`expressions/*.exp3.json`） | 含义 | `yui-lolita` 里的表情名 | `yui-origin` 里的表情名 |
|---|---|---|---|
| `bzy` | 半睁眼 | `expression2` | `bzy` |
| `yyy` | 眩晕眼 | `expression3` | `yyy` |
| `xxy` | 星星眼 | `expression4` | `xxy` |
| `wuyu` / `wy` | 无语 | `expression5` | `wy` |
| `k1`–`k3` / `z1`–`z3` | 三种嘴型（origin 的依次是露齿、慌张、嘟嘴） | `expression6`–`8` | `z1`–`z3` |
| `bx` / `sbx` | 比心 | `expression9` | `sbx` |
| `swk` / `swz` | 捂嘴 | `expression10` | `swz` |
| `s1` / `syhs` | 右手挥手 | `expression11` | `syhs` |
| `s2` / `szhs` | 左手挥手 | `expression12` | `szhs` |
| `by` | 闭眼（＞＜） | `by` | `by` |

两个模型的动作组都按情绪分：`happy`、`sad`、`angry`、`neutral`、`surprised`，加上待机的 `Idle`。

## 搬动或删除已发布的内置素材

`static/` 下发布过的素材路径可能已经被用户存进了配置（比如旧版文档和示例模型里写过绝对路径）。删掉或搬走之后不要留副本，在 `utils/static_asset_aliases.py` 的 `LEGACY_STATIC_ASSET_ALIASES` 里登记「旧路径 → 新路径」。主服务和 monitor 的 `/static` 挂载都混入了 `LegacyStaticAssetAliasMixin`，旧地址会直接返回新文件。只登记真的被移除的文件。

## 离线预录 Live2D（逐帧渲染成带透明通道的视频）

给演示或宣传材料预录模型动画时用过的做法，属于实测观察：

- 渲染页只需要 PIXI 加 `static/libs/` 里那几个文件，加载顺序同主页（Cubism 2 运行时必须在 `index.min.js` 之前）。
- 模型用 `autoUpdate: false` 关掉自动更新，自己按固定步长（比如 1/30 秒）推进，每步渲染后把画布读出来。画布要开 `preserveDrawingBuffer` 才能 `toDataURL`。
- 口型：先从音频按帧算出 RMS 包络，在 `beforeModelUpdate` 事件里写 `ParamMouthOpenY`。
- `coreModel` 没有「按索引取参数默认值」的接口，要复位参数就在模型加载完时自己存一份快照。
- 输出格式的兼容性：
  - 带透明通道的 WebM（VP9 alpha）只有 Chromium 内核能放。
  - iOS 上所有浏览器都是 WebKit，不支持透明 WebM。可行的替代是把「上半画面是颜色、下半画面是 alpha 遮罩」堆叠编码成一个 H.264 的 mp4，页面里用 WebGL 合成（预乘 alpha，混合用 `ONE, ONE_MINUS_SRC_ALPHA`）。
  - 内嵌到网页里的普通视频一律用 H.264、`yuv420p`。HEVC 在不少机器的 Chromium 里是黑屏，不要因为自己机器上能放就用。
- 单文件的演示页（素材全部内嵌）在浏览器里直接双击能开；内嵌预览类的浏览器通常打不开 `file://`，验证时起一个本地静态服务器。
