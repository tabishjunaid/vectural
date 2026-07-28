import { useMemo, useRef, useState } from 'react';
import { AppShell } from '../components/AppShell';
import { AnswerSections } from '../components/AnswerSections';
import { AnalyticsPanel } from '../components/AnalyticsPanel';
import { PersonaSelect } from '../components/PersonaSelect';
import { DepthSelect } from '../components/DepthSelect';
import { usePersona } from '../lib/persona';
import { useDepth } from '../lib/depth';
import { useModel } from '../lib/model';
import { askStream, type LiveAnswer, type ModelOption } from '../lib/api';

/* Side-by-side model comparison. Runs the SAME question through two chosen models
   at once (both whole-pipeline — see backend override threading) and shows the two
   answers with their analytics next to each other. This is where "Ollama (local,
   $0) vs OpenAI" becomes a direct, measurable comparison: same evidence, same
   depth/persona, two models — diff the latency, cost, tokens, and the answers. */

interface RunState {
  loading: boolean;
  answer: LiveAnswer | null;
  error: string | null;
}

const EMPTY: RunState = { loading: false, answer: null, error: null };

function providerOf(models: ModelOption[], id: string | null): string {
  return models.find((m) => m.id === id)?.provider ?? '';
}

/* A single comparison column: model header + answer (or loading/error) + analytics. */
function Column({ label, provider, state }: { label: string; provider: string; state: RunState }) {
  return (
    <div className="compare-col">
      <div className="compare-col-head">
        <span className="compare-col-model">{label || '—'}</span>
        {provider && <span className="badge-mini badge-neutral">{provider}</span>}
      </div>
      {state.loading && <div className="compare-col-status">● Running…</div>}
      {!state.loading && state.error && (
        <div className="refusal-card">
          <div className="refusal-kicker">Run failed</div>
          <p>{state.error}</p>
        </div>
      )}
      {!state.loading && !state.error && state.answer && (
        <>
          {state.answer.mode === 'refusal' ? (
            <div className="refusal-card">
              <div className="refusal-kicker">Not answered · {state.answer.reason}</div>
              <p>{state.answer.markdown}</p>
            </div>
          ) : (
            <AnswerSections markdown={state.answer.markdown} />
          )}
          <AnalyticsPanel analytics={state.answer.analytics} />
        </>
      )}
    </div>
  );
}

export function Compare() {
  const { personaId } = usePersona();
  const { depthId } = useDepth();
  const { models } = useModel();
  const inputRef = useRef<HTMLInputElement>(null);
  const [question, setQuestion] = useState('');

  // Default A/B to the first two distinct models offered (e.g. gpt-4o + a local one).
  const [modelA, setModelA] = useState<string>('');
  const [modelB, setModelB] = useState<string>('');
  const a = modelA || models[0]?.id || '';
  const b = modelB || models[1]?.id || models[0]?.id || '';

  const [runA, setRunA] = useState<RunState>(EMPTY);
  const [runB, setRunB] = useState<RunState>(EMPTY);
  const [busy, setBusy] = useState(false);

  const compare = async () => {
    const q = (inputRef.current?.value ?? question).trim();
    if (!q || busy) return;
    setQuestion(q);
    setBusy(true);
    setRunA({ ...EMPTY, loading: true });
    setRunB({ ...EMPTY, loading: true });
    // Run both concurrently; a failure in one (e.g. Ollama down) must not sink the
    // other, so each settles independently into its own column.
    const one = (model: string, set: (s: RunState) => void) =>
      askStream(q, personaId, () => {}, depthId, model)
        .then((answer) => set({ loading: false, answer, error: null }))
        .catch((e) => set({ loading: false, answer: null, error: e instanceof Error ? e.message : 'request failed' }));
    await Promise.allSettled([one(a, setRunA), one(b, setRunB)]);
    setBusy(false);
  };

  // A compact diff strip once both runs have analytics.
  const diff = useMemo(() => {
    const an = runA.answer?.analytics;
    const bn = runB.answer?.analytics;
    if (!an || !bn) return null;
    const fmt = (n: number) => n.toLocaleString();
    const money = (n: number | null) => (n == null ? '—' : `$${n < 0.01 ? n.toFixed(4) : n.toFixed(3)}`);
    return [
      { label: 'Latency', a: `${(an.latencyMs / 1000).toFixed(1)}s`, b: `${(bn.latencyMs / 1000).toFixed(1)}s` },
      { label: 'Total tokens', a: fmt(an.totalTokens), b: fmt(bn.totalTokens) },
      { label: 'Cost (est.)', a: money(an.costUsd), b: money(bn.costUsd) },
    ];
  }, [runA.answer, runB.answer]);

  const Picker = ({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: string }) => (
    <label className="compare-picker">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {models.map((m) => (
          <option key={m.id} value={m.id}>
            {m.label} · {m.hint}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <AppShell title="Compare" activeNav="compare">
      <div className="main-scroll">
        <div className="main-inner">
          <p className="compare-intro">
            Run one question through two models at once — same evidence, same depth and
            persona. Compare a local Ollama model (zero cost, nothing leaves the machine)
            against OpenAI, side by side.
          </p>

          {diff && (
            <div className="compare-summary">
              {diff.map((d) => (
                <div className="compare-summary-row" key={d.label}>
                  <span className="k">{d.label}</span>
                  <span className="va">{d.a}</span>
                  <span className="vb">{d.b}</span>
                </div>
              ))}
            </div>
          )}

          <div className="compare-grid">
            <Column label={models.find((m) => m.id === a)?.label ?? a} provider={providerOf(models, a)} state={runA} />
            <Column label={models.find((m) => m.id === b)?.label ?? b} provider={providerOf(models, b)} state={runB} />
          </div>
        </div>
      </div>

      <div className="composer">
        <div className="composer-inner compare-composer">
          <PersonaSelect />
          <DepthSelect />
          <Picker label="A" value={a} onChange={setModelA} />
          <Picker label="B" value={b} onChange={setModelB} />
          <input
            ref={inputRef}
            type="text"
            defaultValue={question}
            placeholder="Ask a question to compare across models…"
            onKeyDown={(e) => e.key === 'Enter' && compare()}
          />
          <button className="composer-send" onClick={compare} disabled={busy}>
            {busy ? 'Running…' : 'Compare'}
          </button>
        </div>
      </div>
    </AppShell>
  );
}
