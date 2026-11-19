# Assistant page: AI Operations Manager layout

Date: 2026-11-20 · Page: `/assistant` (`apps/web/src/app/(app)/assistant/page.tsx`)

The Assistant page used to be voice-first only. It is now an operations dashboard built around Mira. Her
3D character, animations, speech recognition, speech synthesis, barge-in, poster design on her screen,
the live agent graph and full screen are unchanged: the conversation code was not modified. The new parts
were added around it.

## What changed

| Area | Before | Now |
|---|---|---|
| Layout | One centred column (max 5xl) | Header + two columns on desktop: Mira and the composer on the left, an operations panel on the right (sticky). On phones the order is Mira → composer → operations → suggestions → quick actions, so approvals are not buried. |
| Command input | Single-line input | `CommandComposer`: multiline, grows to 10 lines, Enter sends, Shift+Enter adds a line, Enter ignored while a Thai/other IME is composing, character count, visible label. |
| Quick actions | – | Six cards for workflows that exist today: promo campaign (images + captions), social captions, event poster, short video, quotation, campaign brief. A card fills the composer (you add what it is for, Send, then confirm). The poster card opens the free design on Mira's screen. No card starts a paid job. |
| Quote-to-Cash | – | Quotation is a real quick action (the Document agent makes it). Invoices, payment tracking and customer follow-up are shown as **Planned** labels, not buttons or links. |
| Job summary | Only spoken in Mira's welcome | Operations panel: awaiting approval / in progress / did not finish (each links to the filtered job list), budget bar, offline workers, and the newest jobs awaiting approval, each with a direct **Review** link to `/jobs/{id}`. |
| Suggestions | – | "Suggested next": approvals first, then failures, offline workers, budget ≥ 80 %, then a next job based on recent output (captions for images, a video from a campaign, a quotation). A job is never suggested when its agents are offline. |
| First-time users | – | A clear "Start here" label over the NEW CHAT button, and a three-step guide when the organisation has never run a job. |
| Thai text | 12 px labels, uppercase/tracked labels | On this page Thai text gets line-height 1.7 and a 13 px minimum. Uppercase and letter-spacing are applied only in English. The page sets `lang` so the browser breaks Thai lines correctly. |
| Jobs page | Filter only from the dropdown | `/jobs?status=awaiting_approval` (any real status) opens pre-filtered, so the panel's links land on the right list. |

## Where things live

- `src/lib/workflows.ts`: the workflow catalogue (`WORKFLOWS`), the planned Quote-to-Cash steps (`PLANNED`),
  `offlineFor`, `isFirstRun`, `suggestTasks`. Every workflow maps to a deliverable that the planner supports
  (`apps/api/app/agents/planner.py`); a test checks the command wording against the planner's keywords.
- `src/components/assistant/CommandComposer.tsx`
- `src/components/assistant/QuickActions.tsx` (with `PlannedSteps`)
- `src/components/assistant/OpsPanel.tsx`: `OpsSummary`, `TaskSuggestions`, `StartGuide`
- Strings: `composer.*`, `qa.*`, `planned.*`, `ops.*`, `suggest.*`, `start.*` and `assistant.subtitle` /
  `startHere` / `startHint` in `src/i18n/dictionaries.ts` (Thai and English).
- Thai readability rule: `.assistant-page:lang(th)` in `src/app/globals.css`.

Data: the panel polls `GET /v1/dashboard/summary` and `GET /v1/jobs?status=awaiting_approval&limit=3`
every 15 s and refreshes when Mira announces a finished job. No API changes were needed.

## Accessibility and motion

- Every quick action, stat and suggestion is a real `<button>` or `<a>` with a text label; the composer has a
  visible `<label>` and its help text is linked with `aria-describedby`.
- The Space bar still talks to Mira; it is ignored inside the composer and on buttons (unchanged rule).
- Status messages (offline workers, load errors) use `role="status"`.
- No new animations. Scrolling to the composer after a quick action uses `behavior: "auto"` when the user
  prefers reduced motion; hover transitions are covered by the global reduced-motion rule.
- Colours come from the existing tokens (`bg`, `surface`, `line`, `brand`, `cyan`, `warn`, `bad`): dark mode
  and the MCR brand gradient are unchanged.

## Tests

`npm test` in `apps/web`: 45 tests (18 new).

- `src/lib/workflows.test.ts`: catalogue only contains planner-supported deliverables, planned steps are
  not actions, offline queue detection, suggestion order and limits, first-run detection, zero budget.
- `src/components/assistant/assistant.test.tsx`: Enter sends / Shift+Enter does not / IME composing does
  not / Send disabled until long enough; a quick action is handed to the page (not submitted); planned
  steps are not buttons or links; offline warnings; disabled while designing; Review links go to
  `/jobs/{id}`; counts link to the filtered list; suggestions open pages or start quick actions.

Checked in the browser at 1440 × 1000 and 375 × 812: no horizontal scroll; the quotation card fills the
composer, focuses it with the caret at the end and starts nothing; full screen still hides the panels and
Escape leaves it.

Production build (`npm run build`, Next.js 16.4.6, 2026-11-20): compiled, type-checked and generated all 14
pages with no errors or warnings. `/assistant` and `/jobs` are prerendered as static pages (`/jobs` reads
`?status=` on the client). Next 16 writes dev output to `.next/dev`, so the build can run while the dev
server is up.

## Iteration 2 (2026-11-20): Focus/Work modes, live jobs, shared snapshot

Mira's 3D character and animations, voice (listen, speak, barge-in, Space), job confirmation, the poster
design flow and full screen are unchanged. No API or backend change was needed.

| # | Requirement | What changed |
|---|---|---|
| 1 | Focus / Work mode | `ModeToggle` in the header (two toggle buttons, `aria-pressed`). **Work**: Mira at 34–40 vh (240–460 px) beside the dashboard. **Focus**: Mira at 48–56 vh (300–680 px), panels and quick actions hidden, a one-line `FocusStrip` ("8 waiting for approval · 0 in progress", linked) with "Show dashboard". Saved per browser (`lib/layoutMode.ts`, `useSyncExternalStore`, falls back to memory when storage throws). Measured at 1440×1000: Work 400 px, Focus 560 px. |
| 2 | Live progress with AgentGraph | The panel lists jobs in progress (queued → running) with their real progress bar and **Watch live**, which puts the existing `AgentGraph` on Mira's stage for that job (also for jobs started by n8n or elsewhere). The graph header has **Back to Mira**. Suggestions offer "Watch … live" too. |
| 3 | Approvals: deadline / priority | The data model has **no deadline or business priority** (`Task.priority` is internal scheduling order), so none is shown. Each waiting job now shows real data: how long it has waited ("Ready for review 3 hours ago", from `finished_at`), its QA verdict (passed / asks for a human check) and the demo badge. |
| 4 | Consistent briefing and overview | `lib/opsSnapshot.ts`: one snapshot (dashboard summary + one jobs list) feeds the panel, the suggestions and Mira's welcome briefing (`ops.get()` returns the snapshot on screen when it is fresh, otherwise refreshes it for both). Budget text comes from one function (`budgetFigures` in `lib/briefing.ts`). Requests per refresh went from 2 + 1 for the briefing to 2 shared; concurrent refreshes share one request. |
| 5 | Context-aware suggestions | Added: review the single waiting job directly (`/jobs/{id}`); watch a running job; send the newest failed command again (fills the composer, confirmed before it runs; skipped when the agents it asked for are offline, then the filtered failed list is linked instead). Existing rules kept. |
| 6 | Preserve voice etc. | Conversation code untouched except the Space handler, which now also lets `/` focus the composer and ignores Space on links. |
| 7 | States | Loading (skeleton, `aria-busy`), first-load error with **Try again**, failed refresh keeps the last figures and says from when, empty (no waiting jobs / no live jobs), browser offline banner (`useOnline`), offline workers (existing). |
| 8 | Mobile / tablet | Phone: Mira 34 vh (276 px at 375×812), talk button 112 px, order composer → operations → suggestions → quick actions. Tablet (768): operations and suggestions side by side. Desktop: right column. No horizontal scroll at 375, 768, 1440. |
| 9 | Accessibility | Skip link to the command box; `/` shortcut (listed under the composer on md+); mode toggle as a labelled group; screen readers hear job **status changes** in a polite live region instead of every progress percent (the caption's "Working… n%" is `aria-live="off"`); error banner `role="alert"`; progress bars labelled with the job; Watch / Review buttons name the job. |
| 10 | Tests | 22 new (67 total): `lib/opsSnapshot.test.tsx` (shared request, freshness, keeps data on failure, combined list request, live/waiting split, `useOnline`, briefing figures, layout storage), `lib/workflows.test.ts` (+3 suggestion rules), `components/assistant/workmode.test.tsx` (mode toggle, focus strip, loading / error / stale states, watch live, waiting age + QA, suggestion actions). |

New files: `lib/opsSnapshot.ts`, `lib/briefing.ts`, `lib/layoutMode.ts`, `components/assistant/ModeToggle.tsx`,
and the two test files. Changed: `assistant/page.tsx`, `components/assistant/OpsPanel.tsx`, `lib/workflows.ts`,
`i18n/dictionaries.ts`.

Checks: type check, lint and 67 tests pass; `npm run build` passes (14 pages). In the browser (dev server):
one summary and one jobs request per refresh; mode switch, saved choice, `/` shortcut and "Show dashboard"
work; layouts measured at 375, 768 and 1440 px wide.

## Not done / not tested

- **Watch live and Back to Mira were not exercised in the browser**: there was no running job, and starting
  one on the dev stack would call paid providers. Covered by unit tests of the panel and suggestions only.
- **Offline banner** not seen in the browser (the pane cannot simulate offline); `useOnline` is unit-tested.
- **Screen-reader output** was checked through the markup (roles, labels, live regions), not with a real
  screen reader.
- **Voice paths** (welcome briefing spoken from the shared snapshot, barge-in) were not re-run by voice;
  the briefing text itself is unit-tested against the panel's figures.
- Suggestions do not yet look at per-agent usage history.
- The jobs list in the snapshot is the newest 30 of the listed statuses; with more than 30 such jobs the
  oldest live or waiting ones are reached through "View all" / the jobs page.
