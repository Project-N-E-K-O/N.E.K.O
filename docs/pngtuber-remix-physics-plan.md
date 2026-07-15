# Remix 官方链条/父子物理增强计划

## Summary

按“分阶段增强”实现 Remix Web runtime 的官方物理近似：优先补父子物理传导、chain IK、appendage tip-follow 和更稳的 mesh 联动。不追求 Godot 完全等价，本期不做 throwable/collision/hit physics 全量复刻。

## Key Changes

- 扩展 Remix importer 保留本期需要的官方物理字段：`follow_wa_tip`、`follow_wa_mini/max`、`anchor_id`、`sync_appendage`、`appendage_angle`、`wiggle_segm`、`segm_length`、`subdivision`、`wiggle_stiff`、`damping`、`comeback_speed`、`max_anchor_stretch`、`keep_length_anchor`、`mirror_anchor_movement_h/v`，以及对应状态前缀字段中实际存在的值。
- 在 metadata `runtime_features` 增加明确能力标记：`remix_parent_physics`、`remix_chain_ik`、`remix_appendage_physics`。继续保留 `physics_v2`，但不要把它解释成官方全量物理。
- 在 `static/pngtuber-core.js` 新增 Remix 物理状态缓存：
  - 每层记录上一帧 `x/y/rotation/stretch/calcLength`，用于模拟官方 `add_parent_physics()`。
  - 子层根据 `parent_chain` 累加父层 `calcLength`，并受 `phys_eff`、`ignore_bounce`、`dragSpeed`、`rdragStr`、`stretchAmount` 影响。
  - `drag_snap` 只在状态切换/大位移时重置该层物理状态，不再仅当作普通回零逻辑。
- 增加 chain IK 近似：
  - 使用 `chain_softness`、`chain_rot_min/max`、`bone_length` 计算目标角度和插值强度。
  - 对有 `anchor_id` 或可解析目标层的 layer，按官方 `apply_look_at_ik()` 思路朝目标 origin 旋转。
  - 没有 anchor 时保持当前单层 drag rotation 行为，避免旧模型退化。
- 增加 appendage 近似：
  - 对带 appendage 字段的 layer 初始化多点轻量链条，点数来自 `wiggle_segm`，段长来自 `segm_length`。
  - 每帧做简化 Verlet：root 固定在 layer origin，anchor 存在时末端追 anchor；应用长度约束、阻尼、回弹、最大拉伸。
  - `sync_appendage` 存在时复制目标 appendage 点位偏移；找不到目标则安静降级到本层链条。
  - `tip_point + follow_strength + follow_wa_mini/max` 用于让子 sprite 跟随 appendage 指定点。
- 改进 mesh 联动：
  - 保持现有三角 mesh 绘制。
  - mesh deformation 输入改用新的 physics `calcLength/rotation/appendageTip`，让 `tip_point` 周围保持相对稳定，远端随 `mesh_phys_x/y` 和 chain 旋转变形。
  - mesh 几何缺失时仍只保留 metadata，不伪造 mesh。

## Test Plan

- Importer 单测：
  - 验证 appendage/chain 字段从 `.pngRemix` 状态进入 layer state。
  - 验证 `runtime_features.remix_parent_physics/remix_chain_ik/remix_appendage_physics` 只在对应字段存在时为 true。
  - 验证普通 serialized state 不误报 appendage/chain 能力。
- Runtime 静态契约测试：
  - 断言 `parent_chain` 会参与 `calcLength` 累加。
  - 断言 `chain_softness`、`chain_rot_min/max`、`bone_length` 进入 IK 计算路径。
  - 断言 appendage state 包含多点缓存、Verlet/constraint 更新、anchor/sync 降级逻辑。
  - 断言 mesh deformation 使用新 physics 输出，而不是只用 `state.x/y`。
- 回归验证命令：
  - `.\.venv\Scripts\python.exe -m pytest tests/unit/test_pngtuber_router_delete.py tests/unit/test_pngtuber_static_contracts.py -q`
  - `node --check static\pngtuber-core.js`
  - `git diff --check upstream/main...HEAD`

## Assumptions

- 本期目标是“明显更接近官方视觉效果”，不是逐帧复刻 Godot。
- 不新增上传接口，不改导入文件格式；只扩展 metadata/layer state 中已解析出的官方字段。
- `can_be_hit`、`hit_physics`、throwable/collision、完整 Godot mesh physics 留到后续独立阶段。
- 找不到 anchor/sync 目标时必须静默降级，不能让模型停止渲染。
