"use client";

import { useEffect, useRef, useState } from "react";
import { Button, ErrorState, SectionTitle, Spinner, cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { LogoUploadButton } from "@/components/LogoUploadButton";
import {
  type ChatTurn,
  usePosterChat,
  withEventLogo,
  withGameLogo,
  withPresenters,
  withSponsors,
} from "@/lib/poster";
import type { BrandAsset, PosterSpec } from "@/lib/types";
import { useSpeechInput } from "@/lib/useSpeechInput";
import { speak } from "@/lib/voice";

/** The conversation as chat bubbles (used on the Posters page and on Mira's screen). */
export function ChatBubbles({
  turns,
  busy,
  className,
}: {
  turns: ChatTurn[];
  busy: boolean;
  className?: string;
}) {
  const { t } = useI18n();
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    list.current?.scrollTo({
      top: list.current.scrollHeight,
      behavior: "smooth",
    });
  }, [turns, busy]);
  return (
    <div
      ref={list}
      aria-live="polite"
      className={cx(
        "space-y-2 overflow-y-auto rounded-xl border border-line bg-bg p-3",
        className,
      )}
    >
      {turns.length === 0 && !busy ? (
        <p className="text-sm text-dim">{t("posterChat.intro")}</p>
      ) : (
        turns.map((m, i) => (
          <div
            key={i}
            className={cx(
              "flex",
              m.role === "user" ? "justify-end" : "justify-start",
            )}
          >
            <p
              className={cx(
                "max-w-[85%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-sm",
                m.role === "user"
                  ? "bg-brand/25 text-ink"
                  : "border border-line bg-raised text-ink",
              )}
            >
              {m.role === "assistant" && (
                <span className="mb-0.5 block text-xs font-semibold text-cyan">
                  Mira
                </span>
              )}
              {m.content}
            </p>
          </div>
        ))
      )}
      {busy && <p className="text-xs text-dim">{t("posterChat.thinking")}</p>}
    </div>
  );
}

/**
 * Design the poster by talking to Mira. Each message sends the conversation and the current form;
 * Mira answers and returns the whole updated draft, which is applied to the form (and so to the live
 * preview). Creating the poster stays an explicit click, because it can cost money.
 */
export function PosterChat({
  spec,
  onDraft,
  onCreate,
  creating,
  initialTurns,
  onLibraryChanged,
}: {
  spec: PosterSpec;
  onDraft: (draft: Partial<PosterSpec>) => void;
  onCreate: () => void;
  creating: boolean;
  initialTurns?: ChatTurn[];
  /** Called after logos were uploaded from the chat, so the page can refresh its library. */
  onLibraryChanged?: () => void;
}) {
  const { t, locale } = useI18n();
  const tag = locale === "th" ? "th-TH" : "en-US";
  const [input, setInput] = useState("");
  const specRef = useRef(spec);
  useEffect(() => {
    specRef.current = spec;
  });
  const chat = usePosterChat({
    language: locale,
    getSpec: () => specRef.current,
    onDraft,
    unknownNote: (names) => t("posterChat.unknownAssets", { names }),
    initialTurns,
  });

  const send = async (text: string, byVoice = false) => {
    if (chat.busy || !text.trim()) return;
    setInput("");
    const res = await chat.send(text);
    if (res && byVoice) void speak(res.reply, tag); // spoken question, spoken answer
  };
  const sendRef = useRef(send);
  useEffect(() => {
    sendRef.current = send;
  });

  // Voice: what is said is sent as a message, and Mira answers out loud.
  const stopVoice = useRef<() => void>(() => {});
  const voice = useSpeechInput({
    lang: tag,
    onFinal: (heard) => {
      stopVoice.current(); // one spoken message at a time
      void sendRef.current(heard, true);
    },
  });
  useEffect(() => {
    stopVoice.current = voice.stop;
  }, [voice.stop]);

  const addSponsors = (logos: BrandAsset[]) => {
    if (!logos.length) return;
    onDraft({
      sponsor_logo_ids: withSponsors(
        specRef.current,
        logos.map((l) => l.id),
      ).sponsor_logo_ids,
    });
    chat.note(
      t("posterChat.sponsorsAdded", {
        names: logos.map((l) => l.name).join(", "),
      }),
    );
    onLibraryChanged?.();
  };

  const addPresenters = (logos: BrandAsset[]) => {
    if (!logos.length) return;
    const next = withPresenters(
      specRef.current,
      logos.map((l) => l.id),
    );
    onDraft({
      presenter_label: next.presenter_label,
      presenter_logo_ids: next.presenter_logo_ids,
      sponsor_logo_ids: next.sponsor_logo_ids,
    });
    chat.note(
      t(
        tournament
          ? "posterChat.organisersAdded"
          : "posterChat.presentersAdded",
        {
          names: logos.map((l) => l.name).join(", "),
        },
      ),
    );
    onLibraryChanged?.();
  };

  const setEventLogo = (logos: BrandAsset[]) => {
    const logo = logos[0];
    if (!logo) return;
    const next = withEventLogo(specRef.current, logo.id);
    onDraft({
      event_logo_id: next.event_logo_id,
      sponsor_logo_ids: next.sponsor_logo_ids,
      presenter_logo_ids: next.presenter_logo_ids,
    });
    chat.note(t("posterChat.eventLogoSet", { name: logo.name }));
    onLibraryChanged?.();
  };

  const setGameLogo = (logos: BrandAsset[]) => {
    const logo = logos[0];
    if (!logo) return;
    const next = withGameLogo(specRef.current, logo.id);
    onDraft({
      game_logo_id: next.game_logo_id,
      event_logo_id: next.event_logo_id,
      sponsor_logo_ids: next.sponsor_logo_ids,
      presenter_logo_ids: next.presenter_logo_ids,
    });
    chat.note(t("posterChat.gameLogoSet", { name: logo.name }));
    onLibraryChanged?.();
  };
  const setVenuePhoto = (photos: BrandAsset[]) => {
    const photo = photos[0];
    if (!photo) return;
    onDraft({ venue_photo_id: photo.id });
    chat.note(t("posterChat.venuePhotoSet", { name: photo.name }));
    onLibraryChanged?.();
  };
  const tournament = spec.template === "tournament";

  const suggestions = [
    t("posterChat.suggest1"),
    t("posterChat.suggest2"),
    t("posterChat.suggest3"),
  ];

  return (
    <div className="space-y-3">
      <SectionTitle aside={chat.busy ? <Spinner /> : undefined}>
        {t("posterChat.title")}
      </SectionTitle>
      <ChatBubbles
        turns={chat.turns}
        busy={chat.busy}
        className="max-h-80 min-h-28"
      />

      {chat.turns.length === 0 && (
        <div className="flex flex-wrap gap-2">
          {suggestions.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => void send(s)}
              disabled={chat.busy}
              className="rounded-full border border-line px-3 py-1.5 text-left text-xs text-dim transition hover:border-cyan hover:text-ink disabled:opacity-50"
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {chat.error && <ErrorState error={chat.error} compact />}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
        }}
        className="flex gap-2"
      >
        <label htmlFor="poster-chat-input" className="sr-only">
          {t("posterChat.inputLabel")}
        </label>
        <input
          id="poster-chat-input"
          value={
            voice.listening ? voice.interim || t("assistant.listening") : input
          }
          onChange={(e) => setInput(e.target.value)}
          maxLength={1500}
          placeholder={t("posterChat.placeholder")}
          disabled={chat.busy || voice.listening}
          className="input"
        />
        <Button
          variant={voice.listening ? "danger" : "secondary"}
          disabled={!voice.supported || chat.busy}
          aria-pressed={voice.listening}
          aria-label={voice.listening ? t("voice.stop") : t("voice.start")}
          title={voice.supported ? undefined : t("voice.unsupported")}
          onClick={voice.listening ? voice.stop : voice.start}
          className={cx("shrink-0", voice.listening && "mic-live")}
        >
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            className="size-4"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M9 6a3 3 0 0 1 6 0v6a3 3 0 0 1-6 0zM5 11a7 7 0 0 0 14 0M12 18v3" />
          </svg>
        </Button>
        <Button
          type="submit"
          variant="primary"
          disabled={chat.busy || !input.trim()}
          className="shrink-0"
        >
          {t("assistant.send")}
        </Button>
      </form>

      <div className="flex flex-wrap gap-2">
        <LogoUploadButton
          label={t("posterChat.addEventLogo")}
          multiple={false}
          onUploaded={setEventLogo}
          disabled={chat.busy}
        />
        <LogoUploadButton
          label={t("posterChat.addSponsors")}
          onUploaded={addSponsors}
          disabled={chat.busy}
        />
        <LogoUploadButton
          label={
            tournament
              ? t("posterChat.addOrganisers")
              : t("posterChat.addPresenters")
          }
          onUploaded={addPresenters}
          disabled={chat.busy}
        />
        {tournament && (
          <>
            <LogoUploadButton
              label={t("posterChat.addGameLogo")}
              multiple={false}
              onUploaded={setGameLogo}
              disabled={chat.busy}
            />
            <LogoUploadButton
              label={t("posterChat.addVenuePhoto")}
              multiple={false}
              kind="artwork"
              onUploaded={setVenuePhoto}
              disabled={chat.busy}
            />
          </>
        )}
      </div>

      {chat.ready && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-ok/40 bg-ok/10 p-3">
          <p className="min-w-0 flex-1 text-sm text-ink">
            {t("posterChat.ready")}
          </p>
          <Button variant="primary" loading={creating} onClick={onCreate}>
            {t("poster.create")}
          </Button>
        </div>
      )}
      <p className="hint">{t("posterChat.hint")}</p>
    </div>
  );
}
