/* Vectural API client — talks to the FastAPI backend (backend/api/app.py).
   Replaces the mock data: the shapes here are the backend's JSON, adapted into
   the render shapes the existing components already consume (numeric [n]
   citations, ReviewItem, CoverageRow). Base path is /api, proxied to the
   backend in dev (vite) and prod (nginx). */

import type { Citation, CoverageRow, PersonaId, ReviewItem } from './mock-data';
import type { DepthId } from './depth';

const BASE = import.meta.env.VITE_API_BASE ?? '/api';

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`POST ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

// ---- backend JSON shapes --------------------------------------------------

interface BackendCitation {
  index: number;
  chunk_id: string;
  marker: string; // the text the model actually wrote between the brackets
  service: string;
  path: string;
  span: { start: number; end: number };
}

interface BackendAnswer {
  mode: 'synthesized' | 'instant' | 'refusal';
  persona: PersonaId;
  question: string;
  text: string;
  citations: BackendCitation[];
  reason: string | null;
  likely_services: string[];
  from_cache: boolean;
  stale: boolean;
  follow_ups: string[];
}

interface BackendFlow {
  id: string;
  title: string;
  services: string[];
  trigger: string;
  text: string;
  status: 'pending' | 'needs_review' | 'approved' | 'changes_requested' | 'rejected';
  review_reason: string | null;
  last_approved_by: string | null;
  last_approved_at: string | null;
}

// ---- rendered shapes the UI consumes --------------------------------------

export interface LiveAnswer {
  mode: 'synthesized' | 'instant' | 'refusal';
  question: string;
  persona: PersonaId;
  markdown: string; // citations rewritten to [1], [2], …
  citations: Record<number, Citation>;
  citationIds: number[];
  reason: string | null;
  likelyServices: string[];
  fromCache: boolean;
  stale: boolean;
  followUps: string[]; // grounded questions to explore next
}

function basename(path: string): string {
  const i = path.lastIndexOf('/');
  return i >= 0 ? path.slice(i + 1) : path;
}

function toCitation(c: BackendCitation, stale: boolean): Citation {
  const isFlow = c.chunk_id.startsWith('flow:');
  return {
    service: c.service,
    kind: isFlow ? 'flow' : 'code',
    file: isFlow ? undefined : basename(c.path),
    line: isFlow ? undefined : c.span.start,
    flow: isFlow ? c.chunk_id.replace(/^flow:/, '') : undefined,
    commit: c.chunk_id.split(':').pop() ?? '—',
    indexedAt: 'recently',
    stale,
    needsReview: false, // only approved (authoritative) flows are ever served
  };
}

function adaptAnswer(a: BackendAnswer): LiveAnswer {
  // Rewrite each citation marker to its 1-based display index so the existing
  // CitationChip / SourcesRail rendering (which matches [n]) works unchanged.
  //
  // Replace `marker` — the text the model actually wrote — not just `chunk_id`.
  // Models abbreviate the long service:path:lines:hash id down to its trailing
  // hash, so matching on chunk_id alone silently found nothing and left the
  // citation as dead text with no chip and no way to drill down.
  let markdown = a.text;
  const citations: Record<number, Citation> = {};
  for (const c of a.citations) {
    for (const form of [c.marker, c.chunk_id]) {
      if (form) markdown = markdown.split(`[${form}]`).join(`[${c.index}]`);
    }
    citations[c.index] = toCitation(c, a.stale);
  }
  return {
    mode: a.mode,
    question: a.question,
    persona: a.persona,
    markdown,
    citations,
    citationIds: a.citations.map((c) => c.index),
    reason: a.reason,
    likelyServices: a.likely_services,
    fromCache: a.from_cache,
    stale: a.stale,
    followUps: a.follow_ups ?? [],
  };
}

// ---- public API -----------------------------------------------------------

/* An answer-model choice for the composer dropdown (GET /models). Populated from
   the backend so it only ever lists models whose provider gateway is wired. */
export interface ModelOption {
  id: string;
  label: string;
  provider: string;
  hint: string;
}

export function getModels(): Promise<ModelOption[]> {
  return get<ModelOption[]>('/models');
}

export function ask(
  question: string,
  persona: PersonaId,
  depth: DepthId = 'standard',
  model?: string,
): Promise<LiveAnswer> {
  return post<BackendAnswer>('/ask', { question, persona, depth, model }).then(adaptAnswer);
}

/* One progress event on the answer path (backend AnswerStage). `status` is
   'start' before a step and a terminal marker after ('ok'/'hit'/'miss'/'empty'/
   'fail'); `detail` is a human sentence to show the user. */
export interface AnswerStageEvent {
  stage: string;
  status: string;
  detail: string;
}

/* Streaming ask: POST /ask/stream returns Server-Sent Events. `onStage` fires as
   each pipeline step runs (so the UI can show what's happening live), and the
   promise resolves with the final answer. EventSource can't POST, so we read the
   response body and parse SSE frames ourselves. */
export async function askStream(
  question: string,
  persona: PersonaId,
  onStage: (event: AnswerStageEvent) => void,
  depth: DepthId = 'standard',
  model?: string,
): Promise<LiveAnswer> {
  const res = await fetch(`${BASE}/ask/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, persona, depth, model }),
  });
  if (!res.ok || !res.body) throw new Error(`POST /ask/stream → ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let answer: LiveAnswer | null = null;

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line.
    let sep: number;
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const { event, data } = parseFrame(frame);
      if (!data) continue;
      if (event === 'stage') onStage(JSON.parse(data) as AnswerStageEvent);
      else if (event === 'done') answer = adaptAnswer(JSON.parse(data) as BackendAnswer);
    }
  }

  if (!answer) throw new Error('stream ended without an answer');
  return answer;
}

function parseFrame(frame: string): { event: string; data: string } {
  let event = 'message';
  const dataLines: string[] = [];
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
  }
  return { event, data: dataLines.join('\n') };
}

export function getCoverage(): Promise<CoverageRow[]> {
  return get<CoverageRow[]>('/coverage');
}

function toReviewItem(f: BackendFlow): ReviewItem {
  return {
    id: f.id,
    title: f.title,
    status: f.status === 'needs_review' ? 'needs_review' : 'pending',
    reason:
      f.review_reason ??
      (f.status === 'needs_review' ? 'Code changed since last approval' : 'Newly generated — never reviewed'),
    contributingServices: f.services,
    lastApproved: f.last_approved_by ? `${f.last_approved_at ?? ''} by ${f.last_approved_by}` : null,
    trigger: f.trigger,
    citationIds: [],
    markdown: f.text,
  };
}

export function getReviewQueue(): Promise<ReviewItem[]> {
  return get<BackendFlow[]>('/review/queue').then((flows) => flows.map(toReviewItem));
}

export type ReviewVerb = 'approve' | 'request-changes' | 'reject';

export function reviewAction(
  id: string,
  verb: ReviewVerb,
  architect = 'A. Architect',
  reason?: string,
): Promise<BackendFlow> {
  return post<BackendFlow>(`/review/${id}/${verb}`, { architect, persona: 'architect', reason });
}
