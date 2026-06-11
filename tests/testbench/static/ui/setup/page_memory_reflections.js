/**
 * page_memory_reflections.js — Setup → Memory → Reflections 子页 (P07).
 *
 * 对应 `memory_dir/{character}/reflections.json`. 状态字段 `status` ∈
 *   pending / confirmed / denied / promoted. 手动编辑时注意 id 不要重复
 *   (upstream `ReflectionEngine` 以 id 为主键去重, 撞 id 会静默覆盖).
 * P07 先实装 raw-JSON 视图; 两列 pending/confirmed 预览 + "Reflect now" 留到 P10.
 */

import { renderMemoryEditor } from './memory_editor.js';

export function renderMemoryReflectionsPage(host) {
  return renderMemoryEditor(host, 'reflections');
}
