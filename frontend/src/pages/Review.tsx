import { useState } from 'react';
import { AppShell } from '../components/AppShell';
import { AnswerMarkdown } from '../components/AnswerMarkdown';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { useCitationDrawer } from '../components/citation';
import { CITATIONS, REVIEW_QUEUE, type Citation } from '../lib/mock-data';

function citationLabel(c: Citation): string {
  return c.kind === 'code' ? `${c.service}/${c.file}:${c.line}` : `${c.service} → flow: "${c.flow}"`;
}

/* Flow Narrative Review — ports ux-design/review.html. The only write surface
   in the product; every other screen is read-only Q&A. Architect-gated (UI-4
   wires the real approve/reject workflow — actions here are mock confirmations). */
export function Review() {
  const [activeId, setActiveId] = useState(REVIEW_QUEUE[0].id);
  const [dialog, setDialog] = useState<{ open: boolean; message: string }>({ open: false, message: '' });
  const { openCitation } = useCitationDrawer();

  const item = REVIEW_QUEUE.find((r) => r.id === activeId) ?? REVIEW_QUEUE[0];

  const act = (verb: string) =>
    setDialog({
      open: true,
      message: `Mock action — "${item.title}" would be marked ${verb}. (No backend in UI-0.)`,
    });

  return (
    <AppShell
      title="Flow Narrative Review"
      activeNav="review"
      topbarRight={<span className="persona-pill">Architect only</span>}
    >
      <div className="main-scroll">
        <div className="main-inner" style={{ maxWidth: 1040 }}>
          <p style={{ color: 'var(--text-dim)', fontSize: 13, marginTop: 0 }}>
            Tier-4 flow narratives are not authoritative until an architect approves them. When underlying
            code changes, a narrative is flagged <code>needs_review</code> and never silently regenerated —
            this is the only write surface in the product; every other screen is read-only Q&amp;A.
          </p>

          <div className="panel review-layout" style={{ minHeight: 480 }}>
            <div className="review-queue-list">
              {REVIEW_QUEUE.map((q) => (
                <div
                  key={q.id}
                  className={`queue-item ${q.id === activeId ? 'active' : ''}`}
                  onClick={() => setActiveId(q.id)}
                >
                  <div className="title">
                    {q.status === 'needs_review' ? '⚠ ' : '○ '}
                    {q.title}
                  </div>
                  <div className={`status-line ${q.status}`}>
                    {q.status === 'needs_review' ? 'needs review' : 'pending (new)'}
                  </div>
                </div>
              ))}
            </div>

            <div className="review-detail">
              <div className="review-detail-header">
                <h3>{item.title}</h3>
                <div className="reason">
                  {item.status === 'needs_review' ? '⚠ ' : ''}
                  {item.reason}
                </div>
              </div>

              <div className="review-meta-grid">
                <div>
                  <span className="k">Contributing services</span>
                  {item.contributingServices.join(', ')}
                </div>
                <div>
                  <span className="k">Trigger</span>
                  {item.trigger}
                </div>
                <div>
                  <span className="k">Last approved</span>
                  {item.lastApproved ?? 'Never approved'}
                </div>
                <div>
                  <span className="k">Status</span>
                  {item.status === 'needs_review' ? 'Needs re-review' : 'Pending first review'}
                </div>
              </div>

              <AnswerMarkdown markdown={item.markdown} />

              {item.citationIds.length > 0 && (
                <div className="sources-rail" style={{ borderTop: 'none', paddingTop: 0, marginTop: 14 }}>
                  <div className="sources-rail-label">Sources</div>
                  {item.citationIds.map((id) => (
                    <div className="source-row" key={id} onClick={() => openCitation(id)}>
                      <span className="num">[{id}]</span> {citationLabel(CITATIONS[id])}
                    </div>
                  ))}
                </div>
              )}

              <div className="review-actions">
                <button className="btn-approve" onClick={() => act('approved')}>
                  Approve
                </button>
                <button className="btn-request" onClick={() => act('sent back for changes')}>
                  Request changes
                </button>
                <button className="btn-reject" onClick={() => act('rejected')}>
                  Reject
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={dialog.open}
        onClose={() => setDialog({ open: false, message: '' })}
        title="Review action"
        message={dialog.message}
      />
    </AppShell>
  );
}
