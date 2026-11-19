"use client";

// Shared poster design logic for the Posters page and Mira's screen: the empty draft, the live
// preview (server-rendered, debounced) and the design chat with Mira.

import { useEffect, useRef, useState } from "react";
import type { MessageKey } from "@/i18n/dictionaries";
import { ApiError, api, toApiError } from "./api";
import type { BrandAsset, PosterSpec, PosterTemplate } from "./types";

export const MAX_LOGO_UPLOAD = 8 * 1024 * 1024;
export const MAX_ARTWORK_UPLOAD = 25 * 1024 * 1024;

/** Words that mean "a tournament / registration poster" (start Mira's design in the 4:5 layout). */
export const TOURNAMENT_WORDS =
  /ทัวร์นาเมนต์|รับสมัคร|เงินรางวัล|ชิงรางวัล|สมัครฟรี|tournament|registration|prize/i;
export const MAX_SPONSORS = 8;

/** Upload one logo or artwork to the library (multipart: the JSON api() helper does not apply). */
export async function uploadAsset(
  file: File,
  kind: BrandAsset["kind"] = "logo",
): Promise<BrandAsset> {
  const form = new FormData();
  form.append("file", file);
  form.append("kind", kind);
  form.append("name", file.name.replace(/\.[^.]+$/, ""));
  let res: Response;
  try {
    res = await fetch("/api/proxy/v1/assets", {
      method: "POST",
      body: form,
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch (e) {
    throw toApiError(e);
  }
  const body = await res.json().catch(() => undefined);
  if (!res.ok) {
    const err = body?.error;
    throw new ApiError(
      res.status,
      err?.code ?? `HTTP_${res.status}`,
      err?.message ?? res.statusText,
    );
  }
  return body as BrandAsset;
}

/** Upload logos (or artwork) and return them; stops at the first failure (reported via the thrown ApiError). */
export async function uploadLogos(
  files: FileList | File[],
  tooLarge: string,
  kind: BrandAsset["kind"] = "logo",
): Promise<BrandAsset[]> {
  const out: BrandAsset[] = [];
  const max = kind === "artwork" ? MAX_ARTWORK_UPLOAD : MAX_LOGO_UPLOAD;
  for (const file of Array.from(files)) {
    if (file.size > max) throw new ApiError(413, "FILE_TOO_LARGE", tooLarge);
    out.push(await uploadAsset(file, kind));
  }
  return out;
}

/** Add logos to the end of the sponsor bar (no duplicates, at most 8, never also as event/presenter logo). */
export function withSponsors(spec: PosterSpec, ids: string[]): PosterSpec {
  const taken = new Set([spec.event_logo_id, ...spec.presenter_logo_ids]);
  const sponsors = [
    ...spec.sponsor_logo_ids,
    ...ids.filter((id) => !taken.has(id)),
  ];
  return {
    ...spec,
    sponsor_logo_ids: [...new Set(sponsors)].slice(0, MAX_SPONSORS),
  };
}

export const MAX_PRESENTERS = 6;

/**
 * Add logos to the "Presented by" row (no duplicates, at most 6). A logo already in the sponsor bar
 * moves to the presenter row; the event logo is never repeated there.
 */
export function withPresenters(spec: PosterSpec, ids: string[]): PosterSpec {
  const fresh = ids.filter((id) => id !== spec.event_logo_id);
  const presenters = [...new Set([...spec.presenter_logo_ids, ...fresh])].slice(
    0,
    MAX_PRESENTERS,
  );
  const moved = new Set(presenters);
  return {
    ...spec,
    presenter_label:
      spec.presenter_label.trim() || EMPTY_POSTER.presenter_label,
    presenter_logo_ids: presenters,
    sponsor_logo_ids: spec.sponsor_logo_ids.filter((id) => !moved.has(id)),
  };
}

/** Use a logo as the event logo (it replaces the typeset title) and take it out of the logo rows. */
export function withEventLogo(spec: PosterSpec, id: string): PosterSpec {
  return {
    ...spec,
    event_logo_id: id,
    sponsor_logo_ids: spec.sponsor_logo_ids.filter((x) => x !== id),
    presenter_logo_ids: spec.presenter_logo_ids.filter((x) => x !== id),
  };
}

/** The tournament layout's game logo (centred over the art); never also in a logo row. */
export function withGameLogo(spec: PosterSpec, id: string): PosterSpec {
  return {
    ...spec,
    game_logo_id: id,
    event_logo_id: spec.event_logo_id === id ? null : spec.event_logo_id,
    sponsor_logo_ids: spec.sponsor_logo_ids.filter((x) => x !== id),
    presenter_logo_ids: spec.presenter_logo_ids.filter((x) => x !== id),
  };
}

/** The fixed tournament wording in the current language, for withTemplate. */
export function tournamentDefaults(t: (key: MessageKey) => string) {
  return {
    prize_label: t("tournament.default.prizeLabel"),
    badge_text: t("tournament.default.badge"),
    cta_text: t("tournament.default.cta"),
    col1: t("tournament.default.col1"),
    col2: t("tournament.default.col2"),
  };
}

export const EMPTY_POSTER: PosterSpec = {
  theme: "gold",
  tag: "",
  title: "",
  edition: "",
  date_text: "",
  headline: ["", ""],
  venue: "",
  time_text: "",
  art: "",
  art_asset_id: null,
  event_logo_id: null,
  sponsor_logo_ids: [],
  presenter_label: "Presented by",
  presenter_logo_ids: [],
  variants: 1,
};

/** The spec as the API wants it: blank headline, category and schedule lines dropped. */
export function cleanSpec(spec: PosterSpec): PosterSpec {
  const out: PosterSpec = {
    ...spec,
    headline: spec.headline.map((h) => h.trim()).filter(Boolean),
  };
  if (spec.category_lines)
    out.category_lines = spec.category_lines
      .map((c) => c.trim())
      .filter(Boolean);
  if (spec.schedule)
    out.schedule = spec.schedule
      .map((it) => ({
        label: it.label.trim(),
        big: it.big.trim(),
        lines: it.lines.map((l) => l.trim()).filter(Boolean),
      }))
      .filter((it) => it.label || it.big || it.lines.length);
  return out;
}

/** Aspect ratio of the finished poster: 9:16 for esports, 4:5 for tournament. */
export function posterAspect(spec: PosterSpec): string {
  return spec.template === "tournament" ? "aspect-[4/5]" : "aspect-[9/16]";
}

/**
 * Switch layouts. Moving to the tournament layout fills the usual fixed wording (only where empty),
 * so the preview is useful straight away; every line stays editable.
 */
export function withTemplate(
  spec: PosterSpec,
  template: PosterTemplate,
  defaults: {
    prize_label: string;
    badge_text: string;
    cta_text: string;
    col1: string;
    col2: string;
  },
): PosterSpec {
  if (template !== "tournament") return { ...spec, template };
  const schedule = spec.schedule?.length
    ? spec.schedule
    : [
        { label: defaults.col1, big: "", lines: [] },
        { label: defaults.col2, big: "", lines: [] },
      ];
  return {
    ...spec,
    template,
    prize_label: spec.prize_label || defaults.prize_label,
    badge_text: spec.badge_text || defaults.badge_text,
    cta_text: spec.cta_text || defaults.cta_text,
    schedule,
  };
}

/** Merge a draft returned by Mira into the form (the form always shows at least two headline inputs). */
export function mergeDraft(
  current: PosterSpec,
  draft: Partial<PosterSpec>,
): PosterSpec {
  const next = { ...current, ...draft };
  const lines = draft.headline ?? current.headline;
  next.headline =
    lines.length >= 2 ? lines : [...lines, ...Array(2 - lines.length).fill("")];
  return next;
}

const PREVIEW_DELAY_MS = 450;

/**
 * Live preview: the server typesets the layout exactly as the real job will (same fonts, Thai shaping,
 * logos) over stand-in artwork, or your own artwork. Debounced while typing; stale requests cancelled.
 */
export function usePosterPreview(spec: PosterSpec) {
  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const payload = JSON.stringify(cleanSpec(spec));
  const hasTitle = spec.title.trim().length > 0;

  useEffect(() => {
    if (!hasTitle) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setBusy(true);
      try {
        const res = await fetch("/api/proxy/v1/posters/preview", {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "image/jpeg" },
          body: payload,
          signal: controller.signal,
          cache: "no-store",
          credentials: "same-origin",
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const next = URL.createObjectURL(await res.blob());
        setUrl((prev) => {
          if (prev) URL.revokeObjectURL(prev);
          return next;
        });
        setFailed(false);
      } catch (e) {
        if (!(e instanceof DOMException && e.name === "AbortError"))
          setFailed(true);
      } finally {
        if (!controller.signal.aborted) setBusy(false);
      }
    }, PREVIEW_DELAY_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [payload, hasTitle]);

  // Free the last image when the component goes away.
  const lastUrl = useRef<string | null>(null);
  useEffect(() => {
    lastUrl.current = url;
  }, [url]);
  useEffect(
    () => () => {
      if (lastUrl.current) URL.revokeObjectURL(lastUrl.current);
    },
    [],
  );

  return { url: hasTitle ? url : null, busy, failed, hasTitle };
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface PosterChatResponse {
  reply: string;
  draft: Partial<PosterSpec>;
  ready: boolean;
  unknown_assets: string[];
}

/**
 * The design conversation with Mira. `send` posts the conversation and the current draft; the reply's
 * draft is handed to `onDraft`. Returns the response, or null on error (the error is kept in `error`).
 */
export function usePosterChat(opts: {
  language: "th" | "en";
  getSpec: () => PosterSpec;
  onDraft: (draft: Partial<PosterSpec>) => void;
  unknownNote: (names: string) => string;
  initialTurns?: ChatTurn[];
}) {
  const [turns, setTurns] = useState<ChatTurn[]>(opts.initialTurns ?? []);
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const turnsRef = useRef(turns);
  const optsRef = useRef(opts);
  useEffect(() => {
    turnsRef.current = turns;
    optsRef.current = opts;
  });

  const send = async (text: string): Promise<PosterChatResponse | null> => {
    const content = text.trim().slice(0, 1500);
    if (!content) return null;
    const { language, getSpec, onDraft, unknownNote } = optsRef.current;
    const next = [
      ...turnsRef.current,
      { role: "user" as const, content },
    ].slice(-30);
    turnsRef.current = next;
    setTurns(next);
    setError(null);
    setBusy(true);
    try {
      const res = await api<PosterChatResponse>("/v1/posters/chat", {
        method: "POST",
        body: { messages: next, draft: getSpec(), language },
      });
      onDraft(res.draft);
      setReady(res.ready);
      const reply =
        res.reply +
        (res.unknown_assets.length
          ? `\n${unknownNote(res.unknown_assets.join(", "))}`
          : "");
      const withReply = [
        ...next,
        { role: "assistant" as const, content: reply },
      ];
      turnsRef.current = withReply;
      setTurns(withReply);
      return res;
    } catch (e) {
      setError(toApiError(e));
      return null;
    } finally {
      setBusy(false);
    }
  };

  /** Add a message from Mira that did not come from the model (e.g. "I added these logos"). */
  const note = (content: string) => {
    const withNote = [
      ...turnsRef.current,
      { role: "assistant" as const, content },
    ].slice(-30);
    turnsRef.current = withNote;
    setTurns(withNote);
  };

  const reset = () => {
    turnsRef.current = [];
    setTurns([]);
    setReady(false);
    setError(null);
  };

  return { turns, busy, ready, error, send, note, reset };
}

/** Hand-off between Mira's screen and the Posters page (same browser tab only). */
const HANDOFF_KEY = "mcr.posterHandoff";

export function saveHandoff(data: {
  spec: PosterSpec;
  turns: ChatTurn[];
}): void {
  try {
    sessionStorage.setItem(HANDOFF_KEY, JSON.stringify(data));
  } catch {
    // storage blocked: the Posters page simply starts empty
  }
}

export function takeHandoff(): { spec: PosterSpec; turns: ChatTurn[] } | null {
  try {
    const raw = sessionStorage.getItem(HANDOFF_KEY);
    if (!raw) return null;
    sessionStorage.removeItem(HANDOFF_KEY);
    const data = JSON.parse(raw) as { spec?: PosterSpec; turns?: ChatTurn[] };
    if (!data.spec || !Array.isArray(data.turns)) return null;
    return {
      spec: mergeDraft(EMPTY_POSTER, data.spec),
      turns: data.turns.slice(-30),
    };
  } catch {
    return null;
  }
}
