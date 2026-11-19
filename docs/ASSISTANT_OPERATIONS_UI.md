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

## Not done

- Suggestions use the dashboard summary only. They do not yet look at per-agent usage history.
