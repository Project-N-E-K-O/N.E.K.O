/**
 * page_memory_facts.js — Setup → Memory → Facts 子页 (P07).
 *
 * 对应 `memory_dir/{character}/facts.json`. 列表元素 schema (参考 upstream
 * `memory/facts.py::FactStore`):
 *   {id, text, importance, entity, tags, hash, created_at, absorbed}.
 * P07 仅提供 raw-JSON 编辑器; 表格化 / 提取向导由 P10 记忆操作加上.
 */

import { renderMemoryEditor } from './memory_editor.js';

export function renderMemoryFactsPage(host) {
  return renderMemoryEditor(host, 'facts');
}
