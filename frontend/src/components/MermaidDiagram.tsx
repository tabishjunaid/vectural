import { useEffect, useRef, useState } from 'react';

/* Renders a Mermaid diagram from the answer's ```mermaid block.

   Mermaid is heavy (~500 KB), so it is imported lazily — only a question whose
   answer actually contains a diagram pays for it, and the initial bundle stays
   lean. If the model emits invalid Mermaid we fall back to showing the source in
   a code block rather than blanking the answer: a broken diagram must never hide
   the (grounded, cited) prose around it. */

let mermaidReady: Promise<typeof import('mermaid').default> | null = null;

function loadMermaid() {
  if (!mermaidReady) {
    mermaidReady = import('mermaid').then(({ default: mermaid }) => {
      mermaid.initialize({ startOnLoad: false, theme: 'neutral', securityLevel: 'strict' });
      return mermaid;
    });
  }
  return mermaidReady;
}

let seq = 0;

export function MermaidDiagram({ code }: { code: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const id = `mermaid-${(seq += 1)}`;
    loadMermaid()
      .then((mermaid) => mermaid.render(id, code))
      .then(({ svg }) => {
        if (!cancelled && ref.current) ref.current.innerHTML = svg;
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [code]);

  if (failed) {
    // Invalid diagram — show the source instead of nothing.
    return (
      <div className="code-block">
        <div className="code-block-head">
          <span>mermaid (could not render)</span>
        </div>
        <pre>
          <code>{code.replace(/\n$/, '')}</code>
        </pre>
      </div>
    );
  }

  return <div className="mermaid-diagram" ref={ref} aria-label="diagram" />;
}
