# Known limitations and remaining work

## Not verified on the development machine

The machine used for implementation had no Docker, PostgreSQL, Redis, OpenAI key or running ComfyUI.
The following therefore exist as code and configuration but were **not executed**:

| Item | State |
|---|---|
| Docker images and both Compose files | Written; never built or started. Expect to fix small issues on first `docker compose up`. |
| PostgreSQL | The migration was generated and applied on SQLite only. Types are portable (`Uuid`, `JSON`, `Numeric`, timezone-aware `DateTime`), but `alembic upgrade head` has not been run against PostgreSQL, and row locking (`FOR UPDATE [SKIP LOCKED]`) is a no-op on SQLite, so concurrent-dispatch locking is untested. |
| Redis broker and Redis rate limiting | Not run. Celery was run for real with the filesystem broker. |
| MinIO / S3 storage backend | `S3Storage` is untested. The local storage backend is tested. |
| OpenAI API (chat, images, speech) | Adapters are tested against HTTP-level fakes that check request shape and auth header, not against the real service. Model names and prices in the defaults are configuration to confirm. |
| Adobe Firefly | Adapter written to Adobe's v3 async API reference and tested against an HTTP-level fake (sign-in, job, status polling, download). Not yet run against the real service: needs your Firefly Services credentials. Image5 uses a different request format and is not supported yet. |
| ComfyUI | Verified with a real local install (SDXL base 1.0 on an RTX 5080): images are generated and routed locally. The Docker path (`host.docker.internal`) is not verified. |
| Production HTTPS (Caddy) | Configuration only. |

## Functional limitations

- **Demo mode is not AI output.** With no provider configured and `DEMO_MODE=true`, captions,
  images, document text and the video script are labelled placeholders. Only the pipeline (planning,
  queues, storage, PDF/DOCX/MP4 rendering, QA, approval) is real in that mode.
- **Video is template-based.** Full-screen slides (heading, on-screen text, burned-in captions) over
  generated images or a gradient, hard cuts, no motion, no music. Remotion is not used.
- **Voice and scenes:** each scene's narration is recorded as its own clip and the scene lasts as long as its clip
  (plus a short pause), so the voice changes with the slides; spare time is shared across scenes.
- **Video length:** narration is budgeted from the measured speaking speed of the TTS voice (Thai ≈ 8.2
  characters/s; the script is asked to fill 80–100% of it), shortened by a second model pass if it overshoots, and sped up by at most 1.2× if still slightly long.
  Only beyond that is the video lengthened, and QA then flags it for review.
- **Voiceover** needs a cloud TTS provider. Without one the MP4 has a silent AAC track and the QA
  report says so. Voice language quality for Thai depends on the provider/model chosen.
- **Event posters** have two layouts: esports 9:16 (sponsor bar, title, date banner, headline, venue, time,
  presenter row; three colour themes) and tournament 4:5 (prize, registration, schedule card, QR code,
  sponsor strip, optional left/right characters on a drawn backdrop). Headline fonts are Kanit (Black Italic) when installed, else Noto Sans Thai Black,
  else the regular bold Thai font; set `FONT_DISPLAY_PATH` to use another. The artwork is lowered under the title
  because image models tend to put faces in the top third; very tall subjects can still meet the title.
  The art prompt asks for an original character, but a description that names an existing game hero can still
  produce look-alikes: only publish artwork you have the right to use.
- **Thai typography.** PDF and video text use a manual fix for tone marks stacked on upper vowels
  instead of full OpenType shaping (ReportLab's shaping was unreliable for Thai; Pillow on Windows
  has no libraqm). Other mark combinations (for example marks above tall consonants such as ป ฝ ฟ)
  may sit slightly low. Line breaks in Thai PDF paragraphs can fall inside a word because there is
  no Thai word segmentation. The Docker image uses Pillow with libraqm, which shapes correctly in
  video slides.
- **DOCX** uses the Tahoma font by name; it is not embedded, so the reader's machine needs it
  (present on Windows and Office installations).
- **Rule-based planner** (used when no LLM is available) recognises counts and keywords in English
  and common Thai phrasings only. Ambiguous commands may be under-planned; the plan is shown in the
  Job Monitor before work completes.
- **Limits:** at most 8 images and 20 captions per job, video 15–120 seconds, one document and one
  video per job.
- **QA** verifies integrity, counts, language, codecs, duration and metadata deterministically. It
  does not judge visual brand consistency of images; its text review needs a cloud LLM.
- **Partial jobs** are final: there is no "retry failed task" button yet (submit a new job).
- **Cancellation** of a running task takes effect at its next heartbeat; a long FFmpeg render or a
  blocking provider call finishes first.
- **Dead-letter handling** is a terminal `failed` task plus a `task.failed` audit event with
  `dead_letter: true`; there is no separate dead-letter queue or replay tool.
- **Windows workers** run with the threads pool, where Celery time limits are not enforced. Timeouts
  there come from the HTTP clients and the FFmpeg subprocess timeout.

## Security notes

- Dashboard sessions are a JWT in an httpOnly, SameSite=Lax cookie held by the Next.js server; there
  is no refresh token or server-side revocation list (tokens expire after 12 hours by default).
- The dashboard proxy relies on SameSite=Lax plus JSON bodies for CSRF protection; there is no
  explicit CSRF token.
- Signed download links are bearer capabilities valid for 15 minutes; anyone holding a link can
  download that file until it expires.
- User management is CLI-only (`app.cli seed`); there is no invitation flow, password reset or MFA.
- API keys have the admin role and no per-key scopes.
- Approving jobs and deleting library assets require the owner or admin role (members can create and
  review work). Files of jobs that are not approved are drafts: people in the dashboard can review
  them, but API-key clients (n8n) receive no download link until the job is approved.
- Rate limiting is per process unless `REDIS_URL` is set.
- No malware/content-safety scanning of generated assets.

## Remaining work before a pilot

1. Bring the Compose stack up on a Docker host; run migrations and the test suite against
   PostgreSQL, Redis and MinIO; fix what surfaces.
2. Run the acceptance script with a real OpenAI key and a real ComfyUI instance; review output
   quality and tune prompts and the SDXL workflow.
3. Import the n8n workflow into the deployed n8n, attach credentials and run it end to end.
4. Load-test concurrent jobs and confirm queue isolation and lease recovery by killing workers.
5. Add user management, per-task retry from the dashboard and a dead-letter replay command.
6. Optional: motion/transitions (Remotion), background music from a licensed library, additional TTS
   and image providers. (The visual agent graph now exists in the Job Monitor and on Mira's screen.)

See [MCR_WORKFORCE_CURRENT_STATE.md](MCR_WORKFORCE_CURRENT_STATE.md) for the full audit and roadmap.
