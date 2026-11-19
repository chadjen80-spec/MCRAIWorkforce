// One-shot speech helpers for the assistant page (browser Web Speech API).
// Recognition audio is processed by the browser vendor's speech service; synthesis uses the
// voices installed in the browser/OS. Neither goes through MCR servers.

interface RecognitionResult {
  isFinal: boolean;
  0: { transcript: string };
}
interface RecognitionEvent {
  resultIndex: number;
  results: ArrayLike<RecognitionResult>;
}
interface Recognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((e: RecognitionEvent) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  abort(): void;
}
type RecognitionCtor = new () => Recognition;

export type VoiceErrorCode = "unsupported" | "denied" | "no-microphone" | "network" | "no-speech" | "failed" | "aborted";

export class VoiceError extends Error {
  constructor(public code: VoiceErrorCode) {
    super(code);
  }
}

function recognitionCtor(): RecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function canListen(): boolean {
  return recognitionCtor() !== null;
}

export function canSpeak(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

/** Listen for one utterance and resolve with its text. Rejects with VoiceError. */
export function listenOnce(lang: string, opts: { onInterim?: (text: string) => void; signal?: AbortSignal } = {}): Promise<string> {
  return new Promise((resolve, reject) => {
    const Ctor = recognitionCtor();
    if (!Ctor) return reject(new VoiceError("unsupported"));
    if (opts.signal?.aborted) return reject(new VoiceError("aborted"));
    const rec = new Ctor();
    rec.lang = lang;
    rec.continuous = false;
    rec.interimResults = true;
    let finalText = "";
    let error: VoiceErrorCode | null = null;
    const onAbort = () => {
      error = "aborted";
      rec.abort();
    };
    opts.signal?.addEventListener("abort", onAbort);
    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) finalText += (finalText ? " " : "") + r[0].transcript.trim();
        else interim += r[0].transcript;
      }
      opts.onInterim?.((finalText + " " + interim).trim());
    };
    rec.onerror = (e) => {
      if (error) return;
      if (e.error === "not-allowed" || e.error === "service-not-allowed") error = "denied";
      else if (e.error === "audio-capture") error = "no-microphone";
      else if (e.error === "network") error = "network";
      else if (e.error === "no-speech") error = "no-speech";
      else if (e.error === "aborted") error = "aborted";
      else error = "failed";
    };
    rec.onend = () => {
      opts.signal?.removeEventListener("abort", onAbort);
      if (finalText) resolve(finalText);
      else reject(new VoiceError(error ?? "no-speech"));
    };
    try {
      rec.start();
    } catch {
      opts.signal?.removeEventListener("abort", onAbort);
      reject(new VoiceError("failed"));
    }
  });
}

// Voice choice: the assistant is a young woman, so prefer a female voice for the language.
// Which voices exist depends on the browser and OS (e.g. Edge offers the female Thai voice
// "Premwadee"; Chrome on Windows only has the male "Pattara"). When only a male or unknown
// voice is available, the pitch is raised so it still sounds light.
const FEMALE = /premwadee|achara|kanya|narisa|female|woman|aria|jenny|zira|samantha|sonia|libby|michelle|ana|emma|ava|google us english|google uk english female/i;
const MALE = /pattara|niwat|male|david|mark|guy|ryan|george|james|daniel|google uk english male/i;

function loadVoices(): Promise<SpeechSynthesisVoice[]> {
  const synth = window.speechSynthesis;
  const now = synth.getVoices();
  if (now.length) return Promise.resolve(now);
  return new Promise((resolve) => {
    const done = () => resolve(synth.getVoices());
    synth.addEventListener("voiceschanged", done, { once: true });
    setTimeout(done, 1200); // some browsers never fire the event
  });
}

export function pickVoice(voices: SpeechSynthesisVoice[], lang: string): { voice: SpeechSynthesisVoice | null; pitch: number; rate: number } {
  const prefix = lang.slice(0, 2).toLowerCase();
  const inLang = voices.filter((v) => v.lang.toLowerCase().replace("_", "-").startsWith(prefix));
  const natural = (v: SpeechSynthesisVoice) => /natural|online|neural/i.test(v.name);
  const female = inLang.filter((v) => FEMALE.test(v.name));
  const best = female.find(natural) ?? female[0];
  if (best) return { voice: best, pitch: 1.3, rate: 1.05 };
  const neutral = inLang.find((v) => !MALE.test(v.name));
  if (neutral) return { voice: neutral, pitch: 1.5, rate: 1.05 };
  // Only a male voice (or none) for this language: lift the pitch as far as stays intelligible.
  return { voice: inLang[0] ?? null, pitch: 1.9, rate: 1.08 };
}

/**
 * Speak text aloud. Resolves when speech ends. Without speech synthesis (or if it fails) it
 * resolves after a reading pause so the caption stays on screen long enough.
 */
function browserSpeak(text: string, lang: string, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal?.aborted) return resolve();
    const readingPause = Math.min(6000, 900 + text.length * 45);
    let done = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const finish = () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      resolve();
    };
    const onAbort = () => {
      if (canSpeak()) window.speechSynthesis.cancel();
      finish();
    };
    signal?.addEventListener("abort", onAbort);
    if (!canSpeak()) {
      timer = setTimeout(finish, readingPause);
      return;
    }
    const synth = window.speechSynthesis;
    synth.cancel();
    // Safety net: some browsers never fire onend for certain voices.
    timer = setTimeout(finish, readingPause + 14000);
    void loadVoices().then((voices) => {
      if (done) return;
      const choice = pickVoice(voices, lang);
      const utter = new SpeechSynthesisUtterance(text);
      utter.lang = lang;
      if (choice.voice) utter.voice = choice.voice;
      utter.pitch = choice.pitch;
      utter.rate = choice.rate;
      utter.onend = finish;
      utter.onerror = () => {
        // e.g. autoplay policy or no voice for this language: fall back to a reading pause
        clearTimeout(timer);
        timer = setTimeout(finish, readingPause);
      };
      synth.speak(utter);
    });
  });
}

// --- cloud voice ---------------------------------------------------------------------------
// When the backend has a speech provider configured, the assistant uses it so every browser
// gets the same female voice. If it is not configured (409), over budget (402) or fails, we
// fall back to the browser voice and stop asking for a few minutes.
let cloudOffUntil = 0;

async function cloudSpeak(text: string, lang: string, signal?: AbortSignal): Promise<boolean> {
  if (Date.now() < cloudOffUntil || typeof Audio === "undefined") return false;
  let url: string | null = null;
  try {
    const res = await fetch("/api/proxy/v1/assistant/speech", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: text.slice(0, 600), language: lang.toLowerCase().startsWith("th") ? "th" : "en" }),
      signal,
    });
    if (!res.ok) {
      if (res.status === 409 || res.status === 402 || res.status === 429) cloudOffUntil = Date.now() + 5 * 60_000;
      return false;
    }
    const blob = await res.blob();
    if (!blob.size || signal?.aborted) return false;
    url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    await new Promise<void>((resolve, reject) => {
      audio.onended = () => resolve();
      audio.onerror = () => reject(new Error("audio"));
      signal?.addEventListener(
        "abort",
        () => {
          audio.pause();
          resolve();
        },
        { once: true },
      );
      audio.play().catch(reject); // e.g. autoplay blocked: fall back to the browser voice
    });
    return true;
  } catch {
    return false;
  } finally {
    if (url) URL.revokeObjectURL(url);
  }
}

/** Speak text aloud: cloud voice when available, otherwise the browser voice. Resolves when speech ends. */
export async function speak(text: string, lang: string, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return;
  if (await cloudSpeak(text, lang, signal)) return;
  if (signal?.aborted) return;
  await browserSpeak(text, lang, signal);
}
