import { createContext, useContext, useState, type ReactNode } from 'react';
import { CITATIONS, type Citation } from '../lib/mock-data';

/* Citation drawer — a slide-over that resolves a citation id to its concrete
   evidence (path + line, or flow narrative). Mirrors the mockup's
   openCitationById. Verifiability is load-bearing (R1): a citation always
   resolves to a specific chunk, and this is where the user inspects it. */
interface CitationDrawerValue {
  openCitation: (id: number) => void;
}

const CitationDrawerContext = createContext<CitationDrawerValue | null>(null);

// eslint-disable-next-line react-refresh/only-export-components
export function useCitationDrawer(): CitationDrawerValue {
  const ctx = useContext(CitationDrawerContext);
  if (!ctx) throw new Error('useCitationDrawer must be used within a CitationDrawerProvider');
  return ctx;
}

function chunkPreview(c: Citation): string {
  if (c.kind === 'code') {
    const body = c.file?.includes('refund')
      ? 'def reverse_refund(refund_id: str) -> None:\n    ...'
      : 'def handle_event(evt) -> None:\n    ...';
    return `# ${c.file}, line ${c.line}\n...\n${body}\n...`;
  }
  return `Flow narrative excerpt — "${c.flow}"\nContributing services shown in the Flow Narrative Review screen.`;
}

export function CitationChip({ id }: { id: number }) {
  const { openCitation } = useCitationDrawer();
  const c = CITATIONS[id];
  return (
    <button
      type="button"
      className={`citation-chip ${c?.needsReview ? 'needs-review' : ''}`}
      onClick={() => openCitation(id)}
      aria-label={`Citation ${id}`}
    >
      {id}
    </button>
  );
}

export function CitationDrawerProvider({ children }: { children: ReactNode }) {
  const [activeId, setActiveId] = useState<number | null>(null);
  const c = activeId != null ? CITATIONS[activeId] : null;

  return (
    <CitationDrawerContext.Provider value={{ openCitation: setActiveId }}>
      {children}
      {c && (
        <>
          <div className="drawer-backdrop" onClick={() => setActiveId(null)} />
          <aside className="drawer" role="dialog" aria-label="Citation detail">
            <button className="drawer-close" onClick={() => setActiveId(null)} aria-label="Close">
              ×
            </button>
            <h4>{c.kind === 'code' ? `${c.file}:${c.line}` : `Flow: ${c.flow}`}</h4>
            <div className="drawer-service">{c.service}</div>
            <div className="drawer-meta-row">
              <span className="k">Commit</span>
              <span>{c.commit}</span>
            </div>
            <div className="drawer-meta-row">
              <span className="k">Indexed</span>
              <span>{c.indexedAt}</span>
            </div>
            <div className="drawer-meta-row">
              <span className="k">Status</span>
              <span>
                {c.stale ? (
                  <span className="badge-mini badge-stale">may be stale</span>
                ) : (
                  <span className="badge-mini badge-current">current</span>
                )}
                {c.needsReview && <span className="badge-mini badge-review"> needs review</span>}
              </span>
            </div>
            <div className="drawer-chunk">{chunkPreview(c)}</div>
          </aside>
        </>
      )}
    </CitationDrawerContext.Provider>
  );
}
