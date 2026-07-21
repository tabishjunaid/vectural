import { createContext, useContext, useMemo, useState, type ReactNode } from 'react';
import type { Citation } from '../lib/mock-data';

/* Citation drawer — a slide-over that resolves a citation id to its concrete
   evidence (path + line, or flow narrative). Verifiability is load-bearing (R1):
   a citation always resolves to a specific chunk, and this is where the user
   inspects it. The citations map is set live by whichever screen is showing an
   answer (Ask / Review), so chips and the drawer read real backend evidence. */
interface CitationDrawerValue {
  openCitation: (id: number) => void;
  setCitations: (citations: Record<number, Citation>) => void;
  citations: Record<number, Citation>;
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
    return `# ${c.file}, line ${c.line}\n(evidence chunk resolved from the retrieved set)`;
  }
  return `Flow narrative "${c.flow}" — an architect-approved cross-service narrative.`;
}

export function CitationChip({ id }: { id: number }) {
  const { openCitation, citations } = useCitationDrawer();
  const c = citations[id];
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
  const [citations, setCitations] = useState<Record<number, Citation>>({});
  const value = useMemo(
    () => ({ openCitation: setActiveId, setCitations, citations }),
    [citations],
  );
  const c = activeId != null ? citations[activeId] : null;

  return (
    <CitationDrawerContext.Provider value={value}>
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
