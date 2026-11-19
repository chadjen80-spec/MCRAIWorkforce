"use client";

import { useId, type FormEvent, type KeyboardEvent, type ReactNode, type RefObject } from "react";
import { Button, cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";

export const COMMAND_MAX = 4000;

/**
 * The typed way to give Mira a command: a multiline box. Enter sends, Shift+Enter starts a new line, and
 * Enter is ignored while an input method (Thai, Japanese, ...) is still composing a word, so picking an
 * IME candidate never sends half a command.
 */
export function CommandComposer({
  value,
  onChange,
  onSubmit,
  minLength,
  label,
  placeholder,
  note,
  inputRef,
  id,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  /** Shortest text that can be sent (trimmed). */
  minLength: number;
  label: string;
  placeholder?: string;
  /** Extra guidance under the box (e.g. after a quick action filled it). */
  note?: ReactNode;
  inputRef?: RefObject<HTMLTextAreaElement | null>;
  id?: string;
  className?: string;
}) {
  const { t } = useI18n();
  const autoId = useId();
  const boxId = id ?? `composer-${autoId}`;
  const helpId = `${boxId}-help`;
  const ready = value.trim().length >= minLength;
  // Grow with the text: 3 lines minimum, 10 at most (then it scrolls).
  const rows = Math.min(10, Math.max(3, value.split("\n").length + Math.floor(value.length / 90)));

  const send = (e?: FormEvent) => {
    e?.preventDefault();
    if (ready) onSubmit();
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== "Enter" || e.shiftKey || e.altKey) return;
    // keyCode 229: some browsers report IME composition this way instead of isComposing.
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    e.preventDefault();
    send();
  };

  return (
    <form onSubmit={send} className={cx("rounded-2xl border border-line bg-surface p-3 focus-within:border-brand-soft/70 sm:p-4", className)}>
      <label htmlFor={boxId} className="mb-2 block text-base font-semibold text-ink">
        {label}
      </label>
      <textarea
        ref={inputRef}
        id={boxId}
        value={value}
        rows={rows}
        maxLength={COMMAND_MAX}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        aria-describedby={helpId}
        className="input min-h-24 resize-y text-base leading-relaxed"
      />
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2">
        <p id={helpId} className="min-w-0 flex-1 text-sm leading-relaxed text-dim">
          {note ?? t("composer.keys")}
        </p>
        <span className="text-xs tabular-nums text-dim" aria-hidden="true">
          {t("composer.count", { n: String(value.length), max: String(COMMAND_MAX) })}
        </span>
        <Button type="submit" variant="primary" disabled={!ready} className="shrink-0 whitespace-nowrap px-5">
          {t("assistant.send")}
        </Button>
      </div>
    </form>
  );
}
