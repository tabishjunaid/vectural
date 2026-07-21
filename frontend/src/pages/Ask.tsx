import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { AppShell, COVERAGE_PCT } from '../components/AppShell';
import { AnswerMarkdown } from '../components/AnswerMarkdown';
import { SourcesRail } from '../components/SourcesRail';
import { PersonaSelect } from '../components/PersonaSelect';
import { useCitationDrawer } from '../components/citation';
import { usePersona } from '../lib/persona';
import { ask, type LiveAnswer } from '../lib/api';

const EXAMPLES = [
  'How does the gateway charge via payments and ledger?',
  'What does payments call?',
  'How are payment events consumed?',
];

/* Ask — live wired to POST /ask. Renders the backend's terminal states
   (synthesized / instant / refusal). Refusal is a first-class state, not an
   error (R1). Persona is threaded into every request (R6). */
export function Ask() {
  const { personaId, persona } = usePersona();
  const { setCitations } = useCitationDrawer();
  const [question, setQuestion] = useState(EXAMPLES[0]);
  const [answer, setAnswer] = useState<LiveAnswer | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const run = useCallback(
    async (q: string) => {
      const trimmed = q.trim();
      if (!trimmed) return;
      setLoading(true);
      setError(null);
      try {
        const result = await ask(trimmed, personaId);
        setAnswer(result);
        setCitations(result.citations);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'request failed');
      } finally {
        setLoading(false);
      }
    },
    [personaId, setCitations],
  );

  // Ask on first load and whenever the persona changes (altitude changes, R6).
  useEffect(() => {
    void run(question);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [personaId]);

  const send = () => {
    const q = inputRef.current?.value ?? question;
    setQuestion(q);
    void run(q);
  };

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
            {EXAMPLES.map((ex) => (
              <button
                key={ex}
                className={`scenario-btn ${question === ex ? 'active' : ''}`}
                onClick={() => {
                  setQuestion(ex);
                  if (inputRef.current) inputRef.current.value = ex;
                  void run(ex);
                }}
              >
                {ex}
              </button>
            ))}
          </div>

          <div className="turn">
            <div className="question-bubble">
              <span className="who">You</span>
              {question}
            </div>

            {loading && (
              <div className="answer-card">
                <span className="answer-mode-tag streaming">● thinking…</span>
              </div>
            )}

            {!loading && error && (
              <div className="refusal-card">
                <div className="refusal-kicker">Request failed</div>
                <p>{error}. Is the backend running on the configured API base?</p>
              </div>
            )}

            {!loading && !error && answer && answer.mode === 'refusal' && (
              <div className="refusal-card">
                <div className="refusal-kicker">Not answered · {answer.reason}</div>
                <p>{answer.markdown}</p>
                {answer.likelyServices[0] && (
                  <Link className="coverage-link" to={`/coverage?service=${answer.likelyServices[0]}`}>
                    View coverage schedule for {answer.likelyServices[0]} →
                  </Link>
                )}
              </div>
            )}

            {!loading && !error && answer && answer.mode !== 'refusal' && (
              <div className="answer-card">
                <span className="answer-mode-tag">
                  ● {answer.mode === 'instant' ? 'Instant · no gateway call' : 'Synthesized answer'}
                  {answer.stale && ' · may be stale'}
                </span>
                <AnswerMarkdown markdown={answer.markdown} />
                <SourcesRail citationIds={answer.citationIds} persona={personaId} />
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="composer">
        <div className="composer-inner">
          <PersonaSelect />
          <input
            ref={inputRef}
            type="text"
            defaultValue={question}
            placeholder="Ask about the codebase..."
            onKeyDown={(e) => e.key === 'Enter' && send()}
          />
          <button className="composer-send" onClick={send}>
            Send
          </button>
        </div>
      </div>
    </AppShell>
  );
}
