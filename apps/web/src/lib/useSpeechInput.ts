"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// Voice dictation through the browser's Web Speech API (Chrome, Edge, Safari).
// Note: in Chromium browsers the audio is sent to the browser vendor's speech service for
// recognition; nothing goes through MCR servers. Firefox does not implement this API.

interface RecognitionAlternative {
  transcript: string;
}
interface RecognitionResult {
  isFinal: boolean;
  0: RecognitionAlternative;
}
interface RecognitionEvent {
  resultIndex: number;
  results: ArrayLike<RecognitionResult>;
}
interface RecognitionErrorEvent {
  error: string;
}
interface Recognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((e: RecognitionEvent) => void) | null;
  onerror: ((e: RecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}
type RecognitionCtor = new () => Recognition;

export type SpeechError = "denied" | "no-microphone" | "network" | "failed";

function getCtor(): RecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function useSpeechInput(opts: { lang: string; onFinal: (text: string) => void }) {
  const [supported, setSupported] = useState(false);
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState("");
  const [error, setError] = useState<SpeechError | null>(null);
  const rec = useRef<Recognition | null>(null);
  const onFinal = useRef(opts.onFinal);

  useEffect(() => {
    onFinal.current = opts.onFinal;
  }, [opts.onFinal]);

  // Detect support after mount (the server render cannot know); deferred a frame so the
  // first client render matches the server markup.
  useEffect(() => {
    const id = requestAnimationFrame(() => setSupported(getCtor() !== null));
    return () => {
      cancelAnimationFrame(id);
      rec.current?.abort();
    };
  }, []);

  const stop = useCallback(() => rec.current?.stop(), []);

  const start = useCallback(() => {
    const Ctor = getCtor();
    if (!Ctor || rec.current) return;
    setError(null);
    const r = new Ctor();
    r.lang = opts.lang;
    r.continuous = true;
    r.interimResults = true;
    r.onresult = (e) => {
      let pending = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const result = e.results[i];
        const text = result[0].transcript.trim();
        if (!text) continue;
        if (result.isFinal) onFinal.current(text);
        else pending += (pending ? " " : "") + text;
      }
      setInterim(pending);
    };
    r.onerror = (e) => {
      if (e.error === "no-speech" || e.error === "aborted") return; // silence is not an error
      if (e.error === "not-allowed" || e.error === "service-not-allowed") setError("denied");
      else if (e.error === "audio-capture") setError("no-microphone");
      else if (e.error === "network") setError("network");
      else setError("failed");
    };
    r.onend = () => {
      rec.current = null;
      setListening(false);
      setInterim("");
    };
    rec.current = r;
    try {
      r.start();
      setListening(true);
    } catch {
      rec.current = null;
      setError("failed");
    }
  }, [opts.lang]);

  return { supported, listening, interim, error, start, stop };
}
