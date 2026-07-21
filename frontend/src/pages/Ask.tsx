import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { AppShell, COVERAGE_PCT } from '../components/AppShell';
import { AnswerMarkdown } from '../components/AnswerMarkdown';
import { SourcesRail } from '../components/SourcesRail';
import { PersonaSelect } from '../components/PersonaSelect';
import { usePersona } from '../lib/persona';
import {
  INSTANT_ANSWER,
  NORMAL_ANSWERS,
  REFUSAL_SCENARIO,
  refusalTextForService,
} from '../lib/mock-data';

type Scenario = 'normal' | 'streaming' | 'instant' | 'refusal';

const SCENARIOS: { id: Scenario; label: string }[] = [
  { id: 'normal', label: 'Normal answer' },
  { id: 'streaming', label: 'Simulate streaming' },
  { id: 'instant', label: 'Instant answer (cache/structural)' },
  { id: 'refusal', label: 'Refusal (no coverage)' },
];

function headingsOf(markdown: string): string[] {
  return [...markdown.matchAll(/^## (.*)$/gm)].map((m) => m[1]);
}

/* Ask — ports ux-design/ask.html. Four terminal render states enumerated by
   the scenario bar (streaming / answer+citations / instant / refusal), driven
   by the current persona. Refusal is a first-class state, not an error (R1). */
export function Ask() {
  const { personaId, persona } = usePersona();
  const [scenario, setScenario] = useState<Scenario>('normal');
  const [priorCollapsed, setPriorCollapsed] = useState(true);

  // Streaming simulation — reveal the raw markdown line by line, then swap to
  // the fully parsed answer + sources once complete.
  const [streamText, setStreamText] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [runNonce, setRunNonce] = useState(0);

  useEffect(() => {
    if (scenario !== 'streaming') {
      setStreaming(false);
      return;
    }
    const lines = NORMAL_ANSWERS[personaId].markdown.split('\n');
    let i = 0;
    let shown = '';
    setStreaming(true);
    setStreamText('');
    const timer = setInterval(() => {
      shown += (i > 0 ? '\n' : '') + lines[i];
      i += 1;
      setStreamText(shown);
      if (i >= lines.length) {
        clearInterval(timer);
        setStreaming(false);
      }
    }, 90);
    return () => clearInterval(timer);
  }, [scenario, personaId, runNonce]);

  const data = NORMAL_ANSWERS[personaId];

  return (
    <AppShell
      title="Ask"
      activeNav="ask"
      topbarRight={
        <>
          <Link className="coverage-chip" to="/coverage">
            <span className="dot" /> Coverage: {COVERAGE_PCT}%
          </Link>
          <span className="persona-pill">{persona.label}</span>
        </>
      }
    >
      <div className="main-scroll">
        <div className="main-inner">
          <div className="scenario-bar">
            {SCENARIOS.map((s) => (
              <button
                key={s.id}
                className={`scenario-btn ${scenario === s.id ? 'active' : ''}`}
                onClick={() => setScenario(s.id)}
              >
                {s.label}
              </button>
            ))}
          </div>

          {/* prior turn — collapsed instant-answer example */}
          <div className={`turn ${priorCollapsed ? 'collapsed' : ''}`}>
            <div className="question-bubble">
              <span className="who">You</span>What depends on checkout-svc, three hops out?
            </div>
            <div className="answer-card">
              <span className="answer-mode-tag">● Structural result</span>
              <AnswerMarkdown markdown={INSTANT_ANSWER.markdown} />
            </div>
            {priorCollapsed && (
              <button className="show-full-btn" onClick={() => setPriorCollapsed(false)}>
                Show full answer ↓
              </button>
            )}
          </div>

          {/* live turn */}
          <div className="turn">
            {scenario === 'refusal' ? (
              <>
                <div className="question-bubble">
                  <span className="who">You</span>
                  {REFUSAL_SCENARIO.question}
                </div>
                <div className="refusal-card">
                  <div className="refusal-kicker">Not answered · citation could not be resolved</div>
                  <p>{refusalTextForService(REFUSAL_SCENARIO.serviceName)}</p>
                  <Link
                    className="coverage-link"
                    to={`/coverage?service=${REFUSAL_SCENARIO.serviceName}`}
                  >
                    View coverage schedule for {REFUSAL_SCENARIO.serviceName} →
                  </Link>
                </div>
              </>
            ) : scenario === 'instant' ? (
              <>
                <div className="question-bubble">
                  <span className="who">You</span>
                  {INSTANT_ANSWER.question}
                </div>
                <div className="answer-card">
                  <span className="answer-mode-tag">● {INSTANT_ANSWER.mode} · no gateway call</span>
                  <AnswerMarkdown markdown={INSTANT_ANSWER.markdown} />
                </div>
              </>
            ) : (
              <>
                <div className="question-bubble">
                  <span className="who">You</span>
                  {data.question}
                </div>
                <div className="answer-card">
                  <span className={`answer-mode-tag ${streaming ? 'streaming' : ''}`}>
                    {streaming ? '● streaming…' : '● Synthesized answer'}
                  </span>

                  {!streaming && headingsOf(data.markdown).length > 2 && (
                    <div className="answer-outline">
                      <span className="outline-label">In this answer</span>
                      {headingsOf(data.markdown).map((h) => (
                        <a href="#" key={h}>
                          {h}
                        </a>
                      ))}
                    </div>
                  )}

                  {streaming ? (
                    <div className="answer-body">
                      <p className="stream-raw">
                        {streamText}
                        <span className="stream-cursor" />
                      </p>
                    </div>
                  ) : (
                    <>
                      <AnswerMarkdown markdown={data.markdown} />
                      <SourcesRail citationIds={data.citationIds} persona={personaId} />
                    </>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      <div className="composer">
        <div className="composer-inner">
          <PersonaSelect />
          <input
            type="text"
            defaultValue="How does a refund reversal propagate across services?"
            placeholder="Ask about the codebase..."
          />
          <button className="composer-send" onClick={() => setRunNonce((n) => n + 1)}>
            Send
          </button>
        </div>
      </div>
    </AppShell>
  );
}
