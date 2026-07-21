/* Vectural API client — talks to the FastAPI backend (backend/api/app.py).
   Replaces the mock data: the shapes here are the backend's JSON, adapted into
   the render shapes the existing components already consume (numeric [n]
   citations, ReviewItem, CoverageRow). Base path is /api, proxied to the
   backend in dev (vite) and prod (nginx). */

import type { Citation, CoverageRow, PersonaId, ReviewItem } from './mock-data';

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
  // Rewrite each [chunk_id] marker to its 1-based display index so the existing
  // CitationChip / SourcesRail rendering (which is numeric) works unchanged.
  let markdown = a.text;
  const citations: Record<number, Citation> = {};
  for (const c of a.citations) {
    markdown = markdown.split(`[${c.chunk_id}]`).join(`[${c.index}]`);
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
  };
}

// ---- public API -----------------------------------------------------------

export function ask(question: string, persona: PersonaId): Promise<LiveAnswer> {
  return post<BackendAnswer>('/ask', { question, persona }).then(adaptAnswer);
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
