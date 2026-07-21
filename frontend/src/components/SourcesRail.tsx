import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { Citation, PersonaId } from '../lib/mock-data';
import { useCitationDrawer } from './citation';

/* Sources rail — same evidence, persona-dependent density (§5.1):
   - engineer / architect: full path:line, staleness + needs-review badges
   - product owner: referenced services / flows only
   - business owner: collapsed by default, expands to service names
   Reads the live citations map from the drawer context. */

function fullLabel(c: Citation): string {
  return c.kind === 'code' ? `${c.service}/${c.file}:${c.line}` : `${c.service} → flow: "${c.flow}"`;
}
function shortLabel(c: Citation): string {
  return c.kind === 'code' ? c.service : `${c.service} · flow`;
}

function Row({ id, persona }: { id: number; persona: PersonaId }) {
  const { openCitation, citations } = useCitationDrawer();
  const c = citations[id];
  if (!c) return null;
  const label = persona === 'po' ? shortLabel(c) : fullLabel(c);
  return (
    <div className="source-row" onClick={() => openCitation(id)}>
      <span className="num">[{id}]</span> {label}
      {(persona === 'engineer' || persona === 'architect') && (
        <>
          {c.stale && <span className="badge-mini badge-stale">may be stale</span>}
          {c.needsReview && <span className="badge-mini badge-review">needs review</span>}
          {persona === 'architect' && c.needsReview && (
            <Link
              to="/review"
              style={{ fontSize: '11.5px', fontWeight: 700, color: 'var(--accent)' }}
              onClick={(e) => e.stopPropagation()}
            >
              Review →
            </Link>
          )}
        </>
      )}
    </div>
  );
}

export function SourcesRail({ citationIds, persona }: { citationIds: number[]; persona: PersonaId }) {
  const [expanded, setExpanded] = useState(false);
  if (citationIds.length === 0) return null;

  if (persona === 'bo' && !expanded) {
    return (
      <div className="sources-rail">
        <button className="sources-collapsed-link" onClick={() => setExpanded(true)}>
          Sources ({citationIds.length}) ▾
        </button>
      </div>
    );
  }

  const label = persona === 'po' ? 'Referenced services / flows' : 'Sources';
  return (
    <div className="sources-rail">
      <div className="sources-rail-label">{label}</div>
      {citationIds.map((id) => (
        <Row key={id} id={id} persona={persona} />
      ))}
    </div>
  );
}
