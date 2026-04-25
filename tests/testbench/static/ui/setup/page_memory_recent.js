/**
 * page_memory_recent.js — Setup → Memory → Recent 子页 (P07).
 *
 * 对应 `memory_dir/{character}/recent.json`. P07 仅做"查看 + 直接编辑 + 保存";
 * "从当前对话压缩" 这类触发按钮由 P10 记忆操作专题承担.
 */

import { renderMemoryEditor } from './memory_editor.js';

export function renderMemoryRecentPage(host) {
  return renderMemoryEditor(host, 'recent');
}
