import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeKatex from 'rehype-katex';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';

function looksLikeRichText(text: string) {
  return (
    /```[\s\S]*```/.test(text)
    || /`[^`\n]+`/.test(text)
    || /(?:^|\n)\s{0,3}(?:#{1,6}\s|[-*+]\s|\d+\.\s|>\s)/.test(text)
    || /\[[^\]]+\]\((https?:\/\/|\/)[^)]+\)/.test(text)
    || /(?:^|\n)\|.+\|.+(?:\n|\r\n)\|(?:[-: ]+\|){1,}/.test(text)
    || /\$\$[\s\S]+?\$\$/.test(text)
    || /(?<!\$)\$(?!\$)[^$\n]+(?<!\$)\$(?!\$)/.test(text)
    || /https?:\/\/\S+/.test(text)
  );
}

function CodeBlock({ inline, className, children }: {
  inline?: boolean;
  className?: string;
  children?: React.ReactNode;
}) {
  const language = className?.replace(/^language-/, '') || '';
  const content = String(children ?? '').replace(/\n$/, '');

  if (inline) {
    return <code className="message-markdown-inline-code">{content}</code>;
  }

  return (
    <div className="message-code-block">
      {language ? <div className="message-code-language">{language}</div> : null}
      <pre className="message-markdown-pre">
        <code className={className}>{content}</code>
      </pre>
    </div>
  );
}

/**
 * Streaming text with chunk-based reveal animation.
 *
 * Text is split into "settled" (plain text) and "fresh" (a single <span> with
 * a CSS fade-in).  Every ~200 ms the current batch settles and a new span
 * starts, giving a continuous "materialising" cadence similar to ChatGPT /
 * Claude.ai.
 *
 * Key design choices:
 * - Timer is managed via refs so that incoming streaming updates (text.length
 *   changes) do NOT cancel an in-flight timer.  The old useEffect-cleanup
 *   approach reset the 200 ms timer on every token, meaning settledLen never
 *   advanced during fast streaming.
 * - When the timer fires it reads textLenRef.current (the LATEST length), so
 *   each batch captures exactly 200 ms worth of tokens.
 * - `key={settledLen}` on the span forces React to create a fresh DOM node
 *   each batch, which re-triggers the CSS animation.
 */
function StreamingText({ text }: { text: string }) {
  const [settledLen, setSettledLen] = useState(0);
  const textLenRef = useRef(text.length);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  textLenRef.current = text.length;

  // Kick off a 200 ms settle timer whenever fresh text exists and no timer is
  // already running.  Runs after every render but the guard makes it cheap.
  useEffect(() => {
    if (textLenRef.current > settledLen && timerRef.current === null) {
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        setSettledLen(textLenRef.current);   // snapshot at fire-time
      }, 200);
    }
  });

  // Clear timer on unmount only
  useEffect(() => () => {
    if (timerRef.current !== null) clearTimeout(timerRef.current);
  }, []);

  const fresh = text.slice(settledLen);

  if (!fresh) {
    return <div className="message-block message-block-text">{text}</div>;
  }

  return (
    <div className="message-block message-block-text">
      {text.slice(0, settledLen)}
      <span key={settledLen} className="text-chunk-reveal">{fresh}</span>
    </div>
  );
}

export default function SmartTextBlock({ text, isStreaming }: { text: string; isStreaming?: boolean }) {
  if (!looksLikeRichText(text)) {
    if (isStreaming) {
      return <StreamingText text={text} />;
    }
    return <div className="message-block message-block-text">{text}</div>;
  }

  return (
    <div className="message-block message-block-markdown" data-render-mode="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          code: CodeBlock,
          a: (props: React.ComponentPropsWithoutRef<'a'>) => <a {...props} target="_blank" rel="noreferrer" />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
