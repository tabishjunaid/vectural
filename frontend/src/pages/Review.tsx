import { useEffect, useState } from 'react';
import { AppShell } from '../components/AppShell';
import { AnswerMarkdown } from '../components/AnswerMarkdown';
import { ConfirmDialog } from '../components/ConfirmDialog';
import type { ReviewItem } from '../lib/mock-data';
import { getReviewQueue, reviewAction, type ReviewVerb } from '../lib/api';

/* Flow Narrative Review — live wired to /review. The only write surface in the
   product; every other screen is read-only Q&A. Architect-gated. Approving a
   narrative makes it authoritative and (via /coverage) bumps its services to
   tier 4. */
export function Review() {
  const [queue, setQueue] = useState<ReviewItem[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [dialog, setDialog] = useState<{ open: boolean; message: string }>({ open: false, message: '' });
  const [error, setError] = useState<string | null>(null);

  const load = () =>
    getReviewQueue()
      .then((items) => {
        setQueue(items);
        setActiveId((cur) => (cur && items.some((i) => i.id === cur) ? cur : (items[0]?.id ?? null)));
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'failed to load review queue'));

  useEffect(() => {
    void load();
  }, []);

  const item = queue.find((r) => r.id === activeId) ?? queue[0];

  const act = async (verb: ReviewVerb, label: string) => {
    if (!item) return;
    try {
      await reviewAction(item.id, verb, 'A. Architect', verb === 'request-changes' ? 'Please revise' : undefined);
      setDialog({ open: true, message: `"${item.title}" was ${label}. The queue and coverage are now updated.` });
      await load();
    } catch (e) {
      setDialog({ open: true, message: `Action failed: ${e instanceof Error ? e.message : 'error'}` });
    }
  };

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

          {error && <p style={{ color: 'var(--amber)' }}>{error}</p>}
          {!error && queue.length === 0 && (
            <p style={{ color: 'var(--text-dim)' }}>Review queue is empty — every flow is approved.</p>
          )}

          {item && (
            <div className="panel review-layout" style={{ minHeight: 480 }}>
              <div className="review-queue-list">
                {queue.map((q) => (
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

                <div className="review-actions">
                  <button className="btn-approve" onClick={() => act('approve', 'approved')}>
                    Approve
                  </button>
                  <button
                    className="btn-request"
                    onClick={() => act('request-changes', 'sent back for changes')}
                  >
                    Request changes
                  </button>
                  <button className="btn-reject" onClick={() => act('reject', 'rejected')}>
                    Reject
                  </button>
                </div>
              </div>
            </div>
          )}
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
