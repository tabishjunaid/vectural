import { useCallback, useEffect, useRef, useState } from 'react';
import { AppShell } from '../components/AppShell';
import { useModel } from '../lib/model';
import {
  addRepo,
  cancelRepo,
  dropRepo,
  estimateRepo,
  indexRepo,
  ingestEvents,
  listRepos,
  pauseRepo,
  resumeRepo,
  summariseRepo,
  type IngestEstimate,
  type IngestJob,
  type RepoRow,
} from '../lib/api';

/* Ingestion playground: add a repo by URL, estimate its cost, then index it
   (free/local) and summarise it (pick a model — a local Ollama model runs
   on-device for $0). Watch live progress; pause, cancel, or drop. Reuses the
   coverage-table / status-pill / analytics-tile vocabulary. */

const TIER = ['Not indexed', 'Files', 'Modules', 'Service'];
const DONE = new Set(['done', 'failed', 'cancelled', 'idle']);

function money(n: number | null): string {
  if (n == null) return '—';
  if (n === 0) return '$0.00';
  return n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(3)}`;
}

function Tile({ num, label }: { num: number | string; label: string }) {
  return (
    <div className="analytics-tile">
      <div className="num">{typeof num === 'number' ? num.toLocaleString() : num}</div>
      <div className="label">{label}</div>
    </div>
  );
}

function Progress({ job }: { job: IngestJob }) {
  const pct = job.files_total ? Math.round((job.files_done / job.files_total) * 100) : 0;
  return (
    <div className="progress-wrap">
      <div className="progress-bar">
        <div className="progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="progress-text">
        {job.paused ? 'paused' : job.phase} · {job.files_done}/{job.files_total}
        {job.tokens ? ` · ${job.tokens.toLocaleString()} tok` : ''}
      </span>
    </div>
  );
}

export function Ingest() {
  const { models } = useModel();
  const [repos, setRepos] = useState<RepoRow[]>([]);
  const [jobs, setJobs] = useState<Record<string, IngestJob>>({});
  const [estimates, setEstimates] = useState<Record<string, IngestEstimate>>({});
  const [url, setUrl] = useState('');
  const [model, setModel] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const subs = useRef<Record<string, () => void>>({});

  const summModel = model || models[0]?.id || '';

  const refresh = useCallback(() => {
    listRepos()
      .then(setRepos)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => refresh(), [refresh]);
  useEffect(() => () => Object.values(subs.current).forEach((fn) => fn()), []);

  const watch = (service: string) => {
    subs.current[service]?.();
    subs.current[service] = ingestEvents(service, (job) => {
      setJobs((prev) => ({ ...prev, [service]: job }));
      if (DONE.has(job.phase)) {
        subs.current[service]?.();
        delete subs.current[service];
        refresh();
      }
    });
  };

  const guard = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const onAdd = () =>
    guard(async () => {
      if (!url.trim()) return;
      setBusy(true);
      try {
        await addRepo(url.trim());
        setUrl('');
        refresh();
      } finally {
        setBusy(false);
      }
    });

  const onEstimate = (s: string) =>
    guard(async () => {
      const est = await estimateRepo(s, summModel || undefined);
      setEstimates((prev) => ({ ...prev, [s]: est }));
    });

  const onIndex = (s: string) =>
    guard(async () => {
      const job = await indexRepo(s);
      setJobs((p) => ({ ...p, [s]: job }));
      watch(s);
    });

  const onSummarise = (s: string) =>
    guard(async () => {
      const job = await summariseRepo(s, summModel || undefined);
      setJobs((p) => ({ ...p, [s]: job }));
      watch(s);
    });

  const onDrop = (s: string) =>
    guard(async () => {
      if (!window.confirm(`Drop the index for "${s}"? The cloned source is kept.`)) return;
      await dropRepo(s);
      setEstimates((prev) => {
        const next = { ...prev };
        delete next[s];
        return next;
      });
      refresh();
    });

  return (
    <AppShell title="Indexing" activeNav="ingest">
      <div className="main-scroll">
        <div className="main-inner">
          <p className="compare-intro">
            Add a repo by Git URL (cloned) — or add a local project by folder name once its
            folder is in the estate directory. Estimate its cost, then index it (free · local)
            and summarise it — pick a model; a local Ollama model runs on-device for $0. Watch
            live progress; pause, cancel, or drop.
          </p>

          <div className="ingest-addbar">
            <input
              type="text"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="Git URL to clone, or a local folder name already in the estate"
              onKeyDown={(e) => e.key === 'Enter' && onAdd()}
            />
            <button className="composer-send" onClick={onAdd} disabled={busy}>
              {busy ? 'Adding…' : 'Add repo'}
            </button>
            <label className="compare-picker">
              <span>Summariser</span>
              <select value={summModel} onChange={(e) => setModel(e.target.value)}>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label} · {m.hint}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {error && <p className="ingest-error">{error}</p>}

          <div className="panel">
            <table className="coverage-table">
              <thead>
                <tr>
                  <th>Repo</th>
                  <th>State</th>
                  <th>Coverage</th>
                  <th>Chunks</th>
                  <th>Progress</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {repos.map((r) => {
                  const job = jobs[r.service];
                  const running = !!job && !DONE.has(job.phase);
                  return (
                    <tr key={r.service}>
                      <td>
                        <strong>{r.service}</strong>
                        {r.gitUrl && <div className="ingest-url">{r.gitUrl}</div>}
                      </td>
                      <td>
                        <span className={`status-pill ${r.indexed ? 'indexed' : 'not-indexed'}`}>
                          {r.phase !== 'idle' ? r.phase : r.indexed ? 'Indexed' : 'Not indexed'}
                        </span>
                      </td>
                      <td>{TIER[r.summaryTier] ?? `tier ${r.summaryTier}`}</td>
                      <td>{r.chunks.toLocaleString()}</td>
                      <td>
                        {running ? (
                          <Progress job={job} />
                        ) : job?.phase === 'done' ? (
                          <span className="ingest-doneline">
                            ✓ {job.tokens ? `${job.tokens.toLocaleString()} tok · ${money(job.cost_usd)}` : 'done'}
                          </span>
                        ) : (
                          '—'
                        )}
                      </td>
                      <td className="ingest-actions">
                        <button onClick={() => onEstimate(r.service)}>Estimate</button>
                        <button onClick={() => onIndex(r.service)} disabled={running}>
                          Index
                        </button>
                        <button
                          onClick={() => onSummarise(r.service)}
                          disabled={running || !r.indexed}
                        >
                          Summarise
                        </button>
                        {running &&
                          (job.paused ? (
                            <button onClick={() => guard(() => resumeRepo(r.service))}>Resume</button>
                          ) : (
                            <button onClick={() => guard(() => pauseRepo(r.service))}>Pause</button>
                          ))}
                        {running && (
                          <button onClick={() => guard(() => cancelRepo(r.service))}>Cancel</button>
                        )}
                        <button
                          className="ingest-danger"
                          onClick={() => onDrop(r.service)}
                          disabled={running}
                        >
                          Drop
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {Object.entries(estimates).map(([svc, est]) => (
            <div key={svc} className="panel ingest-estimate">
              <div className="sources-rail-label">
                Estimate · {svc} · {est.model ?? 'default model'}
              </div>
              <div className="analytics-tiles">
                <Tile num={est.totals.files} label="Files" />
                <Tile num={est.totals.chunks} label="Chunks" />
                <Tile num={est.totals.embed_tokens} label="Embed tokens · local · $0" />
                <Tile num={est.totals.gateway_tokens} label="Summarise tokens" />
                <Tile num={money(est.cost_usd)} label="Est. cost" />
              </div>
              <span
                className={`badge-mini ${
                  est.totals.fits_monthly_indexing_budget ? 'badge-current' : 'badge-stale'
                }`}
              >
                {est.totals.fits_monthly_indexing_budget ? 'fits monthly budget' : 'over monthly budget'}
              </span>
            </div>
          ))}
        </div>
      </div>
    </AppShell>
  );
}
