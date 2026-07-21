# PNGTubeRemix 分层物理兼容记录

> **文档性质：current implementation record。** 旧的“父子物理增强计划”已经落地为可验证的导入和运行时子集。本页不承诺与 PNGTubeRemix 当前外部版本像素级等价。

## 已实现

`main_routers/pngtuber_importers/pngtube_remix.py` 解析 `.pngRemix` 工程并输出 `layered_canvas_v1` 元数据。当前保留或规范化：

- 状态与任一状态可见的图层；
- 父子层级、局部变换和有效绘制顺序；
- sprite sheet 信息；
- mesh 顶点、UV、三角形与 binding 元数据；
- 可识别的 bounce、stretch、drag 和分层物理字段；
- 来源热键与 action 信息。

`static/pngtuber-core.js` 消费其中的浏览器运行时子集，包括状态、口型、眨眼、asset action、父子变换、bounce 与已支持的分层物理。某字段出现在导入 metadata 中，不代表浏览器已经完整渲染它。

## 稳定契约

- 父级隐藏时子级不能继续绘制；
- 物理变换在规范化父子链上组合，不能重复应用世界坐标；
- 非法数值、缺失父级和父子环必须安全降级；
- 老 adapter metadata 没有新字段时仍可按旧图层模式显示；
- 运行时隐藏、模型切换和 dispose 必须清理计时器与 animation frame；
- 第三方工程中的脚本、绝对路径和原始热键不能直接执行或注册。

## 验证

```bash
uv run pytest tests/unit/test_pngtube_remix_importer.py tests/unit/test_pngtuber_static_contracts.py tests/unit/test_pngtuber_router_delete.py -q
node --check static/pngtuber-core.js
```

## 剩余兼容工作

- 用合法样本继续覆盖嵌套 mesh、异常父子链和不同 physics 参数组合；
- 对“已保存但未消费”的 mesh/binding 字段逐项实现，不做静默伪兼容；
- 外部格式变化后重新验证 Variant 解码和字段含义；
- 若 adapter schema 改变，增加版本迁移和旧模型回归。
