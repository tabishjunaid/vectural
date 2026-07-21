/* Codebase Intelligence Platform — UX mockup data + tiny markdown renderer.
   Hardcoded mock data only. No backend, no build step, no external deps. */

// ---------------------------------------------------------------------------
// Coverage manifest — single source of truth reused by coverage.html AND the
// refusal card on ask.html, per the design-document.md §5.4 contract.
// ---------------------------------------------------------------------------
const COVERAGE_MANIFEST = [
  { service: "payments-api",        tier: 4, tierLabel: "4 · flow",    lastIndexed: "2 days ago",  nextScheduled: null,               status: "indexed" },
  { service: "checkout-svc",        tier: 1, tierLabel: "1 · file",    lastIndexed: null,          nextScheduled: "tier-2, week 6",   status: "partial" },
  { service: "ledger-svc",          tier: 3, tierLabel: "3 · service", lastIndexed: "6 hours ago", nextScheduled: "tier-4, week 9",   status: "partial" },
  { service: "notification-svc",    tier: 0, tierLabel: "not indexed", lastIndexed: null,          nextScheduled: "tier-1, week 4",   status: "not-indexed" },
  { service: "fraud-detection-svc", tier: 2, tierLabel: "2 · module",  lastIndexed: "4 days ago",  nextScheduled: "tier-3, week 7",   status: "partial" },
  { service: "user-profile-svc",    tier: 4, tierLabel: "4 · flow",    lastIndexed: "1 day ago",   nextScheduled: null,               status: "indexed" },
];

function coverageOverallStats() {
  const total = COVERAGE_MANIFEST.length;
  const atLeastTier1 = COVERAGE_MANIFEST.filter((r) => r.tier >= 1).length;
  const atTier4 = COVERAGE_MANIFEST.filter((r) => r.tier >= 4).length;
  return {
    pctTier1: Math.round((atLeastTier1 / total) * 100),
    pctTier4: Math.round((atTier4 / total) * 100),
  };
}

// Shared status wording — used verbatim by both the Coverage Explorer table
// and the Ask screen's refusal card, so the two surfaces never contradict
// each other about the same service.
function coverageStatusLabel(row) {
  if (row.status === "indexed") return `Indexed · tier ${row.tier}`;
  if (row.status === "not-indexed") return `Not yet indexed · scheduled ${row.nextScheduled}`;
  return `Partial · tier ${row.tier} now · ${row.nextScheduled} scheduled`;
}

function refusalTextForService(serviceName) {
  const row = COVERAGE_MANIFEST.find((r) => r.service === serviceName);
  if (!row) return "No reliable coverage for this yet.";
  return `No reliable coverage for this yet. This likely belongs to \`${row.service}\`. ${coverageStatusLabel(row)}.`;
}

// ---------------------------------------------------------------------------
// Personas
// ---------------------------------------------------------------------------
const PERSONAS = [
  { id: "engineer", label: "Engineer" },
  { id: "po", label: "Product owner" },
  { id: "bo", label: "Business owner" },
  { id: "architect", label: "Architect" },
];

// ---------------------------------------------------------------------------
// Citations referenced by the "normal answer" scenario. Shared object per id
// so every persona's answer can point at the same underlying evidence while
// rendering it at different density.
// ---------------------------------------------------------------------------
const CITATIONS = {
  1: { service: "payments-api", kind: "code", file: "refund.py", line: 112, commit: "a3f9c21", indexedAt: "2 days ago", stale: false, needsReview: false },
  2: { service: "ledger-svc", kind: "flow", flow: "Refund propagation", commit: "e88b104", indexedAt: "6 hours ago", stale: false, needsReview: true },
  3: { service: "notification-svc", kind: "code", file: "notify_customer.py", line: 44, commit: "19cc402", indexedAt: "5 days ago", stale: true, needsReview: false },
};

// ---------------------------------------------------------------------------
// The same question, answered at four persona altitudes (design-document.md
// §5.1 — persona threaded through the synthesis template).
// ---------------------------------------------------------------------------
const NORMAL_ANSWERS = {
  engineer: {
    question: "How does a refund reversal propagate across services?",
    citationIds: [1, 2, 3],
    markdown:
`A refund reversal is initiated by \`payments-api\` when a support agent calls
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
    question: "How does a refund reversal propagate across services?",
    citationIds: [1, 2, 3],
    markdown:
`When a support agent reverses a refund, three capabilities pick up the work
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
    question: "How does a refund reversal propagate across services?",
    citationIds: [1, 2, 3],
    markdown:
`Reversing a refund is fully automated once a support agent initiates it.
The payment is un-refunded, the customer's account balance is corrected, and
the customer is notified — all within seconds, with no manual steps or
handoffs between teams involved [1].

This is one of the platform's reviewed cross-service flows, so the behavior
described here has been validated by an architect, not just inferred from
code [2].`,
  },

  architect: {
    question: "How does a refund reversal propagate across services?",
    citationIds: [1, 2, 3],
    markdown:
`## Flow summary

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
// call (design-document.md §4.2).
// ---------------------------------------------------------------------------
const INSTANT_ANSWER = {
  question: "What depends on checkout-svc, three hops out?",
  mode: "Structural result",
  markdown:
`**11 services** depend on \`checkout-svc\` within 3 hops:

- Direct (1 hop): \`payments-api\`, \`inventory-svc\`, \`promo-svc\`
- 2 hops: \`ledger-svc\`, \`notification-svc\`, \`fraud-detection-svc\`, \`analytics-pipeline\`
- 3 hops: \`reporting-svc\`, \`data-warehouse-sync\`, \`audit-log-svc\`, \`customer-360\`

Answered directly from the graph — no model call.`,
};

// ---------------------------------------------------------------------------
// Refusal scenario — citation-resolution or groundedness failure
// (design-document.md §4.2). Wording is generated from the SAME coverage
// manifest data as coverage.html, via refusalTextForService() above.
// ---------------------------------------------------------------------------
const REFUSAL_SCENARIO = {
  question: "What SLA does checkout-svc guarantee for payment confirmation?",
  serviceName: "checkout-svc",
};

// ---------------------------------------------------------------------------
// Flow Narrative Review queue (Screen 3, Phase 7)
// ---------------------------------------------------------------------------
const REVIEW_QUEUE = [
  {
    id: "refund-propagation",
    title: "Refund propagation",
    status: "needs_review",
    reason: "Code changed in payments-api since last approval",
    contributingServices: ["payments-api", "ledger-svc", "notification-svc"],
    lastApproved: "2026-05-02 by A. Chen",
    trigger: "File change in refund.py",
    citationIds: [1, 2, 3],
    markdown:
`A refund reversal begins when a support agent triggers it in \`payments-api\`
[1]. The event is consumed by \`ledger-svc\`, which re-applies the original
charge [2], and by \`notification-svc\`, which confirms the reversal to the
customer [3].`,
  },
  {
    id: "onboarding-flow",
    title: "Customer onboarding",
    status: "pending",
    reason: "Newly generated — never reviewed",
    contributingServices: ["signup-svc", "kyc-svc", "user-profile-svc"],
    lastApproved: null,
    trigger: "Tier-4 generation run, 2026-07-18",
    citationIds: [],
    markdown:
`A new customer signs up through \`signup-svc\`, which triggers identity
verification in \`kyc-svc\` before \`user-profile-svc\` activates the
account.`,
  },
  {
    id: "checkout-abandonment",
    title: "Checkout abandonment recovery",
    status: "needs_review",
    reason: "Code changed in promo-svc since last approval",
    contributingServices: ["checkout-svc", "promo-svc", "notification-svc"],
    lastApproved: "2026-04-11 by R. Okafor",
    trigger: "File change in promo_eligibility.py",
    citationIds: [],
    markdown:
`Abandoned checkouts are detected by \`checkout-svc\` after a 30-minute idle
window, which triggers a discount offer from \`promo-svc\` and a reminder
email via \`notification-svc\`.`,
  },
  {
    id: "fraud-hold",
    title: "Fraud hold escalation",
    status: "pending",
    reason: "Newly generated — never reviewed",
    contributingServices: ["fraud-detection-svc", "payments-api", "ledger-svc"],
    lastApproved: null,
    trigger: "Tier-4 generation run, 2026-07-15",
    citationIds: [],
    markdown:
`When \`fraud-detection-svc\` flags a transaction above the risk threshold,
\`payments-api\` places a hold and \`ledger-svc\` suspends settlement until
manual review clears it.`,
  },
];

// ---------------------------------------------------------------------------
// Tiny markdown → HTML renderer. Good enough for these mock answers only —
// NOT the eventual production renderer (that's react-markdown + remark-gfm,
// see the plan). Code fences are extracted first so citation-marker
// replacement never touches code content (e.g. `arr[0]`).
// ---------------------------------------------------------------------------
function renderMarkdown(md) {
  const codeBlocks = [];
  let html = md.replace(/```(\w+)?\n([\s\S]*?)```/g, (_, lang, code) => {
    const idx = codeBlocks.length;
    codeBlocks.push({ lang: lang || "text", code });
    return `@@CODEBLOCK${idx}@@`;
  });

  html = html
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  html = html.replace(/^### (.*)$/gm, "<h4>$1</h4>");
  html = html.replace(/^## (.*)$/gm, "<h3>$1</h3>");
  // [\s\S] instead of "." so bold survives a source line-wrap (e.g. "**Refund\n  propagation**").
  html = html.replace(/\*\*([\s\S]+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");

  // List blocks: a line starting "- " begins an item; an indented
  // continuation line (soft-wrapped source text) joins the previous item
  // rather than starting a new one or falling out of the list.
  html = html.replace(/(^|\n)((?:- .*|  +\S.*))((?:\n(?:- .*|  +\S.*))*)/g, (_, pre, first, rest) => {
    const lines = (first + rest).split("\n");
    const items = [];
    lines.forEach((line) => {
      if (/^- /.test(line)) items.push(line.replace(/^- /, ""));
      else items[items.length - 1] += " " + line.trim();
    });
    return pre + "<ul>" + items.map((i) => `<li>${i}</li>`).join("") + "</ul>";
  });

  html = html
    .split(/\n{2,}/)
    .map((block) => {
      const t = block.trim();
      if (!t) return "";
      if (/^<(h3|h4|ul)/.test(t)) return t;
      if (/^@@CODEBLOCK\d+@@$/.test(t)) return t;
      // Soft-wrap: join source line breaks within a paragraph into spaces
      // so rendering reflows naturally instead of hard-breaking at whatever
      // column the mock markdown happened to be authored at.
      return "<p>" + t.replace(/\s*\n\s*/g, " ") + "</p>";
    })
    .join("\n");

  // Citation markers -> interactive chip. Runs only on non-code text.
  html = html.replace(
    /\[(\d+)\]/g,
    '<button class="citation-chip" data-cite="$1" onclick="openCitation(this)">$1</button>'
  );

  html = html.replace(/@@CODEBLOCK(\d+)@@/g, (_, i) => {
    const block = codeBlocks[Number(i)];
    const escaped = block.code
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    return `<div class="code-block"><div class="code-block-head"><span>${block.lang}</span><button class="copy-btn" onclick="copyCode(this)">Copy</button></div><pre><code>${escaped}</code></pre></div>`;
  });

  return html;
}

function copyCode(btn) {
  const code = btn.closest(".code-block").querySelector("code").innerText;
  navigator.clipboard?.writeText(code);
  const original = btn.innerText;
  btn.innerText = "Copied";
  setTimeout(() => (btn.innerText = original), 1200);
}
