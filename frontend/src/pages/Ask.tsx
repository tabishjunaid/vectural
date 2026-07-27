import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { AppShell, COVERAGE_PCT } from '../components/AppShell';
import { AnswerSections } from '../components/AnswerSections';
import { SourcesRail } from '../components/SourcesRail';
import { PersonaSelect } from '../components/PersonaSelect';
import { useCitationDrawer } from '../components/citation';
import { usePersona } from '../lib/persona';
import { useDepth } from '../lib/depth';
import { useModel } from '../lib/model';
import { DepthSelect } from '../components/DepthSelect';
import { ModelSelect } from '../components/ModelSelect';
import { askStream, type AnswerStageEvent, type LiveAnswer } from '../lib/api';

const EXAMPLES = [
  'How does the gateway charge via payments and ledger?',
  'What does payments call?',
  'How are payment events consumed?',
];

/* The pipeline stages, in order, with the label shown before each runs. Keys
   match the backend AnswerStage.stage values (backend/answer/service.py). */
const STAGE_ORDER = ['cache', 'plan', 'retrieve', 'context', 'synthesize', 'cite', 'ground'] as const;
const STAGE_LABEL: Record<string, string> = {
  cache: 'Checking cache',
  plan: 'Planning scope',
  retrieve: 'Searching evidence',
  context: 'Gathering architecture',
  synthesize: 'Drafting answer',
  cite: 'Resolving citations',
  ground: 'Verifying claims',
};
// Terminal statuses (anything but 'start') mean the stage finished.
const isDone = (status: string) => status !== 'start';
const isFail = (status: string) => status === 'fail' || status === 'empty';


/* Grounded questions to explore next. Every suggestion is keyed on a relationship
   the graph actually holds, so clicking one always lands on real evidence rather
   than a refusal. Reuses the example-chip styling. */
function FollowUps({ questions, onAsk }: { questions: string[]; onAsk: (q: string) => void }) {
  if (!questions.length) return null;
  return (
    <div className="follow-ups">
      <div className="follow-ups-label">Explore further</div>
      <div className="scenario-bar">
        {questions.map((q) => (
          <button key={q} className="scenario-btn" onClick={() => onAsk(q)}>
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}

/* Ask — live wired to POST /ask. Renders the backend's terminal states
   (synthesized / instant / refusal). Refusal is a first-class state, not an
   error (R1). Persona is threaded into every request (R6). */
export function Ask() {
  const { personaId, persona } = usePersona();
  const { depthId } = useDepth();
  const { modelId } = useModel();
  const { setCitations } = useCitationDrawer();
  const [question, setQuestion] = useState('');
  // Whether the user has asked at least once. Until then the page is a quiet
  // empty state — nothing is submitted on load (or on a hard refresh).
  const [asked, setAsked] = useState(false);
  const [answer, setAnswer] = useState<LiveAnswer | null>(null);
  const [stages, setStages] = useState<AnswerStageEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const run = useCallback(
    async (q: string) => {
      const trimmed = q.trim();
      if (!trimmed) return;
      setAsked(true);
      setQuestion(trimmed);
      setLoading(true);
      setError(null);
      setAnswer(null);
      setStages([]);
      try {
        const result = await askStream(
          trimmed,
          personaId,
          (event) => {
          // Keep the latest event per stage, in pipeline order, so the checklist
          // shows one row per step (start → done) rather than a growing log.
          setStages((prev) => {
            const next = prev.filter((s) => s.stage !== event.stage);
            next.push(event);
            next.sort((a, b) => STAGE_ORDER.indexOf(a.stage as never) - STAGE_ORDER.indexOf(b.stage as never));
            return next;
            });
          },
          depthId,
          modelId ?? undefined,
        );
        setAnswer(result);
        setCitations(result.citations);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'request failed');
      } finally {
        setLoading(false);
      }
    },
    [personaId, depthId, modelId, setCitations],
  );

  // Re-ask when the persona changes (altitude changes, R6) — but ONLY once the
  // user has actually asked something. No auto-submit on first load / hard refresh.
  useEffect(() => {
    if (asked && question.trim()) void run(question);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [personaId]);

  const askFollowUp = (q: string) => {
    setQuestion(q);
    if (inputRef.current) inputRef.current.value = q;
    void run(q);
  };

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

          {!asked && (
            <div className="ask-empty">
              Ask anything about the codebase — pick an example above or type your
              own question below to get a cited, verifiable answer.
            </div>
          )}

          {asked && (
          <div className="turn">
            <div className="question-bubble">
              <span className="who">You</span>
              {question}
            </div>

            {loading && (
              <div className="answer-card">
                <span className="answer-mode-tag streaming">● Working…</span>
                <ol className="stage-list">
                  {stages.map((s) => (
                    <li
                      key={s.stage}
                      className={`stage-row ${isDone(s.status) ? 'done' : 'active'} ${
                        isFail(s.status) ? 'failed' : ''
                      }`}
                    >
                      <span className="stage-icon" aria-hidden>
                        {isFail(s.status) ? '✕' : isDone(s.status) ? '✓' : '●'}
                      </span>
                      <span className="stage-text">
                        <span className="stage-label">{STAGE_LABEL[s.stage] ?? s.stage}</span>
                        {s.detail && <span className="stage-detail">{s.detail}</span>}
                      </span>
                    </li>
                  ))}
                </ol>
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
                <FollowUps questions={answer.followUps} onAsk={askFollowUp} />
              </div>
            )}

            {!loading && !error && answer && answer.mode !== 'refusal' && (
              <div className="answer-card">
                <span className="answer-mode-tag">
                  ● {answer.mode === 'instant' ? 'Instant · no gateway call' : 'Synthesized answer'}
                  {answer.stale && ' · may be stale'}
                </span>
                <AnswerSections markdown={answer.markdown} />
                <SourcesRail citationIds={answer.citationIds} persona={personaId} />
                <FollowUps questions={answer.followUps} onAsk={askFollowUp} />
              </div>
            )}
          </div>
          )}
        </div>
      </div>

      <div className="composer">
        <div className="composer-inner">
          <PersonaSelect />
          <DepthSelect />
          <ModelSelect />
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
