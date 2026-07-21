import type { ReactNode } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { HISTORY_ITEMS } from '../lib/mock-data';
import { usePersona } from '../lib/persona';

export const COVERAGE_PCT = 78; // headline coverage figure shown in rail + topbar chip

type NavKey = 'ask' | 'coverage' | 'review';

interface AppShellProps {
  title: string;
  activeNav: NavKey;
  topbarRight?: ReactNode;
  children: ReactNode;
}

/* App shell — left rail + topbar, shared by every screen (mirrors the
   .app-shell markup repeated across the three mockups). */
export function AppShell({ title, activeNav, topbarRight, children }: AppShellProps) {
  const navigate = useNavigate();
  const { personaId } = usePersona();
  // Review is architect-gated: the nav entry appears for the architect persona
  // (or when already on the Review screen), matching the mockup's behaviour.
  const showReview = personaId === 'architect' || activeNav === 'review';

  return (
    <div className="app-shell">
      <nav className="rail">
        <div className="rail-brand">Codebase Intelligence</div>
        <button className="rail-new" onClick={() => navigate('/ask')}>
          + New ask
        </button>

        <div className="rail-section-label">History</div>
        {HISTORY_ITEMS.map((h) => (
          <div className="rail-history-item" key={h}>
            {h}
          </div>
        ))}

        <div className="rail-nav">
          <NavLink to="/ask" className={activeNav === 'ask' ? 'active' : undefined}>
            Ask
          </NavLink>
          <NavLink to="/coverage" className={activeNav === 'coverage' ? 'active' : undefined}>
            Coverage <span className="badge">{COVERAGE_PCT}%</span>
          </NavLink>
          {showReview && (
            <NavLink to="/review" className={activeNav === 'review' ? 'active' : undefined}>
              Review <span className="badge">4</span>
            </NavLink>
          )}
        </div>
      </nav>

      <div className="main-col">
        <header className="topbar">
          <div className="topbar-title">{title}</div>
          <div className="topbar-right">{topbarRight}</div>
        </header>
        {children}
      </div>
    </div>
  );
}
