import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { AppShell } from '../components/AppShell';
import { coverageStatusLabel, type CoverageRow } from '../lib/mock-data';
import { getCoverage } from '../lib/api';

function statusClass(status: CoverageRow['status']) {
  return status === 'indexed' ? 'indexed' : status === 'partial' ? 'partial' : 'not-indexed';
}

function stats(rows: CoverageRow[]) {
  const total = rows.length || 1;
  return {
    pctTier1: Math.round((rows.filter((r) => r.tier >= 1).length / total) * 100),
    pctTier4: Math.round((rows.filter((r) => r.tier >= 4).length / total) * 100),
    total: rows.length,
  };
}

/* Coverage Explorer — live from GET /coverage, the same coverage source the Ask
   refusal card names (§5.4). Deep link ?service=<name> highlights a row. */
export function Coverage() {
  const [params] = useSearchParams();
  const highlightService = params.get('service');
  const [rows, setRows] = useState<CoverageRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCoverage()
      .then(setRows)
      .catch((e) => setError(e instanceof Error ? e.message : 'failed to load coverage'));
  }, []);

  const s = stats(rows);

  return (
    <AppShell
      title="Coverage Explorer"
      activeNav="coverage"
      topbarRight={<span className="persona-pill">All personas</span>}
    >
      <div className="main-scroll">
        <div className="main-inner">
          <p style={{ color: 'var(--text-dim)', fontSize: 13, marginTop: 0 }}>
            Which services are indexed, at what tier, and when the rest are scheduled. This is the same{' '}
            <code>coverage_manifest</code> data the Ask screen's refusal card reads from — approving a
            flow narrative in Review bumps a service to tier 4 here, live.
          </p>

          {error && <p style={{ color: 'var(--amber)' }}>Could not load coverage: {error}</p>}

          <div className="panel" style={{ overflow: 'hidden' }}>
            <table className="coverage-table">
              <thead>
                <tr>
                  <th>Service</th>
                  <th>Tier reached</th>
                  <th>Last indexed</th>
                  <th>Next scheduled</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.service} className={row.service === highlightService ? 'highlight' : ''}>
                    <td>
                      <strong>{row.service}</strong>
                    </td>
                    <td>{row.tierLabel}</td>
                    <td>{row.lastIndexed ?? '—'}</td>
                    <td>{row.nextScheduled ?? '—'}</td>
                    <td>
                      <span className={`status-pill ${statusClass(row.status)}`}>
                        {coverageStatusLabel(row)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="overall-stats">
            <div className="overall-stat">
              <div className="num">{s.pctTier1}%</div>
              <div className="label">services at tier ≥ 1</div>
            </div>
            <div className="overall-stat">
              <div className="num">{s.pctTier4}%</div>
              <div className="label">services at tier 4 (flow)</div>
            </div>
            <div className="overall-stat">
              <div className="num">{s.total}</div>
              <div className="label">services in manifest</div>
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
