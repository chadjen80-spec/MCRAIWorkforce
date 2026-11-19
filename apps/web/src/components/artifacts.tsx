"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useI18n } from "@/i18n/I18nProvider";
import { api, fileUrl, toApiError } from "@/lib/api";
import { saveHandoff } from "@/lib/poster";
import type { Artifact, ItemList, Job, PosterSpec } from "@/lib/types";
import { Badge, Button, DemoBadge, buttonClass, cx } from "./ui";

function CopyButton({ text }: { text: string }) {
  const { t } = useI18n();
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => () => clearTimeout(timer.current), []);

  const copy = async () => {
    let ok = false;
    try {
      await navigator.clipboard.writeText(text);
      ok = true;
    } catch {
      ok = false;
    }
    setState(ok ? "copied" : "failed");
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), 2000);
  };

  return (
    <Button size="sm" onClick={copy}>
      <span aria-live="polite">
        {state === "copied" ? t("common.copied") : state === "failed" ? t("common.copyFailed") : t("common.copy")}
      </span>
    </Button>
  );
}

function Lightbox({ src, alt, onClose }: { src: string; alt: string; onClose: () => void }) {
  const { t } = useI18n();
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
      previous?.focus();
    };
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={alt}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-4"
      onClick={onClose}
    >
      <button
        ref={closeRef}
        type="button"
        onClick={onClose}
        className="absolute right-4 top-4 rounded-lg border border-line bg-surface px-3 py-1.5 text-sm text-ink"
      >
        {t("common.close")}
      </button>
      {/* eslint-disable-next-line @next/next/no-img-element -- signed, expiring artifact URL; not optimizable */}
      <img src={src} alt={alt} className="max-h-full max-w-full rounded-lg object-contain" onClick={(e) => e.stopPropagation()} />
    </div>
  );
}

function FileGlyph({ label }: { label: string }) {
  return (
    <div className="flex aspect-video items-center justify-center rounded-lg border border-line bg-bg">
      <span className="rounded-md border border-line bg-raised px-3 py-1.5 font-mono text-sm font-semibold uppercase tracking-wider text-brand-soft">
        {label}
      </span>
    </div>
  );
}

const GLYPHS: Partial<Record<Artifact["kind"], string>> = {
  document_pdf: "PDF",
  document_docx: "DOCX",
  subtitle: "SRT",
  manifest: "JSON",
  qa_report: "QA",
};

const EDITABLE_TYPES = ["text/plain", "text/markdown", "application/x-subrip"];
const REGEN_POLL_MS = 3000;
const REGEN_GIVE_UP_MS = 10 * 60 * 1000;

type RegenState = { phase: "idle" } | { phase: "form" } | { phase: "working" } | { phase: "done" } | { phase: "error"; message: string };

const DOCUMENT_KINDS: Artifact["kind"][] = ["document_pdf", "document_docx"];

/**
 * "Regenerate" for an image or a document: asks the server for a new version, then watches the
 * job's file list until the file of the same kind under the same task is replaced (success) or
 * reports an error (old file kept). A document's PDF and DOCX are regenerated together, so the
 * other card picks up the "working" state from the server when the list refreshes.
 */
function useRegenerate(artifact: Artifact, onChanged?: () => void) {
  const { t } = useI18n();
  const meta = (artifact.metadata ?? {}) as Record<string, unknown>;
  const [localState, setState] = useState<RegenState>({ phase: "idle" });
  const state: RegenState = localState.phase === "idle" && meta.regenerating_since ? { phase: "working" } : localState;
  const [concept, setConcept] = useState("");
  const onChangedRef = useRef(onChanged);
  useEffect(() => {
    onChangedRef.current = onChanged;
  });

  useEffect(() => {
    if (state.phase !== "working") return;
    let stopped = false;
    const started = Date.now();
    const poll = async () => {
      if (stopped) return;
      try {
        const list = await api<ItemList<Artifact>>(`/v1/jobs/${artifact.job_id}/artifacts`);
        const current = list.items.filter((a) => a.kind === artifact.kind && a.task_key === artifact.task_key);
        const replaced = current.length > 0 && !current.some((a) => a.id === artifact.id);
        const self = current.find((a) => a.id === artifact.id);
        const selfMeta = (self?.metadata ?? {}) as Record<string, unknown>;
        if (replaced) {
          if (onChangedRef.current) onChangedRef.current();
          else setState({ phase: "done" });
          return;
        }
        if (self && !selfMeta.regenerating_since && typeof selfMeta.regenerate_error === "string") {
          setState({ phase: "error", message: t("regen.failed", { message: selfMeta.regenerate_error }) });
          return;
        }
      } catch {
        // transient network error: keep waiting
      }
      if (Date.now() - started > REGEN_GIVE_UP_MS) return setState({ phase: "error", message: t("regen.timeout") });
      timer = setTimeout(poll, REGEN_POLL_MS);
    };
    let timer = setTimeout(poll, REGEN_POLL_MS);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [state.phase, artifact.id, artifact.job_id, artifact.task_key, artifact.kind, t]);

  const start = async () => {
    setState({ phase: "working" });
    try {
      const text = concept.trim() || null;
      await api(`/v1/artifacts/${artifact.id}/regenerate`, {
        method: "POST",
        body: artifact.kind === "image" ? { concept: text } : { instruction: text },
      });
      onChangedRef.current?.(); // refresh so the document's other file shows it is being regenerated
    } catch (e) {
      setState({ phase: "error", message: t("regen.failed", { message: toApiError(e).message }) });
    }
  };
  return { state, setState, concept, setConcept, start };
}

function isEditable(a: Artifact): boolean {
  return EDITABLE_TYPES.includes(a.content_type.split(";")[0].trim());
}

export function ArtifactCard({
  artifact,
  showJobLink = false,
  onChanged,
}: {
  artifact: Artifact;
  showJobLink?: boolean;
  onChanged?: () => void;
}) {
  const { t, tx, bytes, dateTime } = useI18n();
  const [enlarged, setEnlarged] = useState(false);
  const [mediaFailed, setMediaFailed] = useState(false);
  const url = fileUrl(artifact.download_url);
  const kindLabel = tx("kind", artifact.kind);
  // After a save, show the new text straight away instead of waiting for the list to refresh.
  const [savedText, setSavedText] = useState<string | null>(null);
  const text = savedText ?? artifact.text_preview;
  const edited = savedText !== null || artifact.metadata?.edited === true;

  const [draft, setDraft] = useState<string | null>(null);
  const [loadingDraft, setLoadingDraft] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const canEdit = isEditable(artifact);
  const regen = useRegenerate(artifact, onChanged);
  const isDocument = DOCUMENT_KINDS.includes(artifact.kind);
  const isVideo = artifact.kind === "video";
  const isStoryboard = artifact.kind === "storyboard";
  const isScript = artifact.kind === "script";
  const isSubtitle = artifact.kind === "subtitle";
  const isAudio = artifact.kind === "audio";
  // A poster made from library artwork (your own, or kept from an AI poster) would come out the same
  // again, so it has no "new image" button.
  const ownArtwork = artifact.metadata?.artwork === "uploaded" || artifact.metadata?.artwork === "reused";
  // Every poster keeps its artwork in the library: "Edit poster" opens it on the Posters page with the
  // same artwork, so text, logos and the QR code can change without paying for a new image.
  const router = useRouter();
  const keptArtwork = typeof artifact.metadata?.artwork_asset_id === "string" ? artifact.metadata.artwork_asset_id : null;
  const canEditPoster = artifact.kind === "image" && Boolean(artifact.metadata?.poster) && keptArtwork !== null;
  const [openingPoster, setOpeningPoster] = useState(false);
  const [posterError, setPosterError] = useState<string | null>(null);
  const editPoster = async () => {
    setOpeningPoster(true);
    setPosterError(null);
    try {
      const job = await api<Job & { options?: { poster?: PosterSpec } | null }>(`/v1/jobs/${artifact.job_id}`);
      const spec = job.options?.poster;
      if (!spec) throw new Error(t("posterEdit.noSpec"));
      saveHandoff({ spec: { ...spec, art_asset_id: keptArtwork, variants: 1 }, turns: [] });
      router.push("/posters");
    } catch (e) {
      setPosterError(t("posterEdit.failed", { message: toApiError(e).message }));
      setOpeningPoster(false);
    }
  };
  const canRegenerate = (artifact.kind === "image" && !ownArtwork) || isDocument || isVideo || isStoryboard || isScript || isSubtitle || isAudio;
  // Wording of the regenerate form per kind of file.
  const rx = isAudio
    ? { title: "voRegen.title", label: "vidRegen.label", placeholder: "vidRegen.placeholder", note: "voRegen.note", button: "voRegen.button" } as const
    : isSubtitle
    ? { title: "stRegen.title", label: "vidRegen.label", placeholder: "vidRegen.placeholder", note: "stRegen.note", button: "stRegen.button" } as const
    : isScript
    ? { title: "scRegen.title", label: "vidRegen.label", placeholder: "vidRegen.placeholder", note: "scRegen.note", button: "scRegen.button" } as const
    : isStoryboard
    ? { title: "sbRegen.title", label: "vidRegen.label", placeholder: "vidRegen.placeholder", note: "sbRegen.note", button: "sbRegen.button" } as const
    : isVideo
    ? { title: "vidRegen.title", label: "vidRegen.label", placeholder: "vidRegen.placeholder", note: "vidRegen.note", button: "vidRegen.button" } as const
    : isDocument
      ? { title: "docRegen.title", label: "docRegen.label", placeholder: "docRegen.placeholder", note: "docRegen.note", button: "docRegen.button" } as const
      : { title: "regen.title", label: "regen.sceneLabel", placeholder: "regen.scenePlaceholder", note: "regen.replaceNote", button: "regen.button" } as const;

  // Captions: the server writes a new version straight away (a few seconds), so no polling is needed.
  const canRewrite = artifact.kind === "caption";
  const [rewrite, setRewrite] = useState<"idle" | "form" | "working">("idle");
  const [instruction, setInstruction] = useState("");
  const [rewriteError, setRewriteError] = useState<string | null>(null);
  const [rewriteWarnings, setRewriteWarnings] = useState<string[]>([]);
  const latestOnChanged = useRef(onChanged);
  useEffect(() => {
    latestOnChanged.current = onChanged;
  });
  const startRewrite = async () => {
    setRewrite("working");
    setRewriteError(null);
    setRewriteWarnings([]);
    try {
      const res = await api<{ artifact: Artifact; warnings: string[] }>(`/v1/artifacts/${artifact.id}/regenerate-caption`, {
        method: "POST",
        body: { instruction: instruction.trim() || null },
      });
      setSavedText(res.artifact.text_preview ?? "");
      setRewriteWarnings(res.warnings);
      setInstruction("");
      setRewrite("idle");
      onChanged?.();
      // QA re-checks the job in the background; refresh again once it has had time to finish.
      setTimeout(() => latestOnChanged.current?.(), 10000);
    } catch (e) {
      setRewriteError(t("capRegen.failed", { message: toApiError(e).message }));
      setRewrite("idle");
    }
  };

  const startEdit = async () => {
    setEditError(null);
    // The preview stops at 4,000 characters; load the whole file for longer texts.
    if (text !== null && text.length < 4000) return setDraft(text);
    setLoadingDraft(true);
    try {
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setDraft(await res.text());
    } catch (e) {
      setEditError(t("deliverables.editLoadFailed", { message: e instanceof Error ? e.message : String(e) }));
    }
    setLoadingDraft(false);
  };
  const saveEdit = async () => {
    if (draft === null || draft.trim() === "") return;
    setSaving(true);
    setEditError(null);
    try {
      const updated = await api<Artifact>(`/v1/artifacts/${artifact.id}/text`, { method: "PUT", body: { text: draft } });
      setSavedText(updated.text_preview ?? draft);
      setDraft(null);
      onChanged?.();
    } catch (e) {
      setEditError(t("deliverables.editFailed", { message: toApiError(e).message }));
    }
    setSaving(false);
  };

  let preview: React.ReactNode;
  if (mediaFailed) {
    preview = (
      <p role="alert" className="rounded-lg border border-bad/40 bg-bad/10 px-3 py-6 text-center text-sm text-ink">
        {t("deliverables.mediaError")}
      </p>
    );
  } else if (artifact.kind === "image") {
    preview = (
      <div className="relative">
      <button
        type="button"
        onClick={() => setEnlarged(true)}
        aria-label={`${t("deliverables.enlarge")}: ${artifact.filename}`}
        className="block w-full cursor-zoom-in overflow-hidden rounded-lg border border-line bg-bg"
      >
        {/* eslint-disable-next-line @next/next/no-img-element -- signed, expiring artifact URL; not optimizable */}
        <img
          src={url}
          alt={artifact.filename}
          loading="lazy"
          onError={() => setMediaFailed(true)}
          className="aspect-video w-full object-contain"
        />
      </button>
        {regen.state.phase === "working" && (
          <div role="status" className="absolute inset-0 flex flex-col items-center justify-center gap-2 rounded-lg bg-bg/80 text-sm text-ink backdrop-blur-sm">
            <span aria-hidden="true" className="size-6 animate-spin rounded-full border-2 border-cyan border-t-transparent" />
            {t("regen.working")}
          </div>
        )}
      </div>
    );
  } else if ((isVideo || isStoryboard || isScript || isSubtitle || isAudio) && regen.state.phase === "working") {
    preview = (
      <div role="status" className="flex aspect-video flex-col items-center justify-center gap-2 rounded-lg border border-line bg-bg px-3 text-center text-sm text-ink">
        <span aria-hidden="true" className="size-6 animate-spin rounded-full border-2 border-cyan border-t-transparent" />
        {t("vidRegen.working")}
      </div>
    );
  } else if (artifact.kind === "video") {
    preview = (
      <video
        controls
        preload="metadata"
        src={url}
        onError={() => setMediaFailed(true)}
        className="aspect-video w-full rounded-lg border border-line bg-black"
      />
    );
  } else if (artifact.kind === "audio") {
    preview = <audio controls preload="none" src={url} onError={() => setMediaFailed(true)} className="w-full" />;
  } else if (isDocument && regen.state.phase === "working") {
    preview = (
      <div role="status" className="flex aspect-video flex-col items-center justify-center gap-2 rounded-lg border border-line bg-bg text-sm text-ink">
        <span aria-hidden="true" className="size-6 animate-spin rounded-full border-2 border-cyan border-t-transparent" />
        {t("docRegen.working")}
      </div>
    );
  } else if (rewrite === "working") {
    preview = (
      <div role="status" className="flex min-h-32 flex-col items-center justify-center gap-2 rounded-lg border border-line bg-bg p-3 text-sm text-ink">
        <span aria-hidden="true" className="size-6 animate-spin rounded-full border-2 border-cyan border-t-transparent" />
        {t("capRegen.working")}
      </div>
    );
  } else if (draft !== null) {
    preview = (
      <div>
        <label htmlFor={`edit-${artifact.id}`} className="sr-only">
          {t("deliverables.editLabel", { name: artifact.filename })}
        </label>
        <textarea
          id={`edit-${artifact.id}`}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          maxLength={20000}
          rows={10}
          autoFocus
          className="input min-h-56 resize-y font-sans leading-relaxed"
        />
        <p className="hint text-right tabular-nums">{draft.length}/20000</p>
      </div>
    );
  } else if (text !== null && text !== "") {
    preview = (
      <div>
        <pre className="max-h-56 overflow-y-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-bg p-3 font-sans text-sm leading-relaxed text-ink">
          {text}
        </pre>
        {text.length >= 4000 && <p className="hint">{t("deliverables.previewTruncated")}</p>}
      </div>
    );
  } else {
    preview = <FileGlyph label={GLYPHS[artifact.kind] ?? artifact.filename.split(".").pop() ?? kindLabel} />;
  }

  return (
    <li
      className={cx(
        "flex min-w-0 flex-col gap-3 rounded-xl border bg-surface p-3",
        artifact.is_demo ? "border-warn/60" : "border-line",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="info">{kindLabel}</Badge>
        {artifact.is_demo && <DemoBadge />}
        {artifact.released === false && <Badge tone="warn">{t("deliverables.draft")}</Badge>}
        {edited && <Badge tone="neutral">{t("deliverables.edited")}</Badge>}
        <span className="ml-auto text-xs text-dim">{bytes(artifact.size_bytes)}</span>
      </div>
      {preview}
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-ink" title={artifact.filename}>
          {artifact.filename}
        </p>
        <p className="text-xs text-dim">
          {tx("agent", artifact.agent)} · {dateTime(artifact.created_at)}
        </p>
      </div>
      {canRegenerate && regen.state.phase === "form" && (
        <div className="space-y-2 rounded-lg border border-line bg-bg p-3">
          <p className="text-sm font-medium text-ink">{t(rx.title)}</p>
          {/* A new voiceover reads the same script again, so there is nothing to instruct. */}
          {!isAudio && (
            <>
              <label htmlFor={`regen-${artifact.id}`} className="label">
                {t(rx.label)}
              </label>
              <textarea
                id={`regen-${artifact.id}`}
                value={regen.concept}
                onChange={(e) => regen.setConcept(e.target.value)}
                maxLength={500}
                rows={3}
                placeholder={t(rx.placeholder)}
                className="input resize-y"
              />
            </>
          )}
          {artifact.kind === "image" && <p className="hint">{t("regen.sceneHint")}</p>}
          <p className="text-xs text-warn">{t(rx.note)}</p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="primary" onClick={regen.start}>
              {t("regen.start")}
            </Button>
            <Button size="sm" onClick={() => regen.setState({ phase: "idle" })}>
              {t("common.cancel")}
            </Button>
          </div>
        </div>
      )}
      {canRewrite && rewrite === "form" && (
        <div className="space-y-2 rounded-lg border border-line bg-bg p-3">
          <p className="text-sm font-medium text-ink">{t("capRegen.title")}</p>
          <label htmlFor={`rewrite-${artifact.id}`} className="label">
            {t("capRegen.label")}
          </label>
          <textarea
            id={`rewrite-${artifact.id}`}
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            maxLength={500}
            rows={2}
            placeholder={t("capRegen.placeholder")}
            className="input resize-y"
          />
          <p className="text-xs text-warn">{t("capRegen.note")}</p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="primary" onClick={startRewrite}>
              {t("capRegen.start")}
            </Button>
            <Button size="sm" onClick={() => setRewrite("idle")}>
              {t("common.cancel")}
            </Button>
          </div>
        </div>
      )}
      {rewriteError && (
        <p role="alert" className="text-xs text-bad">
          {rewriteError}
        </p>
      )}
      {rewriteWarnings.length > 0 && (
        <div role="status" className="rounded-lg border border-warn/50 bg-warn/10 p-2 text-xs text-ink">
          <p className="font-medium text-warn">{t("capRegen.warnings")}</p>
          <ul className="ml-4 list-disc">
            {rewriteWarnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      )}
      {regen.state.phase === "error" && (
        <p role="alert" className="text-xs text-bad">
          {regen.state.message}
        </p>
      )}
      {regen.state.phase === "done" && (
        <p role="status" className="text-xs text-ok">
          {t("regen.doneRefresh")}
        </p>
      )}
      {editError && (
        <p role="alert" className="text-xs text-bad">
          {editError}
        </p>
      )}
      {draft !== null ? (
        <div className="mt-auto flex flex-wrap gap-2">
          <Button size="sm" variant="primary" loading={saving} disabled={draft.trim() === ""} onClick={saveEdit}>
            {saving ? t("common.saving") : t("common.save")}
          </Button>
          <Button size="sm" onClick={() => setDraft(null)} disabled={saving}>
            {t("common.cancel")}
          </Button>
        </div>
      ) : (
      <div className="mt-auto flex flex-wrap gap-2">
        <a href={url} download={artifact.filename} className={buttonClass("secondary", "sm")}>
          {t("common.download")}
        </a>
        {text !== null && text !== "" && <CopyButton text={text} />}
        {canRegenerate && (regen.state.phase === "idle" || regen.state.phase === "error") && (
          <Button size="sm" onClick={() => regen.setState({ phase: "form" })}>
            {t(rx.button)}
          </Button>
        )}
        {canEditPoster && (
          <Button size="sm" loading={openingPoster} onClick={() => void editPoster()} title={t("posterEdit.hint")}>
            {t("posterEdit.button")}
          </Button>
        )}
        {canRewrite && rewrite === "idle" && (
          <Button size="sm" onClick={() => setRewrite("form")}>
            {t("capRegen.button")}
          </Button>
        )}
        {canEdit && (
          <Button size="sm" loading={loadingDraft} onClick={startEdit} aria-label={t("deliverables.editLabel", { name: artifact.filename })}>
            {t("common.edit")}
          </Button>
        )}
        {showJobLink && (
          <Link href={`/jobs/${artifact.job_id}`} className={buttonClass("ghost", "sm")}>
            {t("deliverables.openJob")}
          </Link>
        )}
        {posterError && (
          <p role="alert" className="w-full text-xs text-bad">
            {posterError}
          </p>
        )}
      </div>
      )}
      {enlarged && <Lightbox src={url} alt={artifact.filename} onClose={() => setEnlarged(false)} />}
    </li>
  );
}

export function ArtifactGrid({
  artifacts,
  showJobLink = false,
  onChanged,
}: {
  artifacts: Artifact[];
  showJobLink?: boolean;
  onChanged?: () => void;
}) {
  return (
    <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {artifacts.map((a) => (
        <ArtifactCard key={a.id} artifact={a} showJobLink={showJobLink} onChanged={onChanged} />
      ))}
    </ul>
  );
}
