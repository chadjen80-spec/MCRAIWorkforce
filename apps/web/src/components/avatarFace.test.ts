import { describe, expect, it } from "vitest";
import { eyeState, makeBlinker, mouthOpenness, mouthStep } from "./avatarFace";

describe("Mira's mouth", () => {
  it("stays closed when she is not speaking", () => {
    for (const t of [0, 0.3, 1.7, 12]) expect(mouthOpenness(t, false, 0.9)).toBe(0);
  });

  it("follows the measured voice level when there is one", () => {
    expect(mouthOpenness(5, true, 0.42)).toBe(0.42);
    expect(mouthOpenness(5, true, 3)).toBe(1);
    expect(mouthOpenness(5, true, -1)).toBe(0);
  });

  it("opens and closes in a talking rhythm when the level is unknown", () => {
    const samples = Array.from({ length: 300 }, (_, i) => mouthOpenness(i / 60, true, null));
    expect(Math.max(...samples)).toBeGreaterThan(0.5);
    expect(samples.filter((v) => v === 0).length).toBeGreaterThan(50); // closes between syllables and words
    expect(samples.every((v) => v >= 0 && v <= 1)).toBe(true);
  });

  it("repaints only in a few steps", () => {
    expect([0, 0.1, 0.2, 0.6, 0.95].map(mouthStep)).toEqual([0, 0, 0.25, 0.5, 1]);
  });
});

describe("Mira's eyes", () => {
  it("blink briefly every few seconds", () => {
    const blink = makeBlinker(42);
    const frames = Array.from({ length: 60 * 30 }, (_, i) => blink(i / 60)); // 30 s at 60 fps
    let blinks = 0;
    let longest = 0;
    let run = 0;
    frames.forEach((closed, i) => {
      if (closed && !frames[i - 1]) blinks++;
      run = closed ? run + 1 : 0;
      longest = Math.max(longest, run);
    });
    expect(blinks).toBeGreaterThanOrEqual(5);
    expect(blinks).toBeLessThanOrEqual(16);
    expect(longest).toBeLessThanOrEqual(9); // never closed for more than ~0.15 s at a time
    expect(frames.filter(Boolean).length / frames.length).toBeLessThan(0.1); // open most of the time
  });

  it("close happily when she celebrates", () => {
    expect(eyeState(false, 0.9)).toBe("happy");
    expect(eyeState(true, 0)).toBe("closed");
    expect(eyeState(false, 0.2)).toBe("open");
  });
});
