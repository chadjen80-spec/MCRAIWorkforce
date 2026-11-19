"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { AgentGraph } from "@/components/AgentGraph";
import { AssistantAvatar, type AvatarMode } from "@/components/AssistantAvatar";
import { ChatBubbles } from "@/components/PosterChat";
import { LogoUploadButton } from "@/components/LogoUploadButton";
import { CommandComposer } from "@/components/assistant/CommandComposer";
import { FocusStrip, ModeToggle } from "@/components/assistant/ModeToggle";
import { OpsSummary, StartGuide, TaskSuggestions } from "@/components/assistant/OpsPanel";
import { QuickActions } from "@/components/assistant/QuickActions";
import { Button, DemoBadge, buttonClass, cx } from "@/components/ui";
import { JobStatusBadge } from "@/components/status";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/dictionaries";
import { api, fileUrl, newIdempotencyKey, toApiError } from "@/lib/api";
import {
  EMPTY_POSTER,
  TOURNAMENT_WORDS,
  cleanSpec,
  mergeDraft,
  posterAspect,
  saveHandoff,
  tournamentDefaults,
  usePosterChat,
  usePosterPreview,
  withEventLogo,
  withGameLogo,
  withPresenters,
  withSponsors,
  withTemplate,
} from "@/lib/poster";
import { workforceBriefing } from "@/lib/briefing";
import { useLayoutMode } from "@/lib/layoutMode";
import { liveJobs, useOnline, useOpsSnapshot, waitingJobs } from "@/lib/opsSnapshot";
import type {
  Artifact,
  BrandAsset,
  ItemList,
  Job,
  JobStatus,
  PosterSpec,
  PosterTemplate,
  QaReport,
  Session,
  Task,
} from "@/lib/types";
import { POSTER_WORDS, confirmAction, posterIntent } from "@/lib/intent";
import { useApi } from "@/lib/useApi";
import { VoiceError, canListen, listenOnce, speak } from "@/lib/voice";
import { isFirstRun, workflowById, type Workflow, type WorkflowId } from "@/lib/workflows";

// Voice-first front door: a 3D assistant greets the user, listens to one spoken command,
// repeats it back, and only starts the job after an explicit confirmation (spoken or clicked),
// because a job can spend money. Typing works everywhere as a fallback.

const SETTLED: readonly JobStatus[] = ["awaiting_approval", "completed", "partial", "failed", "cancelled", "rejected"];

export default function AssistantPage() {
  const { t, locale } = useI18n();
  const router = useRouter();
  const tag = locale === "th" ? "th-TH" : "en-US";

  const [mode, setMode] = useState<AvatarMode>("idle");
  const [bubble, setBubble] = useState<string | null>(null);
  const [heard, setHeard] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [settledJob, setSettledJob] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const session = useRef<AbortController | null>(null);
  // Mira celebrates (smiling eyes, bounce, sparkles) when a job finishes with all deliverables.
  const [celebrate, setCelebrate] = useState(false);
  // ...and looks sad (downcast eyes, frown, a tear) when it fails or only partly succeeds.
  const [sorry, setSorry] = useState(false);
  const celebrateTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // --- poster design on Mira's screen -------------------------------------------------------
  const [posterMode, setPosterMode] = useState(false);
  const [posterSpec, setPosterSpec] = useState<PosterSpec>(EMPTY_POSTER);
  const posterSpecRef = useRef(posterSpec);
  useEffect(() => {
    posterSpecRef.current = posterSpec;
  });
  const posterChat = usePosterChat({
    language: locale,
    getSpec: () => posterSpecRef.current,
    onDraft: (draft) =>
      setPosterSpec((s) => {
        const next = mergeDraft(s, draft);
        posterSpecRef.current = next;
        return next;
      }),
    unknownNote: (names) => t("posterChat.unknownAssets", { names }),
  });
  const posterPreview = usePosterPreview(posterSpec);
  // Jobs created from a design here: their finished poster is shown on Mira's screen.
  const [posterJobs, setPosterJobs] = useState<string[]>([]);

  const monitor = useApi<{ job: Job; tasks: Task[]; qa?: QaReport | null; artifacts?: Artifact[] }>(
    jobId ? `assistant:${jobId}` : null,
    async (signal) => {
      const [job, tasks] = await Promise.all([
        api<Job>(`/v1/jobs/${jobId}`, { signal }),
        api<ItemList<Task>>(`/v1/jobs/${jobId}/tasks`, { signal }),
      ]);
      if (!SETTLED.includes(job.status)) return { job, tasks: tasks.items };
      // Settled: polling stops after this fetch, so the QA report and the files are loaded once here
      // (the graph's details panel and the poster result both use them).
      const [qa, artifacts] = await Promise.all([
        job.qa_verdict ? api<QaReport>(`/v1/jobs/${jobId}/qa`, { signal }).catch(() => null) : Promise.resolve(null),
        api<ItemList<Artifact>>(`/v1/jobs/${jobId}/artifacts`, { signal }).then((r) => r.items, () => undefined),
      ]);
      setSettledJob(job.id);
      return { job, tasks: tasks.items, qa, artifacts };
    },
    { interval: jobId !== null && settledJob === jobId ? 0 : 2000 },
  );
  const job = monitor.data?.job;
  const settled = job ? SETTLED.includes(job.status) : false;

  // --- operations overview -----------------------------------------------------------------
  // One shared snapshot feeds the welcome briefing, the operations panel and the suggestions.
  const ops = useOpsSnapshot();
  const online = useOnline();
  const [layout, setLayout] = useLayoutMode();
  const composerRef = useRef<HTMLTextAreaElement>(null);
  // Set when a quick action filled the composer: shows what to add before sending.
  const [filled, setFilled] = useState(false);
  const showPosterResult = Boolean(job && settled && posterJobs.includes(job.id));
  // The finished poster comes from the files loaded with the settled job (no separate request).
  const posterResult = { data: showPosterResult ? monitor.data?.artifacts?.filter((a) => a.kind === "image") : undefined };

  // --- conversation primitives ---------------------------------------------------------
  const begin = (): AbortSignal => {
    clearTimeout(celebrateTimer.current);
    setCelebrate(false);
    setSorry(false);
    session.current?.abort();
    const controller = new AbortController();
    session.current = controller;
    setBusy(true);
    return controller.signal;
  };
  const end = (signal: AbortSignal) => {
    if (!signal.aborted) setBusy(false);
  };
  const stop = () => {
    session.current?.abort();
    session.current = null;
    setBusy(false);
    setMode("idle");
    setHeard("");
  };

  const say = async (text: string, signal: AbortSignal) => {
    if (signal.aborted) return;
    setBubble(text);
    setMode("speaking");
    await speak(text, tag, signal);
    if (!signal.aborted) setMode("idle");
  };
  const hear = async (signal: AbortSignal): Promise<string> => {
    setHeard("");
    setMode("listening");
    try {
      return await listenOnce(tag, { onInterim: setHeard, signal });
    } finally {
      if (!signal.aborted) setMode("idle");
    }
  };
  const reportVoiceError = async (e: unknown, signal: AbortSignal) => {
    const code = e instanceof VoiceError ? e.code : "failed";
    if (signal.aborted || code === "aborted") return;
    if (code === "no-speech") return say(t("assistant.notHeard"), signal);
    setBubble(code === "unsupported" ? t("voice.unsupported") : t(`voice.err.${code}`));
    setMode("idle");
  };

  const submit = async (command: string, signal: AbortSignal) => {
    setMode("thinking");
    try {
      const created = await api<Job>("/v1/jobs", {
        method: "POST",
        body: { command, language: locale },
        headers: { "Idempotency-Key": newIdempotencyKey() },
      });
      setPending(null);
      setJobId(created.id);
      await say(t("assistant.started"), signal);
    } catch (err) {
      await say(t("assistant.submitFailed", { message: toApiError(err).message }), signal);
    }
  };

  // One design turn: send what was said or typed, apply Mira's draft, read her answer, listen again.
  const converse = async (text: string, signal: AbortSignal): Promise<void> => {
    setMode("thinking");
    const res = await posterChat.send(text);
    if (signal.aborted) return;
    if (!res) return say(t("posterStage.chatFailed"), signal);
    await say(res.reply, signal);
    await listenForDesign(signal);
  };

  const listenForDesign = async (signal: AbortSignal): Promise<void> => {
    if (signal.aborted || !canListen()) return; // no microphone: typing and the buttons remain
    let answer: string;
    try {
      answer = await hear(signal);
    } catch (e) {
      // Silence or a blocked microphone is fine here: the buttons and the text box are on screen.
      if (!(e instanceof VoiceError) || e.code === "failed" || e.code === "network") await reportVoiceError(e, signal);
      return;
    }
    const intent = posterIntent(answer, locale);
    if (intent === "create") return createPoster(signal);
    if (intent === "close") return closeDesign(signal);
    return converse(answer, signal);
  };

  const designPoster = async (text: string, signal: AbortSignal) => {
    setPending(null);
    setJobId(null);
    // A tournament / registration poster starts in the 4:5 layout (Mira can still switch it).
    const start = TOURNAMENT_WORDS.test(text) ? withTemplate(EMPTY_POSTER, "tournament", tournamentDefaults(t)) : EMPTY_POSTER;
    setPosterSpec(start);
    posterSpecRef.current = start;
    posterChat.reset();
    setPosterMode(true);
    await converse(text, signal);
  };

  const createPoster = async (signal: AbortSignal) => {
    const spec = posterSpecRef.current;
    if (!spec.title.trim()) return say(t("posterStage.needTitle"), signal);
    setMode("thinking");
    try {
      const created = await api<Job>("/v1/jobs", {
        method: "POST",
        body: {
          command: `${t("poster.command")}: ${[spec.tag, spec.title, spec.edition].filter(Boolean).join(" ")}`,
          language: locale,
          data_classification: "public",
          poster: cleanSpec(spec),
        },
        headers: { "Idempotency-Key": newIdempotencyKey() },
      });
      setPosterJobs((ids) => [...ids, created.id]);
      setPosterMode(false);
      setJobId(created.id);
      await say(t("assistant.started"), signal);
    } catch (err) {
      await say(t("assistant.submitFailed", { message: toApiError(err).message }), signal);
    }
  };

  const closeDesign = async (signal: AbortSignal) => {
    setPosterMode(false);
    await say(t("posterStage.closed"), signal);
  };

  const propose = async (command: string, signal: AbortSignal) => {
    const text = command.trim().slice(0, 4000);
    if (text.length < 5) return say(t("assistant.tooShort"), signal);
    setPending(text);
    const poster = POSTER_WORDS.test(text);
    await say(t("assistant.confirm", { command: text }) + (poster ? ` ${t("assistant.posterOffer")}` : ""), signal);
    await awaitDecision(text, signal);
  };

  // Listen for "yes / no / design it" about the command waiting for confirmation.
  const awaitDecision = async (text: string, signal: AbortSignal) => {
    if (signal.aborted || !canListen()) return; // no microphone: the Confirm / Cancel buttons remain
    try {
      const action = confirmAction(await hear(signal), locale, text);
      if (action === "design") await designPoster(text, signal);
      else if (action === "submit") await submit(text, signal);
      else if (action === "cancel") {
        setPending(null);
        await say(t("assistant.cancelled"), signal);
      } else await say(t("assistant.unclear"), signal);
    } catch (e) {
      // Silence or a blocked microphone here is fine: the buttons are still on screen.
      if (!(e instanceof VoiceError) || e.code === "failed" || e.code === "network") await reportVoiceError(e, signal);
    }
  };

  // --- user actions ---------------------------------------------------------------------
  const welcomed = useRef(false);
  // Mira's welcome: greets you by first name, reviews the workforce, then says how to start.
  // Returns false when it was cut short.
  const welcome = async (signal: AbortSignal, closing: MessageKey): Promise<boolean> => {
    welcomed.current = true;
    setMode("thinking");
    // The briefing quotes the same snapshot the operations panel shows (a fresh one is shared by both),
    // so Mira never says different job counts or budget figures from the ones on screen.
    const [me, snapshot] = await Promise.all([
      api<Session>("/v1/auth/me", { signal }).catch(() => null),
      ops.get(15000),
    ]);
    if (signal.aborted) return false;
    const firstName = (me?.user.full_name ?? "").trim().split(/\s+/)[0] ?? "";
    await say(`${workforceBriefing(t, firstName, snapshot?.summary)} ${t(closing)}`.trim(), signal);
    return !signal.aborted;
  };
  // Opening Mira's screen: she says the welcome straight away when the browser lets the page talk
  // (you have already clicked in the app). Otherwise she says it on the first NEW CHAT press.
  const welcomeRef = useRef(welcome);
  useEffect(() => {
    welcomeRef.current = welcome;
  });
  useEffect(() => {
    const play = () => {
      if (welcomed.current) return;
      const signal = begin();
      void welcomeRef.current(signal, "brief.pressToStart").finally(() => end(signal));
    };
    const canTalk = typeof navigator !== "undefined" && navigator.userActivation?.hasBeenActive;
    if (canTalk) {
      const timer = setTimeout(play, 400);
      return () => clearTimeout(timer);
    }
    // After a fresh page load the browser keeps the page silent until you click or press a key: the
    // first click or key anywhere on the screen plays the welcome. (Pressing the talk button or typing
    // a command instead starts that straight away, with no welcome in between.)
    const onFirst = (e: Event) => {
      const el = e.target as HTMLElement | null;
      const busyElsewhere = el?.closest("[data-talk]") || el?.closest("input, textarea, select, form");
      const isSpace = e instanceof KeyboardEvent && e.code === "Space";
      if (!busyElsewhere && !isSpace) setTimeout(play, 0);
      else welcomed.current = true;
      document.removeEventListener("pointerdown", onFirst, true);
      document.removeEventListener("keydown", onFirst, true);
    };
    document.addEventListener("pointerdown", onFirst, true);
    document.addEventListener("keydown", onFirst, true);
    return () => {
      document.removeEventListener("pointerdown", onFirst, true);
      document.removeEventListener("keydown", onFirst, true);
    };
  }, []);
  const startChat = async () => {
    const signal = begin();
    if (posterMode) {
      // While designing, the microphone takes the next instruction for the poster.
      await listenForDesign(signal);
      end(signal);
      return;
    }
    setPending(null);
    setJobId(null);
    try {
      // One press: NEW CHAT always listens straight away. (The spoken welcome plays when the screen
      // opens; if the browser blocked it, it is not replayed here, so you never have to press twice.)
      welcomed.current = true;
      setBubble(t("assistant.greeting"));
      await propose(await hear(signal), signal);
    } catch (e) {
      await reportVoiceError(e, signal);
    } finally {
      end(signal);
    }
  };
  // Barge in: while Mira is talking, the button (or the space bar) stops her mid-sentence and listens
  // right away for whatever comes next here: a design change, "yes / no", or a new command.
  const interrupt = async () => {
    const signal = begin();
    try {
      if (posterMode) await listenForDesign(signal);
      else if (pending) await awaitDecision(pending, signal);
      else await propose(await hear(signal), signal);
    } catch (e) {
      await reportVoiceError(e, signal);
    } finally {
      end(signal);
    }
  };
  const talkButton = () => {
    if (mode === "speaking") return void interrupt();
    if (mode === "listening" || busy) return stop();
    return void startChat();
  };
  const talkRef = useRef(talkButton);
  useEffect(() => {
    talkRef.current = talkButton;
  });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
      const el = e.target as HTMLElement | null;
      const typing = el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
      // "/" jumps to the command box from anywhere on the page (except while typing somewhere).
      if (e.key === "/" && !typing) {
        e.preventDefault();
        composerRef.current?.focus();
        return;
      }
      if (e.code !== "Space") return;
      if (typing || el?.tagName === "BUTTON" || el?.tagName === "A") return;
      e.preventDefault();
      talkRef.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const sendTyped = async () => {
    const text = typed;
    const signal = begin();
    setTyped("");
    setFilled(false);
    if (posterMode) await converse(text, signal);
    else {
      setJobId(null);
      await propose(text, signal);
    }
    end(signal);
  };
  // Quick actions never start a paid job by themselves: a command card puts its text in the composer
  // (you add what it is for, then Send, then confirm); the poster card opens the free design on screen.
  const pickWorkflow = async (w: Workflow) => {
    if (posterMode) return;
    if (w.kind === "poster") {
      const signal = begin();
      await designPoster(t(w.command), signal);
      end(signal);
      return;
    }
    setTyped(t(w.command));
    setFilled(true);
    focusComposer();
  };
  const focusComposer = () => {
    const box = composerRef.current;
    if (!box) return;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    box.scrollIntoView({ block: "center", behavior: still ? "auto" : "smooth" });
    box.focus({ preventScroll: true });
    requestAnimationFrame(() => box.setSelectionRange(box.value.length, box.value.length));
  };
  const startSuggested = (id: WorkflowId) => void pickWorkflow(workflowById(id));
  // A failed job's command goes back into the composer; it is confirmed again before anything runs.
  const retryCommand = (command: string) => {
    if (posterMode) return;
    setTyped(command);
    setFilled(false);
    focusComposer();
  };
  // Show a job that is already running (started here, by n8n or elsewhere) on Mira's screen: the stage
  // switches to the live agent graph, exactly as for a job started from this page.
  const watchJob = (id: string) => {
    if (posterMode) return;
    setPending(null);
    setJobId(id);
  };
  const backToMira = () => setJobId(null);
  const createPosterNow = async () => {
    const signal = begin();
    await createPoster(signal);
    end(signal);
  };
  // Logos picked on Mira's screen go straight into the sponsor bar or the "Presented by" row; Mira
  // says what she added.
  const addLogos = async (
    logos: BrandAsset[],
    row: "art" | "heroL" | "heroR" | "event" | "game" | "venue" | "sponsors" | "presenters",
  ) => {
    if (!logos.length) return;
    const ids = logos.map((l) => l.id);
    const spec = posterSpecRef.current;
    const tournament = spec.template === "tournament";
    const next =
      row === "heroL"
        ? { ...spec, hero_left_id: ids[0] }
        : row === "heroR"
        ? { ...spec, hero_right_id: ids[0] }
        : row === "art"
        ? { ...spec, art_asset_id: ids[0], art: "", variants: 1 }
        : row === "event"
        ? withEventLogo(spec, ids[0])
        : row === "game"
          ? withGameLogo(spec, ids[0])
          : row === "venue"
            ? { ...spec, venue_photo_id: ids[0] }
            : row === "sponsors"
              ? withSponsors(spec, ids)
              : withPresenters(spec, ids);
    posterSpecRef.current = next;
    setPosterSpec(next);
    const names = logos.map((l) => l.name).join(", ");
    const text =
      row === "heroL" || row === "heroR"
        ? t(row === "heroL" ? "posterChat.heroLeftSet" : "posterChat.heroRightSet", { name: logos[0].name })
        : row === "art"
        ? t("posterChat.characterArtSet", { name: logos[0].name })
        : row === "event"
        ? t("posterChat.eventLogoSet", { name: logos[0].name })
        : row === "game"
          ? t("posterChat.gameLogoSet", { name: logos[0].name })
          : row === "venue"
            ? t("posterChat.venuePhotoSet", { name: logos[0].name })
            : row === "sponsors"
              ? t("posterChat.sponsorsAdded", { names })
              : t(tournament ? "posterChat.organisersAdded" : "posterChat.presentersAdded", { names });
    posterChat.note(text);
    const signal = begin();
    await say(text, signal);
    end(signal);
  };
  // Switch the layout on Mira's screen; the fixed tournament wording is filled where empty.
  const switchTemplate = async (template: PosterTemplate) => {
    if ((posterSpecRef.current.template ?? "esports") === template) return;
    const next = withTemplate(posterSpecRef.current, template, tournamentDefaults(t));
    posterSpecRef.current = next;
    setPosterSpec(next);
    const text = t(template === "tournament" ? "posterStage.switchedTournament" : "posterStage.switchedEsports");
    posterChat.note(text);
    const signal = begin();
    await say(text, signal);
    end(signal);
  };
  const closeDesignNow = async () => {
    const signal = begin();
    await closeDesign(signal);
    end(signal);
  };
  const continueOnPostersPage = () => {
    stop();
    saveHandoff({ spec: posterSpecRef.current, turns: posterChat.turns });
    router.push("/posters");
  };
  const confirmPending = async () => {
    if (!pending) return;
    const signal = begin();
    await submit(pending, signal);
    end(signal);
  };
  const designPending = async () => {
    if (!pending) return;
    const signal = begin();
    await designPoster(pending, signal);
    end(signal);
  };
  const cancelPending = async () => {
    const signal = begin();
    setPending(null);
    await say(t("assistant.cancelled"), signal);
    end(signal);
  };

  // --- announce the outcome once per job --------------------------------------------------
  const announce = async (finished: Job) => {
    const signal = begin();
    const text = t(`assistant.outcome.${finished.status}` as "assistant.outcome.completed") + (finished.demo_mode ? t("assistant.outcome.demo") : "");
    const success = finished.status === "completed" || finished.status === "awaiting_approval";
    const failure = finished.status === "failed" || finished.status === "partial";
    if (success) setCelebrate(true);
    if (failure) setSorry(true);
    void ops.refresh();
    await say(text, signal);
    end(signal);
    // hold the expression for a moment after she finishes speaking
    if ((success || failure) && !signal.aborted) {
      celebrateTimer.current = setTimeout(() => {
        setCelebrate(false);
        setSorry(false);
      }, failure ? 8000 : 6000);
    }
  };
  const announceRef = useRef(announce);
  useEffect(() => {
    announceRef.current = announce;
  });
  const announced = useRef<string | null>(null);
  useEffect(() => {
    if (!job || !SETTLED.includes(job.status) || announced.current === job.id) return;
    announced.current = job.id;
    void announceRef.current(job);
  }, [job]);
  useEffect(
    () => () => {
      session.current?.abort();
      clearTimeout(celebrateTimer.current);
    },
    [],
  );

  const progressCaption = mode !== "listening" && Boolean(job && !settled && mode !== "speaking");
  const caption =
    mode === "listening"
      ? heard || t("assistant.listening")
      : progressCaption && job
        ? t("assistant.working", { n: Math.round(job.progress) })
        : (bubble ?? t("assistant.idle"));

  // Announce the watched job's status to screen readers when it changes (with the progress at that moment).
  const [jobAnnouncement, setJobAnnouncement] = useState("");
  const jobStatusKey = job ? `${job.id}:${job.status}` : "";
  const jobRef = useRef(job);
  useEffect(() => {
    jobRef.current = job;
  });
  useEffect(() => {
    const j = jobRef.current;
    setJobAnnouncement(j ? t("assistant.jobStatus", { status: t(`status.${j.status}`), n: String(Math.round(j.progress)) }) : "");
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only on a status change, not every progress tick
  }, [jobStatusKey]);

  // Full screen: the whole assistant (stage and controls) fills the screen; Esc or the button leaves it.
  // Where the browser refuses real full screen (iPhone Safari, embedded views), it fills the window instead.
  const screenRef = useRef<HTMLDivElement>(null);
  const [nativeFull, setNativeFull] = useState(false);
  const [windowFull, setWindowFull] = useState(false);
  const fullScreen = nativeFull || windowFull;
  useEffect(() => {
    const onChange = () => setNativeFull(document.fullscreenElement !== null && document.fullscreenElement === screenRef.current);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setWindowFull(false);
    };
    document.addEventListener("fullscreenchange", onChange);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("fullscreenchange", onChange);
      window.removeEventListener("keydown", onKey);
    };
  }, []);
  const toggleFullScreen = async () => {
    if (fullScreen) {
      setWindowFull(false);
      if (document.fullscreenElement) await document.exitFullscreen().catch(() => undefined);
      return;
    }
    try {
      if (!document.fullscreenEnabled || !screenRef.current) throw new Error("unsupported");
      // Some embedded browsers neither grant nor refuse the request: give it a moment, then fall back.
      await Promise.race([
        screenRef.current.requestFullscreen(),
        new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), 1200)),
      ]);
      if (!document.fullscreenElement) throw new Error("not granted");
    } catch {
      setWindowFull(true); // refused or unsupported: fill the browser window instead
    }
  };

  // Once a command is accepted the stage swaps to the live agent graph: the caption moves to the
  // top, Mira steps aside to the corner and keeps talking, and the graph takes the main area.
  const graphStage = jobId !== null;
  const posterStage = posterMode && !graphStage;
  const sideStage = graphStage || posterStage;

  const snap = ops.snapshot;
  const s = snap?.summary;
  const firstRun = isFirstRun(s);
  const focus = layout === "focus";
  const live = liveJobs(snap);
  const waitingList = waitingJobs(snap);
  // Mira's size follows the layout: large in Focus, moderate beside the dashboard in Work.
  const stageSize = focus
    ? "h-[48vh] min-h-[300px] max-h-[680px] sm:h-[56vh]"
    : "h-[34vh] min-h-[240px] max-h-[460px] md:h-[40vh]";
  return (
    <div lang={locale} className={cx("assistant-page mx-auto", focus ? "max-w-4xl" : "max-w-7xl")}>
      <a
        href="#assistant-typed"
        onClick={(e) => {
          e.preventDefault();
          composerRef.current?.focus();
        }}
        className="sr-only focus:not-sr-only focus:mb-3 focus:inline-block focus:rounded-lg focus:bg-raised focus:px-3 focus:py-2 focus:text-sm focus:text-ink"
      >
        {t("assistant.jumpToComposer")}
      </a>
      <header className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold leading-tight text-ink">{t("assistant.name")}</h1>
          <p className="mt-1 text-base leading-relaxed text-dim">{t("assistant.subtitle")}</p>
        </div>
        <ModeToggle mode={layout} onChange={setLayout} />
      </header>
      {!online && (
        <p role="status" className="mb-4 rounded-xl border border-warn/50 bg-warn/10 p-3 text-sm leading-relaxed text-ink">
          {t("ops.offline")}
        </p>
      )}
      {/* Screen-reader updates for the job on Mira's screen: status changes only, not every percent. */}
      <p className="sr-only" aria-live="polite">
        {jobAnnouncement}
      </p>
      {focus && s && (
        <div className="mb-4">
          <FocusStrip waiting={s.awaiting_approval_jobs} live={live.length} onShowDashboard={() => setLayout("work")} />
        </div>
      )}
      {/* Work layout. Phone: Mira and the composer, then what needs attention, then quick actions.
          Tablet: the operations panels sit side by side. Desktop: they form a right-hand column. */}
      <div className={cx("grid gap-6", !focus && "lg:grid-cols-[minmax(0,1fr)_minmax(18rem,22rem)] lg:grid-rows-[auto_1fr]")}>
      <div className="min-w-0 lg:col-start-1 lg:row-start-1">
    <div
      ref={screenRef}
      className={cx(
        fullScreen && "flex h-full max-w-none flex-col overflow-y-auto bg-bg p-3 sm:p-5",
        windowFull && "fixed inset-0 z-[60]",
      )}
    >
      <div
        className={cx(
          "relative overflow-hidden rounded-3xl border border-line bg-gradient-to-b from-[#c9d6ea] via-[#aebfd9] to-[#8fa3c4]",
          fullScreen && "flex min-h-0 flex-1 flex-col",
        )}
      >
        <button
          type="button"
          onClick={() => void toggleFullScreen()}
          aria-pressed={fullScreen}
          aria-label={fullScreen ? t("assistant.exitFullscreen") : t("assistant.fullscreen")}
          title={fullScreen ? t("assistant.exitFullscreen") : t("assistant.fullscreen")}
          className={cx(
            "absolute z-30 flex size-9 items-center justify-center rounded-full bg-white/80 text-slate-800 shadow-sm backdrop-blur transition hover:bg-white focus-visible:outline-2 focus-visible:outline-cyan",
            sideStage ? "bottom-3 right-3" : "right-4 top-4",
          )}
        >
          <svg aria-hidden="true" viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            {fullScreen ? (
              <path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" />
            ) : (
              <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
            )}
          </svg>
        </button>
        {/* soft studio backdrop */}
        <div aria-hidden="true" className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_50%_20%,rgba(255,255,255,0.85),transparent_60%)]" />
        <div
          aria-hidden="true"
          className={cx(
            "pointer-events-none absolute left-1/2 top-[38%] size-[60%] max-w-[420px] -translate-x-1/2 -translate-y-1/2 rounded-full border-2 transition-opacity duration-500 aspect-square",
            mode === "listening" && !sideStage ? "border-cyan/70 opacity-100 assistant-ring" : "border-white/40 opacity-0",
          )}
        />
        {celebrate && <Sparkles corner={sideStage} size={stageSize} />}
        <AssistantAvatar
          mode={mode}
          happy={celebrate}
          sad={sorry}
          className={cx(
            sideStage
              ? "stage-swap absolute right-0 top-0 z-10 h-40 w-32 sm:h-56 sm:w-52"
              : fullScreen
                ? "relative min-h-[300px] w-full flex-1"
                : cx("relative w-full", stageSize),
          )}
        />

        <div className="absolute left-4 top-4 flex items-center gap-2 rounded-full bg-white/75 px-3 py-1 text-xs font-semibold text-slate-800 shadow-sm backdrop-blur">
          <span aria-hidden="true" className={cx("size-2 rounded-full", mode === "idle" ? "bg-slate-400" : "bg-emerald-500 animate-pulse")} />
          {t("assistant.name")} · {t(`assistant.mode.${mode}`)}
        </div>

        {/* caption bubble */}
        <div
          className={cx(
            "relative flex items-start gap-3 rounded-2xl border border-white/70 bg-white/90 p-4 text-slate-900 shadow-lg backdrop-blur",
            sideStage
              ? "stage-swap mb-3 ml-3 mr-32 mt-14 min-h-24 sm:ml-6 sm:mr-52"
              : fullScreen
                ? "mx-auto -mt-12 mb-6 w-[calc(100%-1.5rem)] max-w-3xl sm:mb-8 sm:text-lg"
                : "mx-3 -mt-10 mb-3 sm:mx-6 sm:mb-5",
          )}
        >
          <span
            aria-hidden="true"
            className={cx(
              "mt-0.5 flex size-10 shrink-0 items-center justify-center rounded-full text-white",
              mode === "listening" ? "bg-gradient-to-br from-fuchsia-500 to-blue-500 mic-live" : mode === "speaking" ? "bg-gradient-to-br from-blue-500 to-cyan-400" : "bg-slate-400",
            )}
          >
            <StatusIcon mode={mode} />
          </span>
          {/* Mira's words are announced; the "Working… n%" ticker is not (the status line above is). */}
          <p aria-live={progressCaption ? "off" : "polite"} className="min-w-0 flex-1 whitespace-pre-wrap text-base leading-relaxed sm:text-lg">
            {caption}
          </p>
        </div>

        {posterStage && (
          <div className={cx("stage-swap relative px-3 pb-3 sm:px-6 sm:pb-6", fullScreen && "flex min-h-0 flex-1 flex-col")}>
            <div className="mb-2 mr-32 flex min-h-9 flex-wrap items-center gap-2 text-sm font-semibold text-slate-800 sm:mr-52">
              {t("posterStage.title")}
              <span role="group" aria-label={t("poster.template")} className="ml-1 inline-flex overflow-hidden rounded-full border border-slate-300 bg-white/70 text-xs font-medium">
                {(["esports", "tournament"] as const).map((tpl) => (
                  <button
                    key={tpl}
                    type="button"
                    aria-pressed={(posterSpec.template ?? "esports") === tpl}
                    disabled={posterChat.busy}
                    onClick={() => void switchTemplate(tpl)}
                    className={cx(
                      "px-3 py-1 transition disabled:opacity-60",
                      (posterSpec.template ?? "esports") === tpl ? "bg-slate-800 text-white" : "text-slate-700 hover:bg-white",
                    )}
                  >
                    {t(tpl === "tournament" ? "posterStage.layoutTournament" : "posterStage.layoutEsports")}
                  </button>
                ))}
              </span>
            </div>
            <div
              className={cx(
                "grid gap-4 rounded-xl border border-line bg-[#050912] p-3 sm:p-4",
                fullScreen ? "min-h-0 flex-1 sm:grid-cols-[auto_minmax(0,1fr)]" : "sm:grid-cols-[minmax(0,15rem)_minmax(0,1fr)]",
              )}
            >
              <div
                className={cx(
                  "relative overflow-hidden rounded-lg border border-line bg-bg",
                  fullScreen ? "mx-auto h-full max-h-[calc(100dvh-16rem)] min-h-[320px] w-auto max-w-full" : "mx-auto w-full max-w-[15rem]",
                  posterAspect(posterSpec),
                )}
              >
                {posterPreview.url ? (
                  // eslint-disable-next-line @next/next/no-img-element -- local object URL of the rendered preview
                  <img
                    src={posterPreview.url}
                    alt={t("poster.previewAlt")}
                    className={cx("size-full object-cover transition-opacity", posterPreview.busy && "opacity-70")}
                  />
                ) : (
                  <p className="flex size-full items-center justify-center p-4 text-center text-sm text-dim">
                    {posterChat.busy ? t("posterChat.thinking") : t("poster.previewEmpty")}
                  </p>
                )}
                {posterPreview.url && !posterSpec.art_asset_id && (
                  <p className="absolute inset-x-2 bottom-2 rounded-md bg-black/70 px-2 py-1 text-center text-[11px] leading-snug text-white">
                    {t("posterStage.standIn")}
                  </p>
                )}
              </div>
              <div className="flex min-h-0 min-w-0 flex-col gap-3">
                <ChatBubbles turns={posterChat.turns} busy={posterChat.busy} className={cx("min-h-40 flex-1", !fullScreen && "max-h-72")} />
                <p className="text-xs text-dim">{t("posterStage.voiceHint")}</p>
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="primary"
                    disabled={!posterSpec.title.trim() || posterChat.busy}
                    onClick={createPosterNow}
                  >
                    {t("poster.create")}
                  </Button>
                  <LogoUploadButton
                    label={t("posterChat.addCharacterArt")}
                    multiple={false}
                    kind="artwork"
                    onUploaded={(art) => void addLogos(art, "art")}
                    disabled={posterChat.busy}
                  />
                  <LogoUploadButton
                    label={t("posterChat.addEventLogo")}
                    multiple={false}
                    onUploaded={(logos) => void addLogos(logos, "event")}
                    disabled={posterChat.busy}
                  />
                  <LogoUploadButton
                    label={t("posterChat.addSponsors")}
                    onUploaded={(logos) => void addLogos(logos, "sponsors")}
                    disabled={posterChat.busy}
                  />
                  <LogoUploadButton
                    label={posterSpec.template === "tournament" ? t("posterChat.addOrganisers") : t("posterChat.addPresenters")}
                    onUploaded={(logos) => void addLogos(logos, "presenters")}
                    disabled={posterChat.busy}
                  />
                  {posterSpec.template === "tournament" && (
                    <>
                      <LogoUploadButton
                        label={t("posterChat.addHeroLeft")}
                        multiple={false}
                        onUploaded={(imgs) => void addLogos(imgs, "heroL")}
                        disabled={posterChat.busy}
                      />
                      <LogoUploadButton
                        label={t("posterChat.addHeroRight")}
                        multiple={false}
                        onUploaded={(imgs) => void addLogos(imgs, "heroR")}
                        disabled={posterChat.busy}
                      />
                      <LogoUploadButton
                        label={t("posterChat.addGameLogo")}
                        multiple={false}
                        onUploaded={(logos) => void addLogos(logos, "game")}
                        disabled={posterChat.busy}
                      />
                      <LogoUploadButton
                        label={t("posterChat.addVenuePhoto")}
                        multiple={false}
                        kind="artwork"
                        onUploaded={(photos) => void addLogos(photos, "venue")}
                        disabled={posterChat.busy}
                      />
                    </>
                  )}
                  <Button onClick={continueOnPostersPage}>{t("posterStage.openFull")}</Button>
                  <Button variant="ghost" onClick={closeDesignNow}>
                    {t("posterStage.close")}
                  </Button>
                </div>
              </div>
            </div>
          </div>
        )}

        {graphStage && (
          <div className={cx("stage-swap relative px-3 pb-3 sm:px-6 sm:pb-6", fullScreen && "flex min-h-0 flex-1 flex-col")}>
            <div className="mb-2 mr-32 flex min-h-9 flex-wrap items-center gap-2 text-sm font-semibold text-slate-800 sm:mr-52">
              <h2 className="text-sm font-semibold">{t("graph.title")}</h2>
              {job && <JobStatusBadge status={job.status} />}
              {job?.demo_mode && <DemoBadge />}
              <span className="ml-auto flex flex-wrap gap-2">
                <button type="button" onClick={backToMira} className={buttonClass("ghost", "sm")}>
                  {t("graph.back")}
                </button>
                {job && (
                  <Link href={`/jobs/${job.id}`} className={buttonClass("secondary", "sm")}>
                    {t("assistant.openJob")}
                  </Link>
                )}
              </span>
            </div>
            {showPosterResult && (posterResult.data ?? []).length > 0 && (
              <div className="mb-3 flex flex-wrap items-end gap-3 rounded-xl border border-line bg-[#050912] p-3">
                {(posterResult.data ?? []).map((a) => (
                  <a key={a.id} href={fileUrl(a.download_url)} target="_blank" rel="noreferrer" className="block">
                    {/* eslint-disable-next-line @next/next/no-img-element -- signed, expiring artifact URL */}
                    <img src={fileUrl(a.download_url)} alt={a.filename} className="h-80 w-auto rounded-lg border border-line" />
                  </a>
                ))}
                <p className="text-sm text-ink">{t("posterStage.result")}</p>
              </div>
            )}
            {job ? (
              <AgentGraph job={job} tasks={monitor.data?.tasks ?? []} fill={fullScreen} qa={monitor.data?.qa} artifacts={monitor.data?.artifacts} />
            ) : (
              <div className="flex h-64 items-center justify-center rounded-xl border border-line bg-[#050912] text-sm text-dim">{t("common.loading")}</div>
            )}
          </div>
        )}
      </div>

      {/* controls: the primary action (talk to Mira) next to the typed command */}
      <div className={cx("mt-5 grid gap-4 md:grid-cols-[auto_1fr] md:items-start", fullScreen && "mx-auto w-full max-w-4xl")}>
        <div className="flex flex-col items-center gap-2 md:pt-2">
          {!fullScreen && <p className="text-sm font-semibold text-cyan">{t("assistant.startHere")}</p>}
          <button
            type="button"
            data-talk="1"
            onClick={talkButton}
            className={cx(
              "flex size-28 flex-col items-center justify-center gap-1 rounded-full text-sm font-bold text-white shadow-xl transition focus-visible:outline-offset-4 sm:size-32",
              mode === "speaking"
                ? "mic-live bg-gradient-to-br from-brand to-cyan hover:scale-105"
                : busy
                  ? "bg-gradient-to-br from-rose-500 to-orange-400"
                  : "bg-gradient-to-br from-brand to-cyan hover:scale-105",
            )}
          >
            <svg aria-hidden="true" viewBox="0 0 24 24" className="size-10" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              {busy && mode !== "speaking" ? <rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" /> : <path d="M9 6a3 3 0 0 1 6 0v6a3 3 0 0 1-6 0zM5 11a7 7 0 0 0 14 0M12 18v3" />}
            </svg>
            {mode === "speaking" ? t("assistant.talkNow") : busy ? t("assistant.stop") : t("assistant.newChat")}
          </button>
        </div>

        <div className="space-y-3">
          {pending && !busy && (
            <div className="rounded-2xl border border-warn/50 bg-warn/10 p-3 sm:p-4">
              <p className={cx("text-sm font-semibold text-warn", locale === "en" && "uppercase tracking-wide")}>{t("assistant.pendingLabel")}</p>
              <p className="mt-1 whitespace-pre-wrap text-base leading-relaxed text-ink">{pending}</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button variant="primary" onClick={confirmPending}>
                  {t("assistant.confirmBtn")}
                </Button>
                {POSTER_WORDS.test(pending) && (
                  <Button onClick={designPending}>{t("assistant.posterBtn")}</Button>
                )}
                <Button onClick={cancelPending}>{t("assistant.cancelBtn")}</Button>
              </div>
            </div>
          )}
          <CommandComposer
            id="assistant-typed"
            inputRef={composerRef}
            value={typed}
            onChange={(v) => {
              setTyped(v);
              if (!v.trim()) setFilled(false);
            }}
            onSubmit={() => void sendTyped()}
            minLength={posterMode ? 1 : 5}
            label={posterMode ? t("posterStage.title") : t("composer.label")}
            placeholder={posterMode ? t("assistant.typePlaceholder") : t("composer.placeholder")}
            note={filled ? t("composer.filled") : undefined}
          />
          <p className="text-sm leading-relaxed text-dim">{t("assistant.hint")}</p>
          <p className="hidden text-sm leading-relaxed text-dim md:block">{t("assistant.keys")}</p>
        </div>
      </div>
    </div>

      </div>

      {!focus && (
      <aside
        aria-label={t("ops.title")}
        className="grid gap-4 md:grid-cols-2 md:items-start lg:sticky lg:top-4 lg:col-start-2 lg:row-span-2 lg:row-start-1 lg:grid-cols-1 lg:self-start"
      >
        {firstRun && (
          <div className="md:col-span-2 lg:col-span-1">
            <StartGuide />
          </div>
        )}
        <OpsSummary
          summary={s}
          waiting={waitingList.slice(0, 3)}
          waitingTotal={s?.awaiting_approval_jobs}
          live={live}
          loading={ops.loading}
          error={ops.error}
          takenAt={snap?.at}
          onRetry={() => void ops.refresh()}
          onWatch={posterMode ? undefined : watchJob}
          watchingJobId={jobId}
        />
        <TaskSuggestions
          summary={s}
          onStart={startSuggested}
          disabled={posterMode}
          jobs={snap?.jobs}
          watchingJobId={jobId}
          onWatch={watchJob}
          onRetry={retryCommand}
        />
      </aside>
      )}

      {!fullScreen && !focus && (
        <div className="min-w-0 lg:col-start-1 lg:row-start-2">
          <QuickActions
            onPick={(w) => void pickWorkflow(w)}
            offline={s?.offline_queues}
            disabled={posterMode}
            disabledReason={t("qa.closeDesignFirst")}
          />
        </div>
      )}
      </div>
    </div>
  );
}

/** Twinkling stars around Mira while she celebrates. */
function Sparkles({ corner, size }: { corner: boolean; size: string }) {
  const spots = corner
    ? [
        [6, 10, 16, 0],
        [30, 4, 12, 0.5],
        [4, 44, 12, 0.9],
        [36, 52, 14, 0.3],
      ]
    : [
        [22, 16, 30, 0],
        [74, 12, 24, 0.4],
        [14, 44, 20, 0.8],
        [82, 40, 28, 0.2],
        [30, 66, 18, 1.1],
        [70, 64, 22, 0.6],
      ];
  return (
    <div
      aria-hidden="true"
      className={cx("pointer-events-none absolute z-20", corner ? "right-0 top-0 h-40 w-32 sm:h-56 sm:w-52" : cx("inset-x-0 top-0", size))}
    >
      {spots.map(([x, y, size, delay]) => (
        <svg
          key={`${x}-${y}`}
          viewBox="0 0 24 24"
          className="sparkle absolute"
          style={{ left: `${corner ? x * 2 : x}%`, top: `${y}%`, width: size, height: size, animationDelay: `${delay}s` }}
        >
          <path d="M12 1l2.6 8.4L23 12l-8.4 2.6L12 23l-2.6-8.4L1 12l8.4-2.6z" fill="#fff7c2" stroke="#fbbf24" strokeWidth="1" />
        </svg>
      ))}
    </div>
  );
}

function StatusIcon({ mode }: { mode: AvatarMode }) {
  const common = { viewBox: "0 0 24 24", className: "size-5", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round" } as const;
  if (mode === "speaking")
    return (
      <svg {...common}>
        <path d="M4 9v6h4l5 4V5L8 9zM16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11" />
      </svg>
    );
  if (mode === "listening")
    return (
      <svg {...common}>
        <path d="M9 6a3 3 0 0 1 6 0v6a3 3 0 0 1-6 0zM5 11a7 7 0 0 0 14 0M12 18v3" />
      </svg>
    );
  if (mode === "thinking")
    return (
      <svg {...common}>
        <path d="M6 12h.01M12 12h.01M18 12h.01" strokeWidth={3.5} />
      </svg>
    );
  return (
    <svg {...common}>
      <path d="M9 6a3 3 0 0 1 6 0v6a3 3 0 0 1-6 0zM5 11a7 7 0 0 0 14 0M12 18v3M4 4l16 16" />
    </svg>
  );
}
