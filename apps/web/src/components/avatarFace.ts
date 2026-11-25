// Mira's face animation. The Nova model's eyes and mouth are painted on one transparent picture (`FaceDecal`) laid over
// the head, with no face bones or morphs. So the face is animated by repainting that picture on a canvas: the mouth
// opens with her voice, and the eyes blink (or close happily when she celebrates). Only the mouth and eye areas are
// redrawn; clearing an area lets the skin underneath show through, because the picture is transparent elsewhere.
//
// The timing functions are pure, so they can be tested without WebGL or a canvas.

/** Areas of the 1024 x 853 face picture, in its own pixels. */
export const FACE = {
  width: 1024,
  height: 853,
  mouth: { x: 425, y: 655, w: 180, h: 75, cx: 512, cy: 690 },
  eyes: [
    { x: 68, y: 232, w: 324, h: 320, cx: 243, cy: 420 },
    { x: 632, y: 232, w: 324, h: 320, cx: 781, cy: 420 },
  ],
} as const;

export type EyeState = "open" | "closed" | "happy";

/** Mouth opening 0..1. `level` is the measured voice loudness when known; otherwise a talking rhythm is used. */
export function mouthOpenness(t: number, speaking: boolean, level: number | null): number {
  if (!speaking) return 0;
  if (level !== null) return Math.max(0, Math.min(1, level));
  // syllables (~6 per second) with a slower phrase envelope and short pauses between words
  const syllable = Math.max(0, Math.sin(t * 2 * Math.PI * 5.8));
  const phrase = 0.55 + 0.45 * Math.sin(t * 2 * Math.PI * 0.7 + 1.3);
  const pause = Math.sin(t * 2 * Math.PI * 0.45) > 0.86 ? 0 : 1;
  return Math.min(1, syllable * phrase * pause);
}

/** Mouth openness rounded to a few steps, so the face picture is only repainted when it visibly changes. */
export function mouthStep(open: number): number {
  return Math.round(Math.max(0, Math.min(1, open)) * 4) / 4;
}

/**
 * Blinking: a blink lasts `duration` seconds and the gap between blinks is 2-6 s, sometimes with a quick second blink,
 * as people do. Deterministic for a given seed, so it can be tested.
 */
export function makeBlinker(seed = 1, duration = 0.13) {
  let rnd = seed >>> 0 || 1;
  const random = () => {
    rnd = (rnd * 1664525 + 1013904223) >>> 0;
    return rnd / 2 ** 32;
  };
  let next = 1.2 + random() * 2; // first blink soon after she appears
  let doubleAt: number | null = null;
  return (t: number): boolean => {
    if (doubleAt !== null && t >= doubleAt && t < doubleAt + duration) return true;
    if (t < next) return false;
    if (t < next + duration) return true;
    // schedule the next blink once this one is over
    doubleAt = random() < 0.2 ? next + duration + 0.12 : null;
    next = next + duration + 2 + random() * 4;
    return false;
  };
}

export function eyeState(blinking: boolean, joy: number): EyeState {
  if (joy > 0.6) return "happy";
  return blinking ? "closed" : "open";
}

const LASH = "#3b1f16";
const LIP = "#5a2a20";

/**
 * Repaints the face picture. `source` is the model's original face image; the returned `draw` only touches the canvas
 * when the eyes or the mouth step change, and reports whether it did (so the texture is re-uploaded only then).
 */
export function createFacePainter(source: CanvasImageSource, canvas: HTMLCanvasElement) {
  canvas.width = FACE.width;
  canvas.height = FACE.height;
  const ctx = canvas.getContext("2d");
  let last = "";
  const draw = (eyes: EyeState, mouth: number): boolean => {
    const key = `${eyes}:${mouth}`;
    if (!ctx || key === last) return false;
    last = key;
    ctx.clearRect(0, 0, FACE.width, FACE.height);
    ctx.drawImage(source, 0, 0, FACE.width, FACE.height);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";

    if (eyes !== "open") {
      for (const e of FACE.eyes) {
        ctx.clearRect(e.x, e.y, e.w, e.h);
        ctx.strokeStyle = LASH;
        ctx.lineWidth = 15;
        ctx.beginPath();
        if (eyes === "happy") {
          // "^" eyes: an upward arc
          ctx.moveTo(e.cx - 105, e.cy + 25);
          ctx.quadraticCurveTo(e.cx, e.cy - 85, e.cx + 105, e.cy + 25);
        } else {
          // closed: a gently curved lash line with a small flick at the outer corner
          const outer = e.cx < FACE.width / 2 ? -1 : 1;
          ctx.moveTo(e.cx - 115, e.cy);
          ctx.quadraticCurveTo(e.cx, e.cy + 55, e.cx + 115, e.cy);
          ctx.moveTo(e.cx + outer * 115, e.cy);
          ctx.lineTo(e.cx + outer * 140, e.cy - 22);
        }
        ctx.stroke();
      }
    }

    if (mouth > 0) {
      const m = FACE.mouth;
      ctx.clearRect(m.x, m.y, m.w, m.h);
      const rx = 30 + 14 * mouth;
      const ry = 5 + 25 * mouth;
      ctx.save();
      ctx.beginPath();
      ctx.ellipse(m.cx, m.cy, rx, ry, 0, 0, Math.PI * 2);
      ctx.fillStyle = "#6e1f2c";
      ctx.fill();
      ctx.clip();
      ctx.beginPath(); // tongue
      ctx.ellipse(m.cx, m.cy + ry * 0.75, rx * 0.7, ry * 0.6, 0, 0, Math.PI * 2);
      ctx.fillStyle = "#e07a8a";
      ctx.fill();
      ctx.restore();
      ctx.beginPath();
      ctx.ellipse(m.cx, m.cy, rx, ry, 0, 0, Math.PI * 2);
      ctx.strokeStyle = LIP;
      ctx.lineWidth = 5;
      ctx.stroke();
    }
    return true;
  };
  return { draw };
}
