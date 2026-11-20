# Natural caption generation upgrade

Date: 2026-11-20 · Content Agent, caption regeneration, QA, caption controls

## What the code did before (inspection)

| Question | Finding |
|---|---|
| Hard-coded templates, keyword replacement, sentence insertion | **No** in the AI path. Captions came from one OpenAI call (`complete_json` into a Pydantic schema). |
| Code-added text | **Yes, partly.** `format_caption` appended the model's separate `call_to_action` field to the end of the caption when it was not already in the text, then a hashtag line. |
| Prompt | One system prompt: "each caption needs a hook, a clear benefit and a call to action, and 3-6 hashtags". It forced every caption into the same shape and always added hashtags. There was no creative brief, no developer layer, no tone or angle metadata and no list of clichés to avoid. |
| Regeneration | One "New caption" action with an optional instruction. A full LLM rewrite, given the current caption and its siblings. It overwrote the file with **no version history**. |
| Demo fallback | Demo output was clearly marked `[DEMO]` and `is_demo`. However, **in demo mode, regenerating a real AI caption replaced it with a demo placeholder.** |
| QA | Deterministic checks: count, empty, Thai, exact duplicates and a claim regex (warning only). The optional LLM review used a generic prompt. No similarity, cliché or repetition checks, and no rewrite when a caption failed. |
| OpenAI adapter | Chat Completions with `response_format: json_object` and Pydantic validation, plus one repair round. Model and timeout configurable; token usage recorded; cost estimated from configured per-1M-token prices; budget checked before each routed call. |

## What changed

### Creative Brief (`app/agents/captions.py`)

Before writing, the agent builds a `CreativeBrief` covering:

- brand and product or service;
- target audience, communication objective and key message;
- platform, tone of voice and language;
- verified product facts and call to action;
- desired length (`short` / `medium` / `long`);
- words or claims to avoid;
- the owner's request.

Sources:
- the project's brand guidelines (which can set `audience`, `tone`, `key_message`, `call_to_action` and `avoid`);
- the organization's verified business context (one fact per line);
- the planner's task parameters.

A missing item is rendered as "not provided: write neutrally, do not invent it". The brief is saved in the content task's result (`creative_brief`), and every regeneration uses that original brief.

### Prompt layers

| Layer | Content |
|---|---|
| system | The Content Agent as an experienced Thai creative copywriter |
| developer | Writing rules:<br>• whole captions;<br>• no stock openings and no CTA pasted at the end;<br>• no forced hook-body-CTA structure;<br>• varied rhythm and openings, and a different focus per caption;<br>• natural Thai;<br>• a cliché list (ในยุคดิจิทัล, ยกระดับธุรกิจ, ตอบโจทย์ทุกความต้องการ, ก้าวไปอีกขั้น…);<br>• at most 2 emojis and 0-3 hashtags.<br>Also brand safety and factual constraints (the existing guardrails, plus: numbers only from verified facts) and the output fields. |
| user | The Creative Brief and the deliverables. Each caption is given a creative angle: conversational, storytelling, problem and solution, educational, direct promotional or community engagement. |

`OpenAIClient.complete_json` now accepts a `developer` layer and a `model` override:
- The developer layer is sent with the `developer` role.
- If a provider rejects that role (some OpenAI-compatible gateways do), the call is repeated once with the layer merged into the system message, and the client remembers this for the process.
- `OPENAI_DEVELOPER_ROLE=false` always merges it.
- **Verified live:** `gpt-4o-mini` on api.openai.com accepts the `developer` role. One call used 1,232 input and 312 output tokens, an estimated $0.0004.

Output schema per caption: `text`, `tone`, `platform`, `creative_angle`, `hashtags`. It is validated by Pydantic before anything is saved.
- The stored caption is exactly the model's `text`, plus a line with the hashtags the model chose, when there are any.
- Code adds nothing else.

### Regeneration: five full-rewrite actions

`POST /v1/artifacts/{id}/regenerate-caption` with `action`:

| Action | What the model is asked |
|---|---|
| `regenerate` | A new creative approach. The previous version is a negative reference: no reused hook, sentences or CTA. |
| `rewrite` | Rewrite naturally: same meaning and facts, every sentence written anew |
| `tone` | The whole caption rewritten in the chosen `tone` |
| `shorten` / `expand` | A coherent shorter or longer caption, not trimmed or padded |

Every call also sends:
- the original brief;
- the owner's feedback (`instruction`);
- the campaign's other captions, so their openings and phrasing are not repeated.

The new text is checked against the previous one:
- **Contains or is cut from the previous caption:** rejected (no concatenation, no trimming).
- **`regenerate`:** rejected if it is too similar to the previous version or keeps its opening.
- **`rewrite` / `tone`:** rejected if barely changed.
- **`shorten` / `expand`:** rejected unless clearly shorter or longer.
- **Meaning-preserving actions:** must keep every verified number, such as prices and quantities.

A rejected attempt gets **one** further full rewrite, with the reasons (`CAPTION_MAX_REWRITES`, default 1). If that also fails, the API returns `422 CAPTION_QUALITY_FAILED` and **the caption is not changed**.

Errors also leave the caption untouched:

| Situation | Response |
|---|---|
| Provider error | 502 |
| Budget exceeded | 402 |
| No cloud model | 409 |

Demo mode never replaces an AI-written caption.

### Version history

`edit_artifact_text` keeps a caption's earlier texts in its metadata. Each entry records the text, when, who and how it was made (`original`, `edit`, `regenerate`, `rewrite`, `tone`, `shorten`, `expand`). The last 20 are kept. Restoring a version is an ordinary save of that text, so the replaced text also becomes a version.

### Quality checks (`issues_for`)

These checks are deterministic: they cost nothing and give the same result every time. They run on every generated and regenerated caption.

| Check | Severity |
|---|---|
| Not Thai when Thai was requested | fail |
| Unsupported claims (prices, %, discounts, "free", guarantees, "รับรองว่า", No.1 or "best" not in the verified facts or the request) | fail |
| Clichés from the list, words the brief says to avoid | fail |
| Near-duplicate of another caption (character 3-gram similarity ≥ 0.55, which works for Thai) | fail |
| More than 6 emojis | fail (5-6: warn) |
| Same opening as another caption, phrase shared with two other captions, repeated sentence, many "!", more than 5 hashtags, too long for the requested length | warn |
| Rewrite-specific checks (above) | fail |

When captions are generated:
- The ones that fail get one bounded full rewrite together, with the reasons and the accepted captions as negative references.
- Anything still flagged is saved with the caption (`metadata.quality`) and in the task result (`quality_flags`).

QA Agent:
- It reports these checks as warnings, so a person reviews them and there are no automatic rework loops.
- Its optional OpenAI review (when configured and within budget) now also checks natural Thai, coherence, clichés and filler, captions repeating each other, and platform fit.

### API integration

- **Model:** `OPENAI_CONTENT_MODEL` (or `content_model` in the organization's OpenAI provider settings) picks a separate model for captions. Empty uses `OPENAI_MODEL`. The dev setup uses `OPENAI_CONTENT_MODEL=gpt-4.1` (in `apps/api/.env`; compose passes the variable through for Docker). The other agents keep `OPENAI_MODEL`.
  - Only the GPT-4o and GPT-4.1 families work for now: GPT-5.x and o-series models reject the `temperature` setting the caption writer sends.
  - Live check with `gpt-4.1`: developer role accepted; 1,323 input and 269 output tokens, about $0.005 for three captions.
- **Per-model prices:** `LLM_MODEL_PRICES` (USD per 1M tokens, input and output) costs each call at its own model's price. The defaults are configuration taken from memory, not checked against OpenAI's current price list; verify them:

  | Model | Input | Output |
  |---|---|---|
  | `gpt-4o-mini` | 0.15 | 0.60 |
  | `gpt-4.1-mini` | 0.40 | 1.60 |
  | `gpt-4.1` | 2.00 | 8.00 |
  | `gpt-4o` | 2.50 | 10.00 |

  Models not listed use `LLM_INPUT_USD_PER_1M` / `LLM_OUTPUT_USD_PER_1M`.
- **Timeout:** the existing `OPENAI_TIMEOUT_SECONDS`.
- **Retries:** one retry after a temporary provider error, plus the adapter's repair round for invalid output.
- **Budget:** checked before **every** call, including rewrites.
- **Usage:** each call is recorded with its input and output tokens, model, purpose `captions` and cost. Token counts are the API's actual numbers. The cost is computed from the configured per-1M-token prices, because the API does not return a price.

### Caption controls (UI)

`components/CaptionTools.tsx`, on every caption card:
- **Actions:** Regenerate (with optional feedback), Rewrite naturally, Change tone (tone picker plus feedback), Shorten, Expand. Copy and Edit/Save remain.
- **While writing:** the card says the current caption stays until the new version is ready.
- **On failure:** the message says nothing was replaced.
- **Versions:** a list of earlier versions, newest first, each with Restore.

## Tests

| Suite | Result |
|---|---|
| `tests/test_natural_captions.py` (new, mocked OpenAI) | 17 passed (includes the caption model and its price) |
| Full backend suite, including the creative AI workflow tests (`test_workforce.py`, 90 passed) | 217 passed, 1 skipped (a PostgreSQL-only test), after the final check changes |
| Frontend (Vitest) | 131 passed (5 new for the caption controls) |
| ruff, mypy, ESLint, TypeScript, `next build` | clean |
| Live OpenAI call (`gpt-4o-mini`) | developer role accepted, valid structured output, usage and cost computed |
| Browser (dev stack) | the five actions and the tone form render on a real caption at phone width (no live regeneration was run, so the caption was not changed) |

Coverage of the requested tests:
1. **Full generation:** system, developer and user layers, the brief and the angles are sent. The stored text equals the model's text.
2. **Regeneration calls the model again**, with the original brief, the previous version and the feedback.
3. **No concatenation:** an appended version is rejected and rewritten. A trimmed "shorten" is refused.
4. **Feedback** reaches the prompt and changes the result.
5. **Verified facts:** dropping a verified price in a tone change is refused; keeping it passes.
6. **Unsupported claims** are refused after one bounded retry, and generation rewrites failing captions once.
7. **Distinctness:** near-duplicates and clichés are detected; the six sample angles differ.
8. **Provider failure** keeps the caption and its history; demo mode never replaces an AI caption.
9. **Budget:** an exceeded budget stops a regeneration before any call.
10. **Existing creative AI workflows** pass.

Also tested: version history and restore, usage records, the developer-role fallback, and that every action asks for a full rewrite.

Changed test: `test_workforce.py::test_regenerate_caption` used to expect an appended CTA and a saved "100%" claim. It now expects the model's whole caption, the kept previous version and no warnings. Claim rejection has its own test.

## Limitations

- **Thai quality depends on the model.** With `gpt-4o-mini`, the live output was fluent but still somewhat generic, and the checks caught one cliché ("ครบจบในที่เดียว") that the rewrite would remove. `gpt-4.1` wrote more natural, conversational Thai on the same brief, but all three captions still repeated the same feature list. The checks flag that as a warning for a person to review.
- **The checks use string patterns, not understanding.** The cliché list and similarity score can miss new stock phrases or flag acceptable repetition. Facts are compared as numbers and names, not meaning; a reworded fact that drops a detail without a number is not detected. The optional AI review in QA covers some of this, as warnings.
- **The brief is built from what the system already has.** That is the brand guidelines, the business context and the planner's parameters; there is no form yet for audience, objective or key message per job, so they come from the request text or stay neutral.
- **Version history lives in the artifact's metadata** (last 20). It is not a separate table and is deleted with the caption.
- **Cost is an estimate** from configured prices; token counts are actual.
