import { useMemo, useRef } from 'react';
import { AnswerMarkdown } from './AnswerMarkdown';

/* Multi-section answer layout (like a Claude.ai explainer). The synthesis prompt
   (synth-v5, backend/answer/synthesis.py) emits each section as a `## <Name>`
   level-2 heading; we split on those, render a jump-to outline on top, and give
   each section its own card. Everything still flows through <AnswerMarkdown>, so
   citations, code blocks and Mermaid diagrams keep working unchanged.

   A short answer with no headings (or a refusal) falls back to the flat renderer,
   so nothing regresses for un-sectioned text. */

export interface AnswerSection {
  title: string;
  body: string;
}

/* Split markdown at top-level `## ` headings into an optional preamble and an
   ordered list of sections. Headings inside a fenced code block are ignored, so a
   `## comment` in a code sample never starts a spurious section. Pure + exported
   for unit testing. */
export function splitSections(markdown: string): {
  preamble: string;
  sections: AnswerSection[];
} {
  const sections: AnswerSection[] = [];
  const preambleLines: string[] = [];
  let current: { title: string; body: string[] } | null = null;
  let inFence = false;

  for (const line of markdown.split('\n')) {
    if (/^\s*(```|~~~)/.test(line)) inFence = !inFence;
    const heading = inFence ? null : /^##\s+(.+?)\s*$/.exec(line);
    if (heading) {
      if (current) sections.push({ title: current.title, body: current.body.join('\n').trim() });
      current = { title: heading[1], body: [] };
    } else if (current) {
      current.body.push(line);
    } else {
      preambleLines.push(line);
    }
  }
  if (current) sections.push({ title: current.title, body: current.body.join('\n').trim() });
  return { preamble: preambleLines.join('\n').trim(), sections };
}

// The Diagram section is already a panel (.mermaid-diagram), so it renders
// without the outer card to avoid a box-in-a-box.
function isDiagram(section: AnswerSection): boolean {
  return section.title.trim().toLowerCase() === 'diagram' || /```\s*mermaid/.test(section.body);
}

export function AnswerSections({ markdown }: { markdown: string }) {
  const { preamble, sections } = useMemo(() => splitSections(markdown), [markdown]);
  const refs = useRef<(HTMLElement | null)[]>([]);

  // Fewer than two sections: nothing to lay out — render flat, exactly as before.
  if (sections.length < 2) return <AnswerMarkdown markdown={markdown} />;

  const scrollTo = (i: number) =>
    refs.current[i]?.scrollIntoView({ behavior: 'smooth', block: 'start' });

  return (
    <div className="answer-sections">
      <nav className="answer-outline" aria-label="Answer sections">
        <span className="outline-label">On this page</span>
        {sections.map((s, i) => (
          <a
            key={`${s.title}-${i}`}
            href="#"
            onClick={(e) => {
              e.preventDefault();
              scrollTo(i);
            }}
          >
            {s.title}
          </a>
        ))}
      </nav>

      {preamble && <AnswerMarkdown markdown={preamble} />}

      {sections.map((s, i) => (
        <section
          key={`${s.title}-${i}`}
          ref={(el) => {
            refs.current[i] = el;
          }}
          className={`answer-section${isDiagram(s) ? ' no-box' : ''}`}
        >
          <div className="answer-section-label">{s.title}</div>
          <AnswerMarkdown markdown={s.body} />
        </section>
      ))}
    </div>
  );
}
