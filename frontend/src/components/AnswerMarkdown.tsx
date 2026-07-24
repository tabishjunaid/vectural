import { Children, isValidElement, type ReactNode } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { CitationChip } from './citation';
import { MermaidDiagram } from './MermaidDiagram';

/* Production markdown renderer (react-markdown + remark-gfm, per the plan).
   Two Vectural-specific behaviours layered on top:
   - inline [n] citation markers become interactive <CitationChip> (R1),
     applied only to text nodes so code content is never touched;
   - fenced code renders in the dark .code-block shell with a copy button.
   Syntax highlighting (Shiki) is deferred to UI-1. */

// Replace [n] markers inside plain-text nodes with citation chips. Runs on
// string children only, so code spans / code blocks are left intact.
function injectCitations(children: ReactNode): ReactNode {
  return Children.map(children, (child) => {
    if (typeof child !== 'string') return child;
    const parts = child.split(/\[(\d+)\]/g);
    if (parts.length === 1) return child;
    return parts.map((part, i) =>
      // Odd indices are the captured citation numbers.
      i % 2 === 1 ? <CitationChip key={i} id={Number(part)} /> : part,
    );
  });
}

function extractText(node: ReactNode): string {
  if (typeof node === 'string') return node;
  if (Array.isArray(node)) return node.map(extractText).join('');
  if (isValidElement(node)) return extractText((node.props as { children?: ReactNode }).children);
  return '';
}

function CodeBlock({ lang, code }: { lang: string; code: string }) {
  const copy = (e: React.MouseEvent<HTMLButtonElement>) => {
    const btn = e.currentTarget;
    navigator.clipboard?.writeText(code);
    const original = btn.innerText;
    btn.innerText = 'Copied';
    setTimeout(() => (btn.innerText = original), 1200);
  };
  return (
    <div className="code-block">
      <div className="code-block-head">
        <span>{lang}</span>
        <button className="copy-btn" onClick={copy}>
          Copy
        </button>
      </div>
      <pre>
        <code>{code.replace(/\n$/, '')}</code>
      </pre>
    </div>
  );
}

const components: Components = {
  // The mockup mapped ## -> h3 and ### -> h4; preserve that visual hierarchy.
  h2: ({ children }) => <h3>{injectCitations(children)}</h3>,
  h3: ({ children }) => <h4>{injectCitations(children)}</h4>,
  p: ({ children }) => <p>{injectCitations(children)}</p>,
  li: ({ children }) => <li>{injectCitations(children)}</li>,
  strong: ({ children }) => <strong>{injectCitations(children)}</strong>,
  pre: ({ children }) => {
    const codeEl = Array.isArray(children) ? children[0] : children;
    const className = isValidElement(codeEl)
      ? ((codeEl.props as { className?: string }).className ?? '')
      : '';
    const lang = /language-(\w+)/.exec(className)?.[1] ?? 'text';
    const code = isValidElement(codeEl)
      ? extractText((codeEl.props as { children?: ReactNode }).children)
      : extractText(children);
    // A ```mermaid block is a diagram, not code — render it visually (§the product
    // is about graphs). Everything else stays in the dark code shell.
    if (lang === 'mermaid') return <MermaidDiagram code={code} />;
    return <CodeBlock lang={lang} code={code} />;
  },
};

export function AnswerMarkdown({ markdown }: { markdown: string }) {
  return (
    <div className="answer-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
