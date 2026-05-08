/**
 * memory_editor_structured.js ? Memory ????? (??????? structured tab).
 *
 * ?????:
 *   Raw JSON ??????????????? (??: ? `{}` ? persona ???
 *   ????; ? `[]` ? facts ?????????). ?????? memory kind
 *   ??? schema ??????, ? "+" ????????, ?????????,
 *   ??????? "??" ?.
 *
 * ?? kind ? schema (???? memory/*.py ??):
 *   - **persona** (dict):  `{<entity>: {facts: [persona_fact, ...]}}`.
 *       persona_fact = {id, text, source, source_id, recent_mentions,
 *                       suppress, suppressed_at, protected}
 *   - **facts** (list):    `[{id, text, importance, entity, tags,
 *                             hash, created_at, absorbed}, ...]`.
 *   - **reflections** (list): `[{id, text, entity, status, source_fact_ids,
 *                                created_at, feedback, next_eligible_at}, ...]`.
 *   - **recent** (list):   `[{type: human|ai|system, data: {content}}, ...]`.
 *       LangChain dump ??, ?? Chat ???????; ????????
 *       type + content ??, ?? (additional_kwargs / id / name / ????
 *       content) ?? [?? ?] ???? raw JSON textarea. content ???
 *       list-of-parts (???), ????? `{type:'text'}` ??????
 *       (???????????), ?????? text ????? "? Raw ??".
 *
 * ????:
 *   - **??????, ??????**: fact ? id/hash/created_at ?????,
 *     ???????, ???? [?? ?] ?. ??? legacy id ??????
 *     ?????? Raw JSON ??.
 *   - **id ??????? hash/embedding**: ???? sha256 ?? (?? Web
 *     Crypto ? digest ?????, ? "????????????" ??
 *     hash ??????? memory ???????????). ?? hash ??,
 *     ???? raw ??.
 *   - **timestamp ? naive ISO**: ????? `datetime.now().isoformat()`
 *     (? tzinfo), ????? `YYYY-MM-DDTHH:MM:SS` ??, ?????
 *     ?? dedupe/compare ????????.
 *   - **entity datalist ??**: ?? `master/neko/relationship/world` ??
 *     ?? entity ?, ????? (???????????? entity ?
 *     ??????), ??? datalist ?? select.
 *   - **input onChange ?? state.model + ?? onModelChanged ???**:
 *     ?????????; ?? "??? / ??? / ????" ??????
 *     ????? restructure() ?? DOM.
 *
 * ??: `renderStructuredView(host, state, {onModelChanged})`.
 */
import { i18n } from '../../core/i18n.js';
import { el } from '../_dom.js';

// ?? public entry ?????????????????????????????????????????????????????

export function renderStructuredView(host, state, { onModelChanged }) {
  // ??: ???????? null ????? (?? Raw ???????),
  // ??? coerce ?????, ?? structured ?????????????
  // (????????????? _validate_shape ??, ??? revert/reload
  // ??????).
  if (state.kind === 'persona') {
    if (!isPlainObject(state.model)) state.model = {};
  } else if (!Array.isArray(state.model)) {
    state.model = [];
  }

  const notify = () => onModelChanged && onModelChanged();

  if (state.kind === 'persona')          renderPersona(host, state, notify);
  else if (state.kind === 'facts')       renderFacts(host, state, notify);
  else if (state.kind === 'reflections') renderReflections(host, state, notify);
  else if (state.kind === 'recent')      renderRecent(host, state, notify);
}

// ?? persona (dict[entity -> {facts: list}]) ??????????????????????????
//
// ??????:
//   - `notify()` (???): ??/????? model + ???? dirty, ?
//     **??? DOM** (?? textarea ???????).
//   - `restructure()` (????): ???/???/???, ????? DOM.
//   ?? "+" / "?" ??? restructure; ?? input ? onChange ?? notify.

function renderPersona(host, state, notify) {
  const container = el('div', { className: 'memory-struct-root' });
  host.append(container);

  const restructure = () => { redraw(); notify(); };

  function redraw() {
    container.innerHTML = '';
    const entities = Object.keys(state.model);
    if (entities.length === 0) {
      container.append(el('div', { className: 'empty-state' },
        i18n('setup.memory.editor.empty_persona_hint')));
    }
    for (const entity of entities) {
      container.append(buildPersonaEntity(state, entity, notify, restructure));
    }
    container.append(addButton(i18n('setup.memory.editor.add_entity'), () => {
      const name = promptEntityName(state.model);
      if (!name) return;
      state.model[name] = { facts: [] };
      restructure();
    }));
  }
  redraw();
}

function promptEntityName(model) {
  const raw = window.prompt(i18n('setup.memory.editor.prompt_entity_name'), '');
  if (!raw) return null;
  const name = raw.trim();
  if (!name) return null;
  if (Object.prototype.hasOwnProperty.call(model, name)) {
    window.alert(i18n('setup.memory.editor.entity_exists', name));
    return null;
  }
  return name;
}

function buildPersonaEntity(state, entity, notify, restructure) {
  const block = el('div', { className: 'memory-entity-group' });
  const section = state.model[entity];
  if (!section || typeof section !== 'object') state.model[entity] = { facts: [] };
  if (!Array.isArray(state.model[entity].facts)) state.model[entity].facts = [];
  const facts = state.model[entity].facts;

  // header: entity ? + ?????? (?) / ???????? (?; ? spacer ??).
  const header = el('div', { className: 'memory-entity-header' });
  const entityLabel = el('code', { className: 'memory-entity-name' }, entity);
  const count = el('span', { className: 'badge secondary' },
    i18n('setup.memory.editor.count_items', facts.length));
  const spacer = el('span', { className: 'spacer' });
  const delEntity = el('button', { className: 'tiny ghost memory-item-delete' },
    i18n('setup.memory.editor.delete_entity'));
  delEntity.addEventListener('click', () => {
    if (!window.confirm(i18n('setup.memory.editor.delete_entity_confirm', entity))) return;
    delete state.model[entity];
    restructure();
  });
  header.append(entityLabel, count, spacer, delEntity);
  block.append(header);

  if (facts.length === 0) {
    block.append(el('div', { className: 'empty-state tiny' },
      i18n('setup.memory.editor.empty_facts_hint')));
  }
  facts.forEach((fact, idx) => {
    block.append(buildPersonaFactCard(facts, idx, notify, restructure));
  });

  block.append(addButton(i18n('setup.memory.editor.add_persona_fact'), () => {
    facts.push(defaultPersonaFact());
    restructure();
  }));

  return block;
}

function buildPersonaFactCard(facts, idx, notify, restructure) {
  const fact = facts[idx];
  const card = el('div', { className: 'memory-item-card' });

  card.append(simpleField(
    i18n('setup.memory.editor.field.text'),
    textareaInput(fact.text ?? '', v => { fact.text = v; notify(); }),
  ));

  card.append(rowFields(
    simpleField(
      // character_card ???? (14 ??), narrow ? 140px ???????;
      // ?? source ???? narrow, ????? flex:1 1 160px.
      i18n('setup.memory.editor.field.source'),
      selectInput(fact.source ?? 'manual', [
        ['manual', 'manual'],
        ['character_card', 'character_card'],
        ['settings', 'settings'],
        ['reflection', 'reflection'],
        ['unknown', 'unknown'],
      ], v => { fact.source = v; notify(); }),
    ),
  ));
  card.append(el('div', { className: 'memory-field-row memory-checkbox-row' },
    inlineField(
      i18n('setup.memory.editor.field.protected'),
      checkboxInput(!!fact.protected, v => { fact.protected = v; notify(); }),
    ),
    inlineField(
      i18n('setup.memory.editor.field.suppress'),
      checkboxInput(!!fact.suppress, v => { fact.suppress = v; notify(); }),
    ),
  ));

  card.append(advancedBlock([
    simpleField(
      i18n('setup.memory.editor.field.id'),
      textInput(fact.id ?? '', v => { fact.id = v; notify(); }),
    ),
    simpleField(
      i18n('setup.memory.editor.field.source_id'),
      textInput(fact.source_id ?? '', v => { fact.source_id = v || null; notify(); }),
    ),
    simpleField(
      i18n('setup.memory.editor.field.suppressed_at'),
      textInput(fact.suppressed_at ?? '', v => { fact.suppressed_at = v || null; notify(); }),
    ),
    simpleField(
      i18n('setup.memory.editor.field.recent_mentions'),
      tagsInput(fact.recent_mentions ?? [], v => { fact.recent_mentions = v; notify(); }),
    ),
  ]));

  card.append(deleteCornerButton(() => { facts.splice(idx, 1); restructure(); }));
  return card;
}

// ?? facts (list[fact_entry]) ?????????????????????????????????????????

function renderFacts(host, state, notify) {
  const container = el('div', { className: 'memory-struct-root' });
  host.append(container);
  const restructure = () => { redraw(); notify(); };

  function redraw() {
    container.innerHTML = '';
    const list = state.model;
    if (list.length === 0) {
      container.append(el('div', { className: 'empty-state' },
        i18n('setup.memory.editor.empty_list_hint')));
    }
    list.forEach((_, idx) => container.append(buildFactCard(list, idx, notify, restructure)));
    container.append(addButton(i18n('setup.memory.editor.add_fact'), () => {
      list.push(defaultFactEntry());
      restructure();
    }));
  }
  redraw();
}

function buildFactCard(list, idx, notify, restructure) {
  const fact = list[idx];
  const card = el('div', { className: 'memory-item-card' });

  card.append(simpleField(
    i18n('setup.memory.editor.field.text'),
    textareaInput(fact.text ?? '', v => { fact.text = v; notify(); }),
  ));

  card.append(rowFields(
    simpleField(
      i18n('setup.memory.editor.field.entity'),
      entityInput(fact.entity ?? 'master', v => { fact.entity = v; notify(); }),
    ),
    simpleField(
      i18n('setup.memory.editor.field.importance'),
      // ????? 1~10, ?? 5. ?? 0 / ?? 10 ??? (?? spinner ????).
      numberInput(fact.importance ?? 5, v => { fact.importance = v; notify(); }, { min: 0, max: 10 }),
      { narrow: true },
    ),
    simpleField(
      i18n('setup.memory.editor.field.tags'),
      tagsInput(fact.tags ?? [], v => { fact.tags = v; notify(); }),
    ),
  ));

  card.append(advancedBlock([
    simpleField(
      i18n('setup.memory.editor.field.id'),
      textInput(fact.id ?? '', v => { fact.id = v; notify(); }),
    ),
    simpleField(
      i18n('setup.memory.editor.field.hash'),
      textInput(fact.hash ?? '', v => { fact.hash = v; notify(); }),
    ),
    simpleField(
      i18n('setup.memory.editor.field.created_at'),
      textInput(fact.created_at ?? '', v => { fact.created_at = v; notify(); }),
    ),
    inlineField(
      i18n('setup.memory.editor.field.absorbed'),
      checkboxInput(!!fact.absorbed, v => { fact.absorbed = v; notify(); }),
    ),
  ]));

  card.append(deleteCornerButton(() => { list.splice(idx, 1); restructure(); }));
  return card;
}

// ?? reflections (list[reflection]) ???????????????????????????????????

function renderReflections(host, state, notify) {
  const container = el('div', { className: 'memory-struct-root' });
  host.append(container);
  const restructure = () => { redraw(); notify(); };

  function redraw() {
    container.innerHTML = '';
    const list = state.model;
    if (list.length === 0) {
      container.append(el('div', { className: 'empty-state' },
        i18n('setup.memory.editor.empty_list_hint')));
    }
    list.forEach((_, idx) => container.append(buildReflectionCard(list, idx, notify, restructure)));
    container.append(addButton(i18n('setup.memory.editor.add_reflection'), () => {
      list.push(defaultReflectionEntry());
      restructure();
    }));
  }
  redraw();
}

function buildReflectionCard(list, idx, notify, restructure) {
  const ref = list[idx];
  const card = el('div', { className: 'memory-item-card' });

  card.append(simpleField(
    i18n('setup.memory.editor.field.text'),
    textareaInput(ref.text ?? '', v => { ref.text = v; notify(); }),
  ));

  card.append(rowFields(
    simpleField(
      i18n('setup.memory.editor.field.entity'),
      entityInput(ref.entity ?? 'master', v => { ref.entity = v; notify(); }),
    ),
    simpleField(
      i18n('setup.memory.editor.field.status'),
      selectInput(ref.status ?? 'pending', [
        ['pending', 'pending'],
        ['confirmed', 'confirmed'],
        ['denied', 'denied'],
        ['promoted', 'promoted'],
        ['archived', 'archived'],
      ], v => { ref.status = v; notify(); }),
      { narrow: true },
    ),
  ));

  card.append(simpleField(
    i18n('setup.memory.editor.field.feedback'),
    textareaInput(ref.feedback ?? '', v => { ref.feedback = v || null; notify(); }),
  ));

  card.append(advancedBlock([
    simpleField(
      i18n('setup.memory.editor.field.id'),
      textInput(ref.id ?? '', v => { ref.id = v; notify(); }),
    ),
    simpleField(
      i18n('setup.memory.editor.field.created_at'),
      textInput(ref.created_at ?? '', v => { ref.created_at = v; notify(); }),
    ),
    simpleField(
      i18n('setup.memory.editor.field.next_eligible_at'),
      textInput(ref.next_eligible_at ?? '', v => { ref.next_eligible_at = v; notify(); }),
    ),
    simpleField(
      i18n('setup.memory.editor.field.source_fact_ids'),
      tagsInput(ref.source_fact_ids ?? [], v => { ref.source_fact_ids = v; notify(); }),
    ),
  ]));

  card.append(deleteCornerButton(() => { list.splice(idx, 1); restructure(); }));
  return card;
}

// ?? recent (list[LangChain dump]) ????????????????????????????????????

function renderRecent(host, state, notify) {
  // ? inline banner (????? + ??) ???, ? empty-state ?????.
  host.append(el('div', { className: 'memory-inline-warn' },
    i18n('setup.memory.editor.recent_warn')));

  const container = el('div', { className: 'memory-struct-root' });
  host.append(container);
  const restructure = () => { redraw(); notify(); };

  function redraw() {
    container.innerHTML = '';
    const list = state.model;
    if (list.length === 0) {
      container.append(el('div', { className: 'empty-state' },
        i18n('setup.memory.editor.empty_list_hint')));
    }
    list.forEach((_, idx) => container.append(buildRecentCard(list, idx, notify, restructure)));
    container.append(addButton(i18n('setup.memory.editor.add_message'), () => {
      list.push(defaultRecentMessage());
      restructure();
    }));
  }
  redraw();
}

function buildRecentCard(list, idx, notify, restructure) {
  const msg = list[idx];
  if (!msg.data || typeof msg.data !== 'object') msg.data = { content: '' };
  const card = el('div', { className: 'memory-item-card' });

  card.append(rowFields(
    simpleField(
      i18n('setup.memory.editor.field.type'),
      selectInput(msg.type ?? 'human', [
        ['human',  i18n('setup.memory.editor.message_type.human')],
        ['ai',     i18n('setup.memory.editor.message_type.ai')],
        ['system', i18n('setup.memory.editor.message_type.system')],
      ], v => { msg.type = v; notify(); }),
      { narrow: true },
    ),
  ));

  // LangChain multimodal ??: content ???
  //   (a) ???? (?????), ?? textarea ??.
  //   (b) list-of-parts, ?? [{type:'text', text:'...'}, {type:'image_url', ...}],
  //       ???? text ??? textarea (????????, ???);
  //       ????????/???????? hint ??, ??????? Raw.
  //   (c) list ??? text ?????, ? content ? null/????: ??
  //       ???? + ??? Raw ??.
  if (typeof msg.data.content === 'string') {
    card.append(simpleField(
      i18n('setup.memory.editor.field.content'),
      textareaInput(msg.data.content, v => { msg.data.content = v; notify(); }),
    ));
  } else if (Array.isArray(msg.data.content)) {
    const parts = msg.data.content;
    const textIdx = parts.findIndex(p => p && typeof p === 'object' && p.type === 'text' && typeof p.text === 'string');
    if (textIdx >= 0) {
      const textPartsCount = parts.filter(p => p && typeof p === 'object' && p.type === 'text' && typeof p.text === 'string').length;
      const nonTextCount = parts.length - textPartsCount;
      card.append(simpleField(
        i18n('setup.memory.editor.field.content'),
        textareaInput(parts[textIdx].text, v => { parts[textIdx].text = v; notify(); }),
      ));
      const hintBits = [];
      if (nonTextCount > 0) hintBits.push(i18n('setup.memory.editor.multimodal_extras', nonTextCount));
      if (textPartsCount > 1) hintBits.push(i18n('setup.memory.editor.multimodal_multi_text', textPartsCount));
      if (hintBits.length) {
        card.append(el('div',
          { className: 'memory-field-hint', style: { marginTop: '-4px' } },
          hintBits.join(' | '),
        ));
      }
    } else {
      card.append(simpleField(
        i18n('setup.memory.editor.field.content'),
        el('div', { className: 'empty-state warn tiny', style: { textAlign: 'left' } },
          i18n('setup.memory.editor.complex_content_hint')),
      ));
    }
  } else {
    card.append(simpleField(
      i18n('setup.memory.editor.field.content'),
      el('div', { className: 'empty-state warn tiny', style: { textAlign: 'left' } },
        i18n('setup.memory.editor.complex_content_hint')),
    ));
  }

  // ??: data ????? (additional_kwargs / id / name ?? non-string content)
  // ???? raw JSON textarea, ???????????.
  card.append(advancedBlock([
    simpleField(
      i18n('setup.memory.editor.field.extra_data'),
      rawJsonInput(getExtraData(msg.data), v => { setExtraData(msg, v); notify(); }),
    ),
  ]));

  card.append(deleteCornerButton(() => { list.splice(idx, 1); restructure(); }));
  return card;
}

function getExtraData(data) {
  const extra = {};
  for (const [k, v] of Object.entries(data || {})) {
    if (k === 'content') continue;
    extra[k] = v;
  }
  return extra;
}

function setExtraData(msg, extra) {
  const content = msg.data?.content ?? '';
  const next = { content };
  if (extra && typeof extra === 'object' && !Array.isArray(extra)) {
    Object.assign(next, extra);
  }
  msg.data = next;
}

// ?? default entry factories ??????????????????????????????????????????

function tsNow() {
  // ???? datetime.now().isoformat() ??: ?? naive ISO, ????.
  // new Date().toISOString() ? UTC + .SSSZ, ????? YYYY-MM-DDTHH:MM:SS.
  const d = new Date();
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T`
       + `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function randHex(n) {
  let out = '';
  const chars = '0123456789abcdef';
  for (let i = 0; i < n; i++) out += chars[Math.floor(Math.random() * 16)];
  return out;
}

function compactTs() {
  const d = new Date();
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}`
       + `${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
}

function defaultPersonaFact() {
  return {
    id: `manual_${compactTs()}_${randHex(8)}`,
    text: '',
    source: 'manual',
    source_id: null,
    recent_mentions: [],
    suppress: false,
    suppressed_at: null,
    protected: false,
  };
}

function defaultFactEntry() {
  return {
    id: `fact_${compactTs()}_${randHex(8)}`,
    text: '',
    importance: 3,
    entity: 'master',
    tags: [],
    hash: '',
    created_at: tsNow(),
    absorbed: false,
  };
}

function defaultReflectionEntry() {
  const created = tsNow();
  return {
    id: `ref_${compactTs()}`,
    text: '',
    entity: 'master',
    status: 'pending',
    source_fact_ids: [],
    created_at: created,
    feedback: null,
    next_eligible_at: created,
  };
}

function defaultRecentMessage() {
  return { type: 'human', data: { content: '' } };
}

// ?? small input helpers ??????????????????????????????????????????????

function textInput(value, onChange) {
  const input = el('input', { type: 'text', value: String(value ?? '') });
  input.addEventListener('input', () => onChange(input.value));
  return input;
}

function numberInput(value, onChange, { min, max } = {}) {
  const input = el('input', { type: 'number', value: Number.isFinite(value) ? value : '' });
  if (min != null) input.min = min;
  if (max != null) input.max = max;
  input.addEventListener('input', () => {
    const raw = input.value;
    if (raw === '') { onChange(null); return; }
    const n = Number(raw);
    onChange(Number.isFinite(n) ? n : raw);
  });
  return input;
}

/**
 * ????? textarea: ???????? (????????????).
 *
 *   - ?? rows=1 (?? CSS), ??????.
 *   - ?? `foldThresholdPx` (~16 ?): ??? `foldDisplayPx` (~8 ?) ?
 *     + ????? "??/??"; ?????????, ???????????.
 *     ?????/????????????????.
 *
 * ?????? CSS (`.memory-field textarea` ? `--bg-panel` ??) ?????.
 */
function textareaInput(value, onChange, { rows = 1 } = {}) {
  const t = el('textarea', { rows, spellcheck: false, value: String(value ?? '') });
  // Initial inline height so first paint is short even before rAF fires the real
  // autosize. Without this the textarea briefly rendered at the browser default
  // (chrome gives ~2 lines + some padding, Firefox gives rows=1 but still ~40px);
  // users perceived it as "7 rows of empty space reserved for the textarea".
  t.style.height = '28px';
  t.addEventListener('input', () => onChange(t.value));
  return wrapWithAutosize(t);
}

function wrapWithAutosize(textarea, {
  foldThresholdPx = 320,
  foldDisplayPx = 160,
} = {}) {
  textarea.classList.add('memory-textarea-auto');
  const toggleBtn = el('button', {
    type: 'button',
    className: 'button tiny memory-textarea-toggle',
  });
  toggleBtn.hidden = true;
  let expanded = false;

  const resize = () => {
    // ???? DOM ? scrollHeight ? 0, ??????.
    if (!textarea.isConnected) { requestAnimationFrame(resize); return; }
    textarea.style.height = 'auto';
    // 24 ???? input ?????? (font 13 * line-height 1.4 + padding 3+3 ? 24.2);
    // ??? textarea ??????, ???? 0.
    const fullH = Math.max(textarea.scrollHeight, 24);
    if (fullH > foldThresholdPx) {
      toggleBtn.hidden = false;
      if (expanded) {
        textarea.style.height = fullH + 'px';
        textarea.style.overflowY = 'hidden';
        toggleBtn.textContent = i18n('setup.memory.editor.textarea.collapse');
      } else {
        textarea.style.height = foldDisplayPx + 'px';
        textarea.style.overflowY = 'auto';
        toggleBtn.textContent = i18n('setup.memory.editor.textarea.expand');
      }
    } else {
      toggleBtn.hidden = true;
      textarea.style.height = fullH + 'px';
      textarea.style.overflowY = 'hidden';
    }
  };

  textarea.addEventListener('input', resize);
  toggleBtn.addEventListener('click', () => { expanded = !expanded; resize(); });
  requestAnimationFrame(resize);

  return el('div', { className: 'memory-textarea-wrap' }, textarea, toggleBtn);
}

function selectInput(value, options, onChange) {
  const s = el('select');
  let matched = false;
  for (const opt of options) {
    const [val, label] = Array.isArray(opt) ? opt : [opt, opt];
    const option = el('option', { value: val }, label);
    if (val === value) { option.selected = true; matched = true; }
    s.append(option);
  }
  if (!matched && value != null && value !== '') {
    const opt = el('option', { value }, `${value}`);
    opt.selected = true;
    s.append(opt);
  }
  s.addEventListener('change', () => onChange(s.value));
  return s;
}

/** entity ??: datalist ?? master/neko/relationship/world, ???????. */
function entityInput(value, onChange) {
  // HTMLInputElement.list ? **?? getter** (????? datalist ??), ??
  // `el('input', { list: id })` ?? TypeError; ??? setAttribute ??.
  const id = `entity-suggest-${Math.random().toString(36).slice(2, 8)}`;
  const input = el('input', { type: 'text', value: String(value ?? '') });
  input.setAttribute('list', id);
  const datalist = el('datalist', { id },
    el('option', { value: 'master' }),
    el('option', { value: 'neko' }),
    el('option', { value: 'relationship' }),
    el('option', { value: 'world' }),
  );
  input.addEventListener('input', () => onChange(input.value));
  const wrap = el('span', { style: { display: 'contents' } }, input, datalist);
  return wrap;
}

function checkboxInput(value, onChange) {
  const c = el('input', { type: 'checkbox', checked: !!value });
  c.addEventListener('change', () => onChange(!!c.checked));
  return c;
}

function tagsInput(value, onChange) {
  // ??? string[] (?? parse ??). ??: ????, ?????? trim.
  const arr = Array.isArray(value) ? value.map(x => String(x)) : [];
  const input = el('input', { type: 'text', value: arr.join(', ') });
  input.addEventListener('input', () => {
    const parts = input.value.split(',').map(s => s.trim()).filter(s => s.length > 0);
    onChange(parts);
  });
  return input;
}

function rawJsonInput(value, onChange) {
  const t = el('textarea', {
    rows: 1,
    spellcheck: false,
    value: JSON.stringify(value ?? {}, null, 2),
  });
  const status = el('span', { className: 'badge', style: { marginLeft: '6px' } });
  status.style.display = 'none';
  t.addEventListener('input', () => {
    const raw = t.value.trim();
    if (!raw) { status.style.display = 'none'; onChange({}); return; }
    try {
      const parsed = JSON.parse(raw);
      status.style.display = 'none';
      onChange(parsed);
    } catch (exc) {
      status.className = 'badge err';
      status.textContent = i18n('setup.memory.editor.invalid',
        String(exc.message || exc).slice(0, 30));
      status.style.display = '';
    }
  });
  const wrap = wrapWithAutosize(t);
  wrap.append(status);
  return wrap;
}

// ?? layout helpers ???????????????????????????????????????????????????

function simpleField(label, control, { narrow = false } = {}) {
  const cls = narrow ? 'memory-field narrow' : 'memory-field';
  return el('div', { className: cls },
    el('label', {}, label),
    control,
  );
}

/**
 * Inline field ? checkbox / ?? radio ???????.
 *
 * ? `simpleField` ????: label ????? (?????), inline ? checkbox
 * ? `flex: 0 0 auto` ????, ????? `[?] ??` ?????, ????
 * ???????; ??? `.memory-field` ? `flex: 1 1 160px` ?? 160px+.
 */
function inlineField(label, control) {
  return el('label', { className: 'memory-field-inline' },
    control,
    el('span', {}, label),
  );
}

function rowFields(...fields) {
  return el('div', { className: 'memory-field-row' }, ...fields);
}

function advancedBlock(children) {
  const details = el('details', { className: 'memory-advanced' });
  const summary = el('summary', {}, i18n('setup.memory.editor.advanced_toggle'));
  details.append(summary, ...children);
  return details;
}

/**
 * ?????? "??" ????. ???????????? (????? ~40px ?
 * ??), ??? advanced ???????; ???? ghost ???, ???
 * hover ?????.
 *
 * ??: ?????? `.memory-item-card` ? `position: relative`, CSS ?
 * ?????? (? testbench.css), ?? append ? card ??.
 */
function deleteCornerButton(onDelete) {
  const btn = el('button', {
    type: 'button',
    className: 'tiny ghost memory-item-delete-corner',
  }, i18n('setup.memory.editor.delete_item'));
  btn.addEventListener('click', onDelete);
  return btn;
}

/**
 * ?? "+ ??..." ??: ??, ?????? "????????".
 * ?? list ?? or entity group ??, ????????? creative action.
 */
function addButton(labelText, onClick) {
  const btn = el('button', { className: 'memory-add-button', type: 'button' },
    `+ ${labelText}`);
  btn.addEventListener('click', onClick);
  return btn;
}

// ?? misc ?????????????????????????????????????????????????????????????

function isPlainObject(v) {
  return v != null && typeof v === 'object' && !Array.isArray(v);
}
