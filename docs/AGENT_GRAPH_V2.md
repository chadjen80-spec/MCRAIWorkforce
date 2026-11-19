# Agent Graph V2: change report

Date: 2026-11-20 · Component: `apps/web/src/components/AgentGraph.tsx` (used on `/assistant` and the Job
Monitor `/jobs/{id}`)

Same visual identity: hexagon tiles, glowing halos, light chasing round the frames, beams from the Master
Bot, travelling dots, star field. Mira's voice experience is untouched. No API, backend or state semantics
changed; all data comes from responses the pages already receive (`GET /v1/jobs/{id}`, `/tasks`, `/qa`,
`/artifacts`).

## What changed

| # | Requirement | Implementation |
|---|---|---|
| 1 | Fit and centre on actual nodes | `graphBounds()` measures the master, the visible hubs, their task satellites, selection rings and labels; `fitView()` pads and centres it. The view refits when nodes appear, unless you have zoomed or panned. A job with two agents now fills the frame instead of sitting in a 1000×770 canvas (real job: 586×700). |
| 2 | Every node selectable | Master Bot, every agent hub and every task satellite is a `role="button"` with `aria-pressed`. Satellites are no longer nested inside the hub button. Click the background or press Esc to clear. |
| 3 | Details panel | `components/graph/GraphDetails.tsx`: status, execution time (from `started_at` / `finished_at`; live "running for" while active), actual cost (`actual_cost_usd`, or "Not reported yet" with the estimate beside it, never a made-up figure), provider and where it ran, the router's reason, what it waits for, attempts / retries, error code and message, and output files (links when the API gives a download or preview URL). The Master Bot shows the job's time, cost and error. |
| 4 | AI progress vs human approval | `ProgressStrip` above the graph: **AI processing** (job progress, tasks done / failed / retrying) and **Human approval** (not required, required after the AI work, waiting, approved, rejected, not reached), from `status`, `require_approval` and `approval_status`. The Master Bot now reflects AI work only: a job waiting for review shows its AI work as done. |
| 5 | QA verdict explained | Selecting QA shows PASS / REVISE / HUMAN_REVIEW with what that means in this system (wording follows `agents/qa.py` and the orchestrator), whether approval still applies after a PASS, the server's summary, and each check with pass / warn / fail and its detail. The QA hub label shows the verdict. The former "QA node opens the files" shortcut is now an **Open files** button in the QA details. |
| 6 | Accurate dependencies | `buildGraphModel()` derives edges from every task's `depends_on`, **including edges into QA** (previously hidden), with arrowheads for direction and a tooltip with the number of task links. Same-agent dependencies are listed in the details ("Waits for"). The planner is shown through the Master Bot's beams, as before. |
| 7 | Zoom, pan, reset, fit | Wheel (toward the pointer), drag, buttons −, +, **Fit**, **Reset** (fit and clear the selection), double-click to fit, and keyboard on the focused graph: + / − / 0 / arrow keys / Esc. |
| 8 | Failed and retrying | New `retrying` state (attempt > 1 and not finished: after a failure or a QA REVISE). Failed and retrying nodes get a second outline (dashed for retrying), an "!" or "↻" badge, coloured label text; agent labels say "2 failed" or "Retrying (attempt 2 of 3)". Legend gained "Retrying". |
| 9 | Performance and accessibility | Star field drawn once as a tiled `<pattern>` (70 circles instead of 630); twinkling stars and moving dots off with reduced motion; arrows trimmed to node edges. Focus ring on every node, graph surface focusable with a keyboard description, zoom level announced, details panel is a labelled region in a polite live area. |
| 10 | Visual identity | Unchanged styles and animations; colours, glows and the hexagon tiles are the same components. |

Data wiring: the Job Monitor passes the QA report and artifacts it already loads. The Assistant page loads
them once, in the same request cycle that sees the job settle (polling stops there); its poster preview now
reuses those artifacts instead of a separate request.

## Files

- New: `src/lib/agentGraph.ts` (model, states, layout bounds, fit, timings, costs),
  `src/components/graph/GraphDetails.tsx` (details panel, progress strip, QA section),
  `src/lib/agentGraph.test.ts`, `src/components/AgentGraph.test.tsx`.
- Changed: `src/components/AgentGraph.tsx`, `src/app/(app)/jobs/[id]/JobMonitor.tsx`,
  `src/app/(app)/assistant/page.tsx`, `src/i18n/dictionaries.ts` (Thai and English).

## Verification

- Lint, type check: clean. Tests: 90 pass (23 new: model, layout, fit, edges, states, selection, details,
  QA, approval, failed / retrying, view controls, keyboard). Production build: passes (14 pages).
- Real job in the browser (`/jobs/022dbfe5…`, captions, QA HUMAN_REVIEW): fitted view 586×700; one edge
  content→QA from the task data; Content details showed 4 s, $0.0004, OpenAI · cloud, the router's reason
  and its two files; QA details showed the explanation, server summary and 4 checks (3 pass, 1 warn); the
  strip showed AI 100% (2/2) next to "waiting for a reviewer"; keyboard zoom, pan, fit and Esc worked; no
  console errors from the graph.

## Not verified

- **Failed, retrying and REVISE states on a live job**: none existed in the dev data, and causing one would
  need provider failures. Covered by unit tests with task fixtures.
- **Animations** were checked in code and tests, not watched on screen (the browser pane showed another tab).
- **Narrow layout (< 640 px)** uses the same fitting code with the narrow positions; not measured in the
  browser this time.
- Screen-reader output was checked through roles, names and live regions, not with a real screen reader.
