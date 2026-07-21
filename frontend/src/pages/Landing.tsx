import { Link } from 'react-router-dom';

/* Landing — ports ux-design/index.html. Entry point listing the three screens. */
export function Landing() {
  return (
    <div className="landing">
      <h1>Vectural — Codebase Intelligence</h1>
      <p className="lead">
        Persona-aware retrieval over the engineering estate. UI-0 foundation: React + TypeScript on
        shadcn/ui (Base UI primitives), running on mock data. See <code>plan/ui-development-plan.md</code>.
      </p>

      <Link className="screen-card" to="/ask">
        <span className="tag">Screen 1 · every persona</span>
        <h3>Ask</h3>
        <p>
          Persona-aware Q&amp;A: streaming markdown answers, citation chips, staleness badges, refusal
          states, and needs-review flags. Try the persona switcher and scenario toolbar.
        </p>
      </Link>

      <Link className="screen-card" to="/coverage">
        <span className="tag">Screen 2 · Phase 5</span>
        <h3>Coverage Explorer</h3>
        <p>
          Which services are indexed, at what tier, and when the rest are scheduled — the same source of
          truth the Ask screen's refusal card reads from.
        </p>
      </Link>

      <Link className="screen-card" to="/review">
        <span className="tag">Screen 3 · Phase 7 · architect-gated</span>
        <h3>Flow Narrative Review</h3>
        <p>
          Architect approve / request changes / reject queue for Tier-4 cross-service flow narratives,
          including needs-review items triggered by code changes.
        </p>
      </Link>

      <p className="mock-note">UI-0 — mock data, no backend. Endpoints are wired in UI-1 onward.</p>
    </div>
  );
}
