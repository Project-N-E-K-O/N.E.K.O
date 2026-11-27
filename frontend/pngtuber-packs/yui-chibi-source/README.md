# YUI Chibi PNGTuber

运行资源随仓库分发，位于同级 `yui-chibi-*.zip` 中，分包清单和校验信息由上级 `manifest.json` 管理。`metadata.actions.json` 和 `model.json` 为可读源码副本；不依赖开发者工作目录。

## 动作与触发

- 待机 2 个；开心、难过、生气、惊讶各 3 个；拖拽 2 个；真实串门出发、返回各 1 个；闲暇 5 个，共 23 个。
- 18 个普通动作支持 AI 语音驱动口型，保留逐帧位置、旋转和透视参数；闲暇 5 个使用原始动画嘴型。
- 每次加载或从其他动作返回时随机选择一个待机，之后循环同一段，不在两段之间自动轮换。
- 主动搭话关闭、当前页面为主页面且无忙碌状态时，空闲 30 秒随机触发闲暇动作；开启主动搭话后停止闲暇触发。
- 串门动作只响应当前访问中真实的 guest departed/ended 事件，不响应恢复、邀请或接待方事件。
- 缩放范围 0.15–0.75。新行为通过 `reviewed_actions.version === 1` 按模型启用，其他 PNGTuber 和 Live2D 保留原逻辑。

## 构建与校验

常规前端构建已调用 `scripts/unpack_builtin_pngtuber.py`，自动校验并解压全部分包。无需运行作者机器上的脚本、生成视频或复制外部素材。缺包、校验失败或资源引用缺失会中止安装；发布失败会恢复旧目录。

需要重新打包时，在仓库根运行：

```powershell
python scripts/pack_yui_chibi.py --source-dir PATH_TO_APPROVED_UNPACKED_MODEL
```

输入目录必须包含完整 `model.json`、`metadata.actions.json` 及其引用的 WebP／PNG 身体帧与 PNG 嘴型贴片。支持旧的 `/static/pngtuber/yui-chibi/` 路径及本包的相对路径。脚本保留 PNG／WebP 后缀并按字节复制身体帧与嘴型贴片，不重新压缩、不修改时序或嘴型几何；保留已有的 idle.png 和 assets/idle-first.png 兼容入口。缺少 PNG 入口且首帧为 WebP 时，仅将首帧无损解码保存为 PNG 入口；固定 ZIP 条目顺序、时间和权限。作者基准图及动作定义保存在源码目录，不重复塞进运行包。

分包目标为每包 64 MiB 解压大小、最多 600 个文件。解包器仍对每包执行原有 250 MiB、1000 文件和单文件 50 MiB 限制，另外限制总包数 32、总文件数 10000、总解压大小 2 GiB。此支持仅用于内置包，不改用户上传限制。

当前身体帧为从原始 PNG 直接编码的有损 WebP Q90（method=4、alpha_quality=100），2157 张动画帧保持原尺寸和逐像素 Alpha；1503 张口型覆盖贴片保留原始 PNG。原始 PNG 母版另行归档，不将有损候选当作原始母版。当前分包下载合计约 267.74 MiB；用户已选择 Q90，并在本机启动测试后反馈整体效果良好；真实双端串门和跨平台场景未逐项确认。

## 验证

```powershell
python -m pytest tests/unit/test_unpack_builtin_pngtuber.py tests/unit/test_check_no_nonascii_asset_names.py -q
node --test tests/frontend/pngtuber_tracked_animation.test.cjs tests/frontend/pngtuber_preview_type_switch_race.test.cjs
```

用户已于 2026-11-27 在本机启动测试并反馈整体效果良好。该反馈不等同于真实双端串门、所有交互组合及跨平台性能的逐项验收。音频口型根据振幅和共振峰近似选择元音，不是精确音素对齐。


待机常驻缓存有512 MiB预算；当前两段共148张720×960身体帧，仅RGBA理论数据约390 MiB，另有动作和浏览器缓存。Q90压缩主要减少下载和磁盘体积，不等比例降低解码内存。


资源打包的失败回滚与恢复由发布事务实现；解包回滚独立处理。
