# Vectural — UI Development Plan

**Status:** Draft for review — companion to `implementation-plan.md` and `design-document.md`
**Scope:** The production React + TypeScript frontend that replaces the static mockups in `ux-design/`. Section references in parentheses point to the implementation plan, e.g. `(§5.4)`.

The implementation plan fixes the frontend as *"React + TypeScript, SSE — persona switcher as first-class control"* (§3) and requires the whole stack to be OSI-open with **no BSL, SSPL, or source-available components** (R4). Those two facts, plus the visual language already established in `ux-design/`, constrain every choice below.

---

## 1. Design system: shadcn/ui

The frontend stack is fixed as React + TypeScript + SSE (§3), and R4 forbids any BSL, SSPL, or source-available component. The choice below was made by surveying the open-source field against two hard filters and then deciding — the survey (§1.1) is kept as the *rationale*, not an open question. **Decision: shadcn/ui** (§1.3).

### 1.1 The open-source field, surveyed against R4 + mockup fit

Every candidate was judged on two things only: its **licence** (must be OSI-open — a "free for now" tier that gates needed pieces behind a paid plan does not qualify) and its **fit with the existing `ux-design/` mockups** — a hand-crafted, data-dense, *non-Material* look built on CSS custom properties (`--accent`, `--amber`, `--border`, `--radius`…) in `ux-design/shared/style.css`.

| System | Type | Licence | Note for Vectural |
|---|---|---|---|
| **shadcn/ui** | Own-the-source (Radix **or** Base UI + Tailwind) | MIT | Copy-in components you own; runs on either primitive layer as of 2025; Tailwind tokens map onto the mockups' CSS variables |
| Base UI | Headless primitives | MIT | v1.0 stable Dec 2025, MUI-maintained, render-prop API — the freshest primitive layer |
| Radix UI | Headless primitives | MIT | ~131M wk downloads; battle-tested, but cadence slowed after the WorkOS acquisition |
| Ark UI | Headless primitives | MIT | XState state machines; cross-framework (React/Vue/Solid) — machinery we don't need on a React-only project |
| Headless UI | Headless primitives | MIT | Tailwind team; ~10 components — too thin for a review-queue / data-grid app |
| Mantine | Batteries-included styled | MIT | 120+ components, strong TS DX, built-in hooks; ships styled, so it needs re-theming to escape its own look |
| MUI (Material UI) | Batteries-included styled | MIT (core) | Mature, but Material aesthetic fights the mockups and some dashboard pieces sit behind a paid Pro tier |
| Ant Design | Batteries-included styled | MIT | Best-in-class data tables, but strongly opinionated enterprise-Material aesthetic; large surface |
| Chakra UI | Batteries-included styled | MIT | Good DX, but its styling engine overlaps and competes with Tailwind |

**R4 tripwire.** Two failure modes disqualify a system regardless of how good it looks: (a) a licence that is BSL / SSPL / source-available, and (b) a nominally "free" kit whose admin/dashboard components — exactly the pieces this app needs — are gated behind a commercial "Pro" tier (MUI, Untitled UI, Tailwind UI). This mirrors the backend's §3 rejection list: a "free for now" licence is not an OSI-open licence.

### 1.2 Decision — why shadcn/ui wins for Vectural

shadcn/ui is not a runtime dependency; it is a set of accessible, unstyled primitives with Tailwind styling that you **copy into the repo and own**. Three reasons that property fits Vectural specifically:

- **Licence hygiene is structural, not reviewed (R4).** shadcn/ui, its primitives, and Tailwind are all MIT, and because the component source lives in our tree rather than in `node_modules`, there is no transitive runtime-dependency licence surface to re-audit on every upgrade — the same "own the rebuildable artifact" posture the backend takes with Neo4j/OpenSearch as caches.
- **It matches the mockups we already have.** The `ux-design/` CSS custom properties map one-to-one onto Tailwind design tokens, so shadcn preserves the established clean, non-Material look; MUI/Ant would fight it.
- **Accessibility comes from the primitive layer, where it is hardest to get right.** The persona switcher (already a dropdown, commit `7b99b8d`), the review approve/reject dialogs (§Phase 7), and citation tooltips (§5.4) all need correct focus management, ARIA roles, and keyboard handling. The primitives ship these; hand-rolling them is where internal tools usually regress.

**One sub-choice remains, and it is swappable:** the primitive layer under shadcn. **Default to Base UI** (v1.0 stable, MUI-maintained, MIT) for its active maintenance and clean API; **Radix** is the drop-in alternative since shadcn supports both, and is worth reaching for only if a specific component exists on Radix but not yet on Base UI. This decision does not ripple outward — component call sites are shadcn's, not the primitive's.

### 1.3 Supporting libraries (all MIT/Apache)

| Concern | Library | Licence | Why |
|---|---|---|---|
| Data grid (Coverage Explorer) | TanStack Table | MIT | Headless — styles with our own Tailwind/shadcn cells; sorting/filtering for the tier×service matrix (§5.4) |
| Streaming answer render | react-markdown + remark-gfm | MIT | Renders the persona answer markdown incrementally as SSE tokens arrive |
| Code block highlighting | Shiki | MIT | Accurate multi-language highlighting for cited code chunks; matches the estate's language spread |
| Server state / caching | TanStack Query | MIT | Coverage manifest, history, review queue — cache + revalidate; pairs with SSE for the answer stream |
| Client routing | React Router | MIT | Ask / Coverage / Review are already three routes in the mockups |
| Icons | Lucide | ISC (OSI) | shadcn's default icon set |
| Build / dev server | Vite | MIT | Fast TS + React toolchain |

SSE itself is the native `EventSource` / `fetch`-stream — no library needed, matching §3 and the deployment view's `FE --SSE--> APIPOD` edge (design-doc §6).

---

## 2. Frontend architecture

```
src/
  app/            # routes: /ask, /coverage, /review — mirrors ux-design/*.html
  components/
    ui/           # shadcn primitives (owned source: button, dialog, dropdown, badge, tooltip…)
    ask/          # QuestionBox, AnswerCard, CitationChip, StalenessBadge, RefusalCard
    coverage/     # CoverageTable, TierMatrix, ScheduleBadge
    review/       # ReviewQueue, NarrativeDiff, ApproveRejectBar
  lib/
    api/          # typed FastAPI client (Pydantic-mirrored types)
    sse/          # answer-stream reader (EventSource wrapper)
    persona/      # persona context — the one first-class global control (R6)
  styles/         # tailwind.css + tokens.css (ported from ux-design/shared/style.css :root)
```

**Load-bearing UI decisions, each tied to a backend contract:**

- **Persona is global context, not per-request state (R6, §5.6).** The switcher sets a `Persona` in React context; every question submission threads it to the answer endpoint, exactly as the routing layer threads it to synthesis. One control, one source of truth.
- **Coverage is read from the manifest, never inferred client-side (§5.4).** The coverage chip in the topbar, the Coverage Explorer, *and* the refusal card all read the same `coverage_manifest` response. The design doc calls out these three surfaces drifting into contradiction as the risk to design against — so they share one query key, not three fetches.
- **Refusal is a first-class render state, not an error toast (§5.4, R1).** The answer component has four terminal states — `streaming`, `answer+citations`, `instant` (cache/structural), `refusal` — already enumerated by the mockup's scenario bar. Refusal names likely owning services; it must look like a considered answer, because fail-closed is a feature, not a failure.
- **Citations are interactive and verifiable (§5.4, R1).** Each citation chip resolves to a specific retrieved chunk (`chunk_id` → path + lines). Clicking opens the cited code. An answer whose citations don't resolve is never rendered as an answer — the UI has no code path that shows an unverifiable claim as fact.
- **Staleness is always visible (§5.9, R3).** When serving runs on a previous version during reindex, the answer carries a staleness badge; flow narratives marked `needs_review` surface a distinct flag rather than silently updating.

---

## 3. Phased delivery (aligned to the backend phases)

The UI does not get its own timeline — it is pulled forward by the backend phases that give it something real to show. Each UI phase's exit criterion is *"the mockup for this screen is now backed by a live endpoint."*

### UI-0 — Foundation (during backend Phase 1–2)
- Vite + React + TS + Tailwind scaffold; port `ux-design/shared/style.css` `:root` tokens into `tokens.css`
- Install/own the shadcn primitives actually used by the three mockups (button, dropdown-menu, dialog, badge, tooltip, table, card)
- Persona context + switcher wired as a real control (behaviour already prototyped)
- Typed API client skeleton mirroring the FastAPI/Pydantic models
- **Exit:** the three mockup screens re-rendered as React routes, still on mock data, visually matching `ux-design/`.

### UI-1 — Ask, retrieval-only (backend Phase 2, "thin slice: engineer persona")
- Question box → hybrid-retrieval endpoint → ranked chunks (no LLM synthesis yet, per §Phase 2)
- Answer card in "ranked results" mode; code-chunk rendering with Shiki
- Engineer persona only
- **Exit:** the Ask screen returns live ranked chunks for the engineer persona; retrieval quality is what the eval harness (§7) is measuring.

### UI-2 — Coverage Explorer (backend Phase 5, "coverage_manifest exposed from day one")
- TanStack Table over the live `coverage_manifest`: service × tier status, scheduled week
- Topbar coverage chip and Ask-screen refusal card both bind to the same query
- **Exit:** coverage is visible and accurate; the three coverage surfaces provably read one source (§5.4).

### UI-3 — Full Ask with synthesis, all personas (backend Phase 6)
- SSE streaming of persona answers via `EventSource`; incremental markdown render
- Four persona templates selectable; answer altitude changes with persona (R6)
- Citation chips resolving to chunks; **refusal render state** wired to the fail-closed path (R1)
- Instant-answer path for cache/structural hits (§5.5) — visually distinct "no gateway call" tag
- **Exit:** end-to-end cited answers stream for every persona; refusal fires correctly for out-of-coverage questions.

### UI-4 — Flow Narrative Review, architect-gated (backend Phase 7)
- Review queue for Tier-4 narratives; approve / request-changes / reject (shadcn dialog, Base UI primitive)
- `needs_review` items surfaced from the freshness pipeline (§5.9)
- Narrative diff view for re-review after code change
- **Exit:** an architect can gate a narrative to authoritative from the UI; nothing serves as authoritative before approval.

### UI-5 — Hardening & observability surface (backend Phase 8)
- Staleness indicators wired to live reindex state (R3)
- Optional internal ops view over the SigNoz metrics already emitted (§7.1): token spend by persona, refusal rate, cache-hit rate, coverage % — read-only, for the pilot team
- Accessibility pass (keyboard, ARIA, contrast), responsive breakpoints, empty/loading/error states across all screens
- **Exit:** pilot-ready; every screen has defined loading/empty/error/refusal states and passes a keyboard-only walkthrough.

---

## 4. What this plan deliberately does not do

- **No design-system spike.** The choice is made — shadcn/ui (§1); a bake-off would burn time the survey and the mockups already spent deciding the look.
- **No client-side business logic that duplicates a backend gate.** Citation resolution and groundedness are backend contracts (§5.4). The UI *renders* their outcome and never re-implements or second-guesses them — a client-side "looks grounded enough" check would reintroduce exactly the R1 risk the backend closes.
- **No state store beyond context + TanStack Query.** Server state is cached by Query; the only genuinely global client state is the current persona. Adding Redux/Zustand here would be architecture ahead of need.

---

## 5. Open UI items for review

1. **Design system — decided: shadcn/ui.** Primitive layer defaults to Base UI; revisit only if a specific component is needed that exists on Radix but not yet on Base UI. Tracked here so the resolved decision and its one live sub-detail stay visible.
2. **Dark mode** — the tokens are already CSS variables, so a dark theme is cheap; confirm whether the pilot wants it in UI-5 or deferred.
3. **Ops/observability view ownership** — UI-5 assumes a thin read-only metrics screen. If SigNoz's own dashboards suffice for the pilot, drop it and link out instead.
4. **Answer history persistence** — the mockup shows a history rail; confirm whether history is per-user server-side state (a new `PostgreSQL` need, cf. design-doc §3.3) or session-local only for v1.
