/**
 * page_memory_persona.js — Setup → Memory → Persona 子页 (P07).
 *
 * 对应 `memory_dir/{character}/persona.json`. 顶层是 dict, 每个 key 是 entity
 * (master/neko/relationship/自定义), 每个 value = {"facts": [...]}.
 *
 * 重要提示 (文案里也会体现):
 *   真实 `PersonaManager.ensure_persona` 首次加载时还会自动把 character card
 *   的"年龄/自我介绍/...."同步到 `source='character_card', protected=True`
 *   的 fact 里. 这里看到的是**磁盘上的原始 JSON**, 不触发那次合并 —  想完整
 *   模拟真实启动, 保存后启一次 Chat workspace 即可让 PersonaManager 跑一遍.
 */

import { renderMemoryEditor } from './memory_editor.js';

export function renderMemoryPersonaPage(host) {
  return renderMemoryEditor(host, 'persona');
}
