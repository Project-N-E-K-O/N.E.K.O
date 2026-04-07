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

export default function SmartTextBlock({ text }: { text: string }) {
  if (!looksLikeRichText(text)) {
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
