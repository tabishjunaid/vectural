import type { LiveAnalytics } from '../lib/api';

/* Per-query analytics shown under every answer (and refusal — tokens are spent
   there too). Exact token usage from the backend, plus latency, model, and a
   per-LLM-call breakdown. Reuses the coverage stat-tile / status-pill / drawer
   meta-row vocabulary so it reads as native. Renders nothing if the backend
   didn't send analytics (older build). */

function fmt(n: number): string {
  return n.toLocaleString();
}

const TASK_LABEL: Record<string, string> = {
  entity_linking: 'Entity linking',
  cypher_generation: 'Cypher',
  synthesis: 'Synthesis',
  groundedness: 'Groundedness',
};

export function AnalyticsPanel({ analytics }: { analytics: LiveAnalytics | null }) {
  if (!analytics) return null;
  const a = analytics;

  const tiles: { num: string; label: string }[] = [
    { num: fmt(a.totalTokens), label: 'Total tokens' },
    { num: fmt(a.inputTokens), label: 'Input' },
    { num: fmt(a.outputTokens), label: 'Output' },
    { num: String(a.llmCalls), label: 'LLM calls' },
    { num: `${(a.latencyMs / 1000).toFixed(1)}s`, label: 'Latency' },
  ];
  if (a.costUsd != null) {
    tiles.push({ num: `$${a.costUsd < 0.01 ? a.costUsd.toFixed(4) : a.costUsd.toFixed(3)}`, label: 'Cost (est.)' });
  }

  return (
    <div className="analytics-panel">
      <div className="sources-rail-label">Query analytics</div>

      <div className="analytics-tiles">
        {tiles.map((t) => (
          <div className="analytics-tile" key={t.label}>
            <div className="num">{t.num}</div>
            <div className="label">{t.label}</div>
          </div>
        ))}
      </div>

      <div className="analytics-pills">
        {a.fromCache && <span className="status-pill indexed">Served from cache · 0 tokens</span>}
        {a.model && <span className="badge-mini badge-current">{a.model}</span>}
        <span className="badge-mini badge-neutral">{a.depth}</span>
        {a.complexity && <span className="badge-mini badge-neutral">{a.complexity}</span>}
        <span className="badge-mini badge-neutral">{a.persona}</span>
        <span className="badge-mini badge-neutral">{a.mode}</span>
        <span className="badge-mini badge-neutral">{a.evidenceChunks} chunks</span>
        <span className="badge-mini badge-neutral">{a.citations} cited</span>
      </div>

      {a.calls.length > 0 && (
        <div className="analytics-breakdown">
          {a.calls.map((c, i) => (
            <div className="drawer-meta-row" key={`${c.task}-${i}`}>
              <span className="k">
                {TASK_LABEL[c.task] ?? c.task} · <span className="analytics-model">{c.model}</span>
              </span>
              <span>
                {fmt(c.inputTokens)} in / {fmt(c.outputTokens)} out
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
