/**
 * workspace_placeholder.js — 渲染"本阶段尚未实装"占位视图.
 *
 * 所有五个 workspace 在 P03 只有骨架, 内容都是"标题 + 说明 + 后续 todo 列表",
 * 单独写 5 份 DOM 构建很啰嗦. 把模板抽到这里, 具体 workspace 文件只负责指明
 * 自己的 i18n 子树 (`workspace.<id>.*`) 与 tab id.
 */

import { i18n, i18nRaw } from '../core/i18n.js';

export function renderPlaceholderWorkspace(host, workspaceKey) {
  host.innerHTML = '';

  const title = document.createElement('h2');
  title.textContent = i18n(`workspace.${workspaceKey}.title`);
  host.append(title);

  const box = document.createElement('div');
  box.className = 'placeholder';

  const heading = document.createElement('h3');
  heading.textContent = i18n(`workspace.${workspaceKey}.placeholder_heading`);
  box.append(heading);

  const p = document.createElement('p');
  p.textContent = i18n(`workspace.${workspaceKey}.placeholder_body`);
  box.append(p);

  const todos = i18nRaw(`workspace.${workspaceKey}.todo_list`);
  if (Array.isArray(todos) && todos.length) {
    const hint = document.createElement('p');
    hint.className = 'muted';
    hint.textContent = '本 workspace 后续阶段要完成的事:';
    box.append(hint);

    const ul = document.createElement('ul');
    for (const item of todos) {
      const li = document.createElement('li');
      const tag = document.createElement('span');
      tag.className = 'todo-tag';
      tag.textContent = item.tag;
      li.append(tag, document.createTextNode(item.text));
      ul.append(li);
    }
    box.append(ul);
  }
  host.append(box);
}
