/* Vectural mock data — typed port of ux-design/shared/mock-data.js.
   Hardcoded only; no backend. UI-0 renders every screen off this module.
   When the real endpoints land (UI-1+), these shapes become the API client's
   response types and this file is replaced by fetches, not rewritten in place. */

// ---------------------------------------------------------------------------
// Coverage manifest — single source of truth reused by the Coverage Explorer
// AND the Ask screen's refusal card, per design-document.md §5.4.
// ---------------------------------------------------------------------------
export type CoverageStatus = 'indexed' | 'partial' | 'not-indexed';

export interface CoverageRow {
  service: string;
  tier: number;
  tierLabel: string;
  lastIndexed: string | null;
  nextScheduled: string | null;
  status: CoverageStatus;
}

export const COVERAGE_MANIFEST: CoverageRow[] = [
  { service: 'payments-api', tier: 4, tierLabel: '4 · flow', lastIndexed: '2 days ago', nextScheduled: null, status: 'indexed' },
  { service: 'checkout-svc', tier: 1, tierLabel: '1 · file', lastIndexed: null, nextScheduled: 'tier-2, week 6', status: 'partial' },
  { service: 'ledger-svc', tier: 3, tierLabel: '3 · service', lastIndexed: '6 hours ago', nextScheduled: 'tier-4, week 9', status: 'partial' },
  { service: 'notification-svc', tier: 0, tierLabel: 'not indexed', lastIndexed: null, nextScheduled: 'tier-1, week 4', status: 'not-indexed' },
  { service: 'fraud-detection-svc', tier: 2, tierLabel: '2 · module', lastIndexed: '4 days ago', nextScheduled: 'tier-3, week 7', status: 'partial' },
  { service: 'user-profile-svc', tier: 4, tierLabel: '4 · flow', lastIndexed: '1 day ago', nextScheduled: null, status: 'indexed' },
];

export function coverageOverallStats() {
  const total = COVERAGE_MANIFEST.length;
  const atLeastTier1 = COVERAGE_MANIFEST.filter((r) => r.tier >= 1).length;
  const atTier4 = COVERAGE_MANIFEST.filter((r) => r.tier >= 4).length;
  return {
    pctTier1: Math.round((atLeastTier1 / total) * 100),
    pctTier4: Math.round((atTier4 / total) * 100),
    total,
  };
}

// Shared status wording — used verbatim by both the Coverage Explorer table
// and the Ask screen's refusal card so the two surfaces never contradict
// each other about the same service (§5.4).
export function coverageStatusLabel(row: CoverageRow): string {
  if (row.status === 'indexed') return `Indexed · tier ${row.tier}`;
  if (row.status === 'not-indexed') return `Not yet indexed · scheduled ${row.nextScheduled}`;
  return `Partial · tier ${row.tier} now · ${row.nextScheduled} scheduled`;
}

export function refusalTextForService(serviceName: string): string {
  const row = COVERAGE_MANIFEST.find((r) => r.service === serviceName);
  if (!row) return 'No reliable coverage for this yet.';
  return `No reliable coverage for this yet. This likely belongs to \`${row.service}\`. ${coverageStatusLabel(row)}.`;
}

// ---------------------------------------------------------------------------
// Personas (R6)
// ---------------------------------------------------------------------------
export type PersonaId = 'engineer' | 'po' | 'bo' | 'architect';

export interface Persona {
  id: PersonaId;
  label: string;
}

export const PERSONAS: Persona[] = [
  { id: 'engineer', label: 'Engineer' },
  { id: 'po', label: 'Product owner' },
  { id: 'bo', label: 'Business owner' },
  { id: 'architect', label: 'Architect' },
];

// ---------------------------------------------------------------------------
// Citations — shared object per id so every persona's answer can point at the
// same evidence while rendering it at different density.
// ---------------------------------------------------------------------------
export interface Citation {
  service: string;
  kind: 'code' | 'flow';
  file?: string;
  line?: number;
  flow?: string;
  commit: string;
  indexedAt: string;
  stale: boolean;
  needsReview: boolean;
}

export const CITATIONS: Record<number, Citation> = {
  1: { service: 'payments-api', kind: 'code', file: 'refund.py', line: 112, commit: 'a3f9c21', indexedAt: '2 days ago', stale: false, needsReview: false },
  2: { service: 'ledger-svc', kind: 'flow', flow: 'Refund propagation', commit: 'e88b104', indexedAt: '6 hours ago', stale: false, needsReview: true },
  3: { service: 'notification-svc', kind: 'code', file: 'notify_customer.py', line: 44, commit: '19cc402', indexedAt: '5 days ago', stale: true, needsReview: false },
};

export interface PersonaAnswer {
  question: string;
  citationIds: number[];
  markdown: string;
}

// The same question answered at four persona altitudes (§5.1).
export const NORMAL_ANSWERS: Record<PersonaId, PersonaAnswer> = {
  engineer: {
    question: 'How does a refund reversal propagate across services?',
    citationIds: [1, 2, 3],
    markdown: `A refund reversal is initiated by \`payments-api\` when a support agent calls
\`POST /refunds/{id}/reverse\` [1]. The handler validates the reversal window,
then publishes a \`RefundReversed\` event on the \`payments.events\` topic.

\`\`\`python
def reverse_refund(refund_id: str) -> None:
    refund = repo.get(refund_id)
    if not refund.within_reversal_window():
        raise ReversalWindowExpired(refund_id)
    publish("payments.events", RefundReversed(refund_id=refund.id))
\`\`\`

\`ledger-svc\` consumes the event and re-applies the original charge as
detailed in the **Refund propagation** flow narrative [2]. It then emits
\`LedgerAdjusted\`, which \`notification-svc\` consumes to message the
customer [3].

- \`payments-api\` — owns the reversal window check and event emission
- \`ledger-svc\` — owns the ledger adjustment (cross-service, see flow [2])
- \`notification-svc\` — owns the customer-facing message copy`,
  },

  po: {
    question: 'How does a refund reversal propagate across services?',
    citationIds: [1, 2, 3],
    markdown: `When a support agent reverses a refund, three capabilities pick up the work
in sequence:

- **Refund handling** in payments confirms the reversal is still inside the
  allowed window and starts the process [1].
- **Ledger adjustment** re-applies the original charge so the customer's
  balance is correct — this is the cross-service **Refund propagation**
  flow [2].
- **Customer notification** sends the confirmation message once the ledger
  update lands [3].

There's no manual handoff between teams here — the whole sequence is
event-driven and typically completes in under a second.`,
  },

  bo: {
    question: 'How does a refund reversal propagate across services?',
    citationIds: [1, 2, 3],
    markdown: `Reversing a refund is fully automated once a support agent initiates it.
The payment is un-refunded, the customer's account balance is corrected, and
the customer is notified — all within seconds, with no manual steps or
handoffs between teams involved [1].

This is one of the platform's reviewed cross-service flows, so the behavior
described here has been validated by an architect, not just inferred from
code [2].`,
  },

  architect: {
    question: 'How does a refund reversal propagate across services?',
    citationIds: [1, 2, 3],
    markdown: `## Flow summary

A refund reversal is initiated by \`payments-api\` [1] and propagates through
two downstream services via events — no synchronous calls, no shared
transaction.

\`\`\`python
def reverse_refund(refund_id: str) -> None:
    refund = repo.get(refund_id)
    if not refund.within_reversal_window():
        raise ReversalWindowExpired(refund_id)
    publish("payments.events", RefundReversed(refund_id=refund.id))
\`\`\`

## Ownership boundaries

- \`payments-api\` — reversal-window validation, event emission [1]
- \`ledger-svc\` — ledger adjustment, documented in the **Refund
  propagation** flow narrative [2]
- \`notification-svc\` — customer messaging [3]

## Review note

The Refund propagation flow narrative is currently flagged \`needs_review\`
[2] — a change landed in \`payments-api\` since it was last approved. The
narrative content above is still shown (previous approved version) but
should not be treated as re-validated until this is cleared in the Review
Queue.`,
  },
};

// ---------------------------------------------------------------------------
// Instant-answer scenario — cache hit / structural graph query, no gateway
// call (§4.2, §5.5).
// ---------------------------------------------------------------------------
export const INSTANT_ANSWER = {
  question: 'What depends on checkout-svc, three hops out?',
  mode: 'Structural result',
  markdown: `**11 services** depend on \`checkout-svc\` within 3 hops:

- Direct (1 hop): \`payments-api\`, \`inventory-svc\`, \`promo-svc\`
- 2 hops: \`ledger-svc\`, \`notification-svc\`, \`fraud-detection-svc\`, \`analytics-pipeline\`
- 3 hops: \`reporting-svc\`, \`data-warehouse-sync\`, \`audit-log-svc\`, \`customer-360\`

Answered directly from the graph — no model call.`,
};

// ---------------------------------------------------------------------------
// Refusal scenario — citation-resolution / groundedness failure (§4.2, R1).
// Wording generated from the SAME coverage manifest via refusalTextForService.
// ---------------------------------------------------------------------------
export const REFUSAL_SCENARIO = {
  question: 'What SLA does checkout-svc guarantee for payment confirmation?',
  serviceName: 'checkout-svc',
};

// ---------------------------------------------------------------------------
// Flow Narrative Review queue (Screen 3, Phase 7)
// ---------------------------------------------------------------------------
export type ReviewStatus = 'needs_review' | 'pending';

export interface ReviewItem {
  id: string;
  title: string;
  status: ReviewStatus;
  reason: string;
  contributingServices: string[];
  lastApproved: string | null;
  trigger: string;
  citationIds: number[];
  markdown: string;
}

export const REVIEW_QUEUE: ReviewItem[] = [
  {
    id: 'refund-propagation',
    title: 'Refund propagation',
    status: 'needs_review',
    reason: 'Code changed in payments-api since last approval',
    contributingServices: ['payments-api', 'ledger-svc', 'notification-svc'],
    lastApproved: '2026-05-02 by A. Chen',
    trigger: 'File change in refund.py',
    citationIds: [1, 2, 3],
    markdown: `A refund reversal begins when a support agent triggers it in \`payments-api\`
[1]. The event is consumed by \`ledger-svc\`, which re-applies the original
charge [2], and by \`notification-svc\`, which confirms the reversal to the
customer [3].`,
  },
  {
    id: 'onboarding-flow',
    title: 'Customer onboarding',
    status: 'pending',
    reason: 'Newly generated — never reviewed',
    contributingServices: ['signup-svc', 'kyc-svc', 'user-profile-svc'],
    lastApproved: null,
    trigger: 'Tier-4 generation run, 2026-07-18',
    citationIds: [],
    markdown: `A new customer signs up through \`signup-svc\`, which triggers identity
verification in \`kyc-svc\` before \`user-profile-svc\` activates the
account.`,
  },
  {
    id: 'checkout-abandonment',
    title: 'Checkout abandonment recovery',
    status: 'needs_review',
    reason: 'Code changed in promo-svc since last approval',
    contributingServices: ['checkout-svc', 'promo-svc', 'notification-svc'],
    lastApproved: '2026-04-11 by R. Okafor',
    trigger: 'File change in promo_eligibility.py',
    citationIds: [],
    markdown: `Abandoned checkouts are detected by \`checkout-svc\` after a 30-minute idle
window, which triggers a discount offer from \`promo-svc\` and a reminder
email via \`notification-svc\`.`,
  },
  {
    id: 'fraud-hold',
    title: 'Fraud hold escalation',
    status: 'pending',
    reason: 'Newly generated — never reviewed',
    contributingServices: ['fraud-detection-svc', 'payments-api', 'ledger-svc'],
    lastApproved: null,
    trigger: 'Tier-4 generation run, 2026-07-15',
    citationIds: [],
    markdown: `When \`fraud-detection-svc\` flags a transaction above the risk threshold,
\`payments-api\` places a hold and \`ledger-svc\` suspends settlement until
manual review clears it.`,
  },
];

// History rail (session-local mock — persistence is an open UI item, §5).
export const HISTORY_ITEMS = [
  'What depends on checkout-svc, three hops out?',
  'How does a refund reversal propagate?',
  'SLA for checkout-svc payment confirmation?',
];
