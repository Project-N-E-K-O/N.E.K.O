import { useEffect, useRef, useState } from 'react';
import { i18n } from './i18n';
import type { ChatMessage, MessageAction, MessageBlock } from './message-schema';

type AskBlock = Extract<MessageBlock, { type: 'nekopower_ask' }>;

/**
 * How long the buttons of a question that took the place of another are held back: a press
 * meant for the one that was there a moment ago must not answer this one.
 */
export const NEKOPOWER_ASK_SETTLE_MS = 900;
// A command or path longer than this is not read at a glance: it is shown whole before an
// approval that holds for more than this once is offered.
const GLANCE_CHARS = 80;
const WIDE_CHOICES = ['always', 'session'];
const CHOICE_WORDS: Record<string, [string, string]> = {
  always: ['agent.ask.always', 'Always allow'],
  session: ['agent.ask.session', 'Allow for this task'],
  once: ['agent.ask.once', 'Just this once'],
  deny: ['agent.ask.deny', 'Deny'],
};

const text = (value: unknown) => (typeof value === 'string' ? value.trim() : '');

// What the user typed and picked for a question that is not answered yet, by task and question.
// Above the composer one question is shown at a time: one that gives way to another (of another
// task) and comes back has what was put into it still there. Let go of once the question is over.
const drafts = new Map<string, { reply: string; picked: Record<string, string[]>; sent: boolean }>();
// One whose answer is on its way is not answered yet: where that fails it waits again, and what
// was put into it is still its draft (its card may have given way to another's meanwhile).
const unanswered = (state: AskBlock['state']) => state === 'pending' || state === 'sending';

/**
 * Lets go of what was put into questions that these messages say are answered or over, and of
 * all of it once the chat is emptied. One that is over is not always drawn so (above the
 * composer its card is just taken away), and a chat that is emptied draws none of its cards
 * again: so it is the chat itself, where the messages are held, that says so.
 */
export function useNekoPowerAskDrafts(messages: ChatMessage[]): void {
  useEffect(() => {
    if (!drafts.size) return;
    // The chat was emptied: none of its questions is left, but those whose answers are on their
    // way. Their cards are put back once what waits is read anew, and where the answer then is
    // not taken the question waits again, with what was put into it.
    if (!messages.length) {
      for (const [key, draft] of [...drafts]) if (!draft.sent) drafts.delete(key);
      return;
    }
    // Otherwise only those the messages say are answered or over. One whose card is not among
    // them may have been pushed out by newer messages (the chat keeps its latest) while its
    // question still waits: it is put back, and what was typed for it is still wanted then.
    for (const message of messages) {
      for (const block of message.blocks) {
        if (block.type === 'nekopower_ask' && !unanswered(block.state)) drafts.delete(`${block.taskId}:${block.questionId}`);
      }
    }
  }, [messages]);
}

export interface AskQuestion {
  /** Where it stands among the questions ("0", "1", ...): what the answer names it by. */
  id: string;
  text: string;
  options: Array<{ label: string; description: string }>;
  multi: boolean;
}

/** The language the page is shown in, by its code (`zh-CN`, `en`, ...); '' where it cannot be read. */
function pageLanguage(): string {
  const w = window as unknown as { i18next?: { language?: unknown }; i18n?: { language?: unknown } };
  const language = (w.i18next ?? w.i18n)?.language;
  return typeof language === 'string' ? language : '';
}

/**
 * What a request for approval asks to allow, in one line: in the language of the page, as its
 * buttons are, where the question has the line in that one (`lines`); else in the language of the
 * commission (`line`). With whether that very line leaves part of the command out.
 */
function saidHere(question: Record<string, unknown>): { line: string; cut: boolean } {
  const lines = (question.lines && typeof question.lines === 'object' ? question.lines : {}) as Record<string, unknown>;
  const language = pageLanguage();
  const mine = (lines[language] ?? lines[language.split('-')[0]]) as Record<string, unknown> | undefined;
  if (mine && typeof mine === 'object' && text(mine.line)) return { line: text(mine.line), cut: mine.line_cut === true };
  return { line: text(question.line), cut: question.line_cut === true };
}

/** What a question of a NekoPower commission is shown and answered by. */
export function askView(question: Record<string, unknown>) {
  const detail = (question.detail && typeof question.detail === 'object' ? question.detail : {}) as Record<string, unknown>;
  const options = (Array.isArray(question.options) ? question.options : [])
    .filter((option): option is string => typeof option === 'string' && option in CHOICE_WORDS);
  const questions: AskQuestion[] = (Array.isArray(question.questions) ? question.questions : []).flatMap((item, index) => {
    if (!item || typeof item !== 'object') return [];
    const asked = item as Record<string, unknown>;
    const offered = (Array.isArray(asked.options) ? asked.options : [])
      .map(option => (option && typeof option === 'object'
        ? { label: text((option as Record<string, unknown>).label), description: text((option as Record<string, unknown>).description) }
        : { label: text(String(option ?? '')), description: '' }))
      .filter(option => option.label);
    return [{ id: String(index), text: text(asked.question), options: offered, multi: asked.multi_select === true }];
  });
  const said = saidHere(question);
  const lineCut = said.cut;
  // The line that says it left part of something out: of the command or path, or (where it names
  // neither) of whatever else it is said by, and of whom something is delivered to.
  const whole = lineCut
    ? [text(detail.command) || text(detail.path) || text(question.key) || text(detail.title) || text(detail.tool), text(detail.content_to)]
      .filter(Boolean).join('\n')
    : text(detail.command) || text(detail.path);
  const shape = options.length ? 'permission' as const
    : question.kind === 'confirmation' ? 'confirmation' as const
      : questions.some(item => item.options.length) ? 'questions' as const : 'words' as const;
  const line = shape === 'permission'
    ? said.line || whole || text(detail.title) || text(detail.tool) || text(question.category)
    : [text(question.question), text(question.details)].filter(Boolean).join(' ');
  return {
    shape,
    line,
    whole,
    // The folder a command is to be run in, where it names one: the same command does something
    // else in another project, so it is said beside what is approved.
    cwd: shape === 'permission' ? text(detail.cwd) : '',
    // The line that says it leaves part of the command or path out.
    lineCut: shape === 'permission' && lineCut,
    // Whether all of what it would do has to be opened before a wider approval is offered.
    needsWhole: shape === 'permission' && (lineCut || (Boolean(whole) && (whole.length > GLANCE_CHARS || whole.includes('\n')))),
    options,
    questions,
    quick: questions.length === 1 && !questions[0].multi,
  };
}

/**
 * What a NekoPower commission asks the user, answered where it is shown: buttons for what can
 * be picked and a line for words beside them. The host sends the answer and says how it fared
 * (`state`): this only hands over what was pressed.
 *
 * `settle`: it took the place of another question a moment ago (in the slot above the
 * composer), so its buttons wait for a moment.
 */
export default function NekoPowerAskBlock({ block, message, interactive, onAction, presentation = 'card', settle = false }: {
  block: AskBlock;
  message: ChatMessage;
  interactive: boolean;
  presentation?: 'card' | 'bubble';
  settle?: boolean;
  onAction?: (message: ChatMessage, action: MessageAction) => void;
}) {
  const view = askView(block.question);
  const draftKey = `${block.taskId}:${block.questionId}`;
  const [reply, setReply] = useState(() => drafts.get(draftKey)?.reply ?? '');
  const [picked, setPicked] = useState<Record<string, string[]>>(() => drafts.get(draftKey)?.picked ?? {});
  // Drawn anew when the page is put in another language (or its words have come): the line is
  // then said in that one (`askView`), and the words of the buttons with it.
  const [, setRedrawn] = useState(0);
  useEffect(() => {
    const redraw = () => setRedrawn(count => count + 1);
    window.addEventListener('localechange', redraw);
    return () => window.removeEventListener('localechange', redraw);
  }, []);
  useEffect(() => {
    // Kept until the question is answered or over, also while an answer is on its way. (Above the
    // composer a card is taken away without being drawn as answered: the chat lets go of what is
    // left of it then, `useNekoPowerAskDrafts`.) Put in anew each time, so that the one let go of
    // when there are too many is the one left longest.
    drafts.delete(draftKey);
    if (unanswered(block.state) && (reply || Object.values(picked).some(labels => labels.length))) {
      drafts.set(draftKey, { reply, picked, sent: block.state === 'sending' });
    }
    if (drafts.size > 50) drafts.delete(drafts.keys().next().value as string);
  }, [draftKey, block.state, reply, picked]);
  // Whether a word is being composed in the line (an input method is at work). Not for good where
  // its end is never told (the line was disabled or left meanwhile): Enter and "send" would do
  // nothing from then on.
  const composing = useRef(false);
  useEffect(() => { composing.current = false; }, [block.state]);
  const [wholeOpen, setWholeOpen] = useState(false);
  const [settling, setSettling] = useState(settle);
  useEffect(() => {
    if (!settle) return;
    const timer = setTimeout(() => setSettling(false), NEKOPOWER_ASK_SETTLE_MS);
    return () => clearTimeout(timer);
  }, [settle]);

  const open = block.state === 'pending' || block.state === 'sending';
  const held = !interactive || block.state !== 'pending' || settling;
  const somePicked = Object.values(picked).some(labels => labels.length);
  const give = (given: Record<string, unknown>) => {
    if (held) return;
    onAction?.(message, {
      id: 'answer', label: 'answer', action: 'nekopower_ask_answer',
      payload: { taskId: block.taskId, questionId: block.questionId, ...given },
    });
  };
  const pick = (item: AskQuestion, label: string) => {
    if (held) return;
    if (view.quick) {
      give({ selected: { [item.id]: [label] }, text: reply });
      return;
    }
    const had = picked[item.id] ?? [];
    const now = had.includes(label) ? had.filter(each => each !== label) : item.multi ? [...had, label] : [label];
    setPicked({ ...picked, [item.id]: now });
  };
  const sendWords = () => {
    // Words alone are no answer to a request for approval: they would be read as a refusal.
    if (held || view.shape === 'permission' || (!reply.trim() && !somePicked)) return;
    give(somePicked ? { selected: picked, text: reply } : { text: reply });
  };
  const compact = presentation === 'bubble';
  // The card shows the line whole, so only one that was cut has more to open; above the composer
  // the line is clamped, and anything not read at a glance is opened first.
  const needsWhole = compact ? view.needsWhole : view.lineCut;
  const hit = (id: string) => (compact ? {
    'data-compact-hit-region': 'true', 'data-compact-hit-region-id': `nekopower-ask:${block.taskId}:${id}`,
    'data-compact-hit-region-kind': 'message-action', 'data-compact-no-drag': 'true',
  } : {});
  const placeholder = view.shape === 'permission'
    ? i18n('agent.ask.notePlaceholder', 'Add a sentence (optional), then press a button')
    : view.shape === 'words'
      ? i18n('agent.ask.replyPlaceholder', 'Type your answer…')
      : i18n('agent.ask.addPlaceholder', 'Type an answer, or add a sentence and pick above');

  return (
    <section className={`message-block nekopower-ask nekopower-ask-${presentation} nekopower-ask-${block.state}`}
      data-nekopower-ask={block.taskId} data-question-id={block.questionId} data-shape={view.shape} data-compact-no-drag="true"
      aria-label={i18n('agent.ask.title', 'NekoPower · waiting for you')}>
      <header className="nekopower-ask-head">
        <strong>🐾 {i18n('agent.ask.title', 'NekoPower · waiting for you')}</strong>
        {block.title ? <span>{block.title}</span> : null}
      </header>

      {/* Its questions each on a line, with what they offer where they offer something; else one line. */}
      {view.questions.length ? view.questions.map((item, index) => (
        <div key={item.id} className="nekopower-ask-question" data-question={item.id}>
          <p className="nekopower-ask-line">{view.questions.length > 1 ? `${index + 1}. ${item.text}` : item.text}</p>
          {item.options.length && open ? (
            <div className="nekopower-ask-options" role="group" aria-label={item.text}>
              {item.options.map(option => {
                const on = (picked[item.id] ?? []).includes(option.label);
                return (
                  <button key={option.label} type="button" className={`nekopower-ask-option${on ? ' is-on' : ''}`}
                    aria-pressed={view.quick ? undefined : on} disabled={held} data-option={option.label}
                    {...hit(`option:${item.id}:${option.label}`)} onClick={() => pick(item, option.label)}>
                    <b>{option.label}</b>
                    {option.description && !compact ? <span>{option.description}</span> : null}
                  </button>
                );
              })}
            </div>
          ) : null}
        </div>
      )) : (
        <>
          {/* What an approval is asked for is drawn whole where nothing here opens all of it. */}
          <p className={`nekopower-ask-line${view.shape === 'permission' && !(needsWhole && view.whole) ? ' nekopower-ask-line-whole' : ''}`}>{view.line}</p>
          {view.cwd ? <p className="nekopower-ask-cwd" data-ask-cwd="true">{i18n('agent.ask.cwd', 'In folder')} {view.cwd}</p> : null}
        </>
      )}

      {open && view.shape === 'permission' ? (
        <>
          {needsWhole && view.whole ? (
            <>
              {wholeOpen ? <pre className="nekopower-ask-whole" tabIndex={0}>{view.whole}</pre> : null}
              <div className="nekopower-ask-row">
                <button type="button" className="nekopower-ask-button is-plain" data-ask-whole="toggle" {...hit('whole')}
                  onClick={() => setWholeOpen(!wholeOpen)}>
                  {wholeOpen ? i18n('agent.ask.hideWhole', 'Show less') : i18n('agent.ask.showWhole', 'Show all of it')}
                </button>
              </div>
            </>
          ) : null}
          <div className="nekopower-ask-row" role="group">
            {view.options
              // An approval that holds for more than this once: only with all of what it would do in view.
              .filter(option => !WIDE_CHOICES.includes(option) || !needsWhole || wholeOpen)
              .map(option => (
                <button key={option} type="button" className={`nekopower-ask-button${option === 'deny' ? ' is-deny' : ''}`}
                  disabled={held} data-choice={option} {...hit(`choice:${option}`)}
                  onClick={() => give({ choice: option, note: reply })}>
                  {i18n(CHOICE_WORDS[option][0], CHOICE_WORDS[option][1])}
                </button>
              ))}
          </div>
          {needsWhole && !wholeOpen && view.options.some(option => WIDE_CHOICES.includes(option)) ? (
            <p className="nekopower-ask-hint">{view.whole
              ? i18n('agent.ask.wideHint', 'Wider approvals appear once all of it is shown')
              // Cut, and nothing here to show it whole by: the wider approvals are given elsewhere.
              : i18n('agent.ask.wideBlocked', 'Not all of it can be shown here: give a wider approval in the NekoPower window')}</p>
          ) : null}
        </>
      ) : null}

      {open && view.shape === 'confirmation' ? (
        <div className="nekopower-ask-row" role="group">
          <button type="button" className="nekopower-ask-button" disabled={held} data-confirm="yes" {...hit('yes')}
            onClick={() => give({ choice: 'yes', text: reply })}>{i18n('agent.ask.yes', 'Yes, go ahead')}</button>
          <button type="button" className="nekopower-ask-button is-deny" disabled={held} data-confirm="no" {...hit('no')}
            onClick={() => give({ choice: 'no', text: reply })}>{i18n('agent.ask.no', 'No')}</button>
        </div>
      ) : null}

      {open && !(view.shape === 'permission' && block.noNote) ? (
        <form className="nekopower-ask-say" onSubmit={event => { event.preventDefault(); if (!composing.current) sendWords(); }}>
          <input type="text" value={reply} placeholder={placeholder} aria-label={placeholder} disabled={!interactive || block.state !== 'pending'}
            data-ask-input="true" {...hit('input')}
            onChange={event => setReply(event.target.value)}
            onCompositionStart={() => { composing.current = true; }}
            onCompositionEnd={() => { composing.current = false; }}
            onBlur={() => { composing.current = false; }}
            // Words alone are no answer to a request for approval (they would read as a refusal):
            // there the line only goes along with a button. And the Enter that picks a word being
            // composed (Chinese, Japanese) is no Enter of the form: it would send what is half typed.
            onKeyDown={event => {
              event.stopPropagation();
              const picksWord = event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229 || composing.current;
              if (event.key === 'Enter' && (view.shape === 'permission' || picksWord)) event.preventDefault();
            }} />
          {view.shape !== 'permission' ? (
            <button type="submit" className="nekopower-ask-button is-solid" disabled={held || (!reply.trim() && !somePicked)} data-ask-send="true" {...hit('send')}>
              {i18n('agent.ask.send', 'Send')}
            </button>
          ) : null}
        </form>
      ) : null}

      {block.state === 'answered' ? (
        <p className="nekopower-ask-result" role="status">
          {block.answer ? i18n('agent.ask.answered', 'Answered: {{answer}}', { answer: block.answer }) : i18n('agent.ask.answeredPlain', 'Answered')}
        </p>
      ) : null}
      {block.state === 'closed' && !block.notice ? (
        <p className="nekopower-ask-result is-gone" role="status">{i18n('agent.ask.closed', 'This question is over')}</p>
      ) : null}
      {block.notice ? <p className="nekopower-ask-notice" role="status">{block.notice}</p> : null}
    </section>
  );
}
