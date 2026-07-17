# PNGTuber 轻量角色载体：当前实现记录

> **文档性质：current implementation record。** 本页只描述本仓库当前可验证的 PNGTuber 导入、存储和浏览器运行时。早期分阶段计划、PR 施工脚本和样例特判已删除；第三方应用未来版本的兼容性不能由本页保证。

## 当前边界

PNGTuber 是与 Live2D、VRM、MMD 并列的角色载体。后端负责接收模型包、识别格式、规范化配置和删除模型；浏览器端负责两图模式或分层 Canvas 模式的渲染、口型、状态和物理。切换角色载体时必须继续遵守现有显示互斥与清理逻辑。

当前 API 位于 `main_routers/pngtuber_router.py`，前缀为 `/api/model/pngtuber`：

- `POST /upload_model`：上传并转换模型；
- `GET /models`：列出已安装模型；
- `DELETE /model`：删除模型。

单文件上限为 50 MB，整个上传包上限为 250 MB。限制以源码常量为准。

## 支持的输入

导入分派位于 `main_routers/pngtuber_importers/`：

- `simple_package.py`：含 `model.json` 的本项目简单包；
- `pngtuber_plus.py`：PNGTuber Plus `.save` 工程；
- `pngtube_remix.py`：PNGTubeRemix `.pngRemix` 工程；
- `godot_variant.py`：读取第三方工程中使用的 Godot Variant 数据；
- `veadotube.py`：识别 veadotube 工程并返回当前不支持的明确错误，而不是伪装成功。

导入结果统一写成项目配置。简单模型使用图片状态；第三方分层工程使用 `adapter: "layered_canvas_v1"` 和 `layered_metadata`。不要让前端直接解释原始 `.save` 或 `.pngRemix` 文件。

## 浏览器运行时

`static/pngtuber-core.js` 是当前 PNGTuber 运行时入口，负责：

- 普通模型的 idle/talking 图片切换；
- 分层模型的图层排序、父子可见性和状态切换；
- 说话层、眨眼层、asset action 与口型 flap；
- 导入元数据允许时的 bounce 和分层物理；
- 模型隐藏、切换和销毁时清理计时器与 animation frame。

运行时只消费规范化字段。原工程热键可以保留为来源元数据，但不能直接注册为项目全局热键；项目自己的交互合同应保持统一。

## PNGTubeRemix 契约

当前导入器会保留状态、父子层级、sprite sheet、mesh 几何与 UV/三角形/binding 信息，以及可解析的物理字段。浏览器是否消费某个字段必须以 `static/pngtuber-core.js` 为准；“元数据已保留”不等于“运行时完整复刻第三方应用”。

稳定约束包括：

- 不能只导入默认状态可见的图层；任一状态需要的图层都必须保留；
- 绘制排序优先使用累积父子关系后的有效 z 顺序；
- 说话、眨眼、状态与 asset 覆盖必须可组合，不能让互斥图层同时出现；
- 原始热键只作来源信息，运行时键位由本项目统一控制；
- 未支持的第三方特性应保留为元数据或给出降级说明，不得声称等价复刻。

## 持久化与安全

上传内容必须先经过文件名、扩展名、包大小和目标路径校验。导入器只能在本次模型目录内展开和读取资源，不能信任工程文件内的绝对路径或路径穿越片段。删除接口必须只删除解析到模型根目录内的目标。

模型配置中保存相对资源路径和规范化元数据。不要把临时上传目录、开发机绝对路径或第三方工程的任意脚本写入运行配置。

## 验证

相关回归覆盖位于：

- `tests/unit/test_pngtuber_static_contracts.py`
- `tests/unit/test_pngtuber_router_delete.py`
- `tests/unit/test_pngtube_remix_importer.py`
- `tests/unit/test_pngtuber_plus_importer.py`

提交前至少运行：

```bash
node --check static/pngtuber-core.js
uv run python -m py_compile main_routers/pngtuber_router.py main_routers/pngtuber_importers/pngtube_remix.py main_routers/pngtuber_importers/pngtuber_plus.py
uv run pytest tests/unit/test_pngtuber_static_contracts.py tests/unit/test_pngtuber_router_delete.py tests/unit/test_pngtube_remix_importer.py tests/unit/test_pngtuber_plus_importer.py -q
```

## 剩余工作

- 继续用合法的第三方样本扩充兼容矩阵，同时避免把某个样本的字段当成格式通则；
- 对已经保存但尚未消费的 mesh/物理字段逐项增加运行时能力与回归测试；
- 任何 adapter schema 变更都要保留版本字段和旧模型降级路径；
- 第三方格式升级后需要重新取样验证，不能把本页当成外部格式的永久规范。
