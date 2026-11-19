import { describe, expect, it } from "vitest";
import { POSTER_WORDS, decide, posterIntent } from "./intent";

describe("decide: confirming a spoken command", () => {
  it("understands Thai yes / no / design", () => {
    expect(decide("ยืนยันค่ะ", "th")).toBe("yes");
    expect(decide("โอเค เริ่มเลย", "th")).toBe("yes");
    expect(decide("ยกเลิก", "th")).toBe("no");
    expect(decide("ออกแบบ", "th")).toBe("design");
  });

  it("checks 'no' first, so a negation never starts a paid job", () => {
    expect(decide("ไม่ใช่", "th")).toBe("no"); // contains ใช่ (yes) but means "no"
    expect(decide("no, cancel that", "en")).toBe("no");
    expect(decide("don't stop, go ahead", "en")).toBe("no"); // "stop" wins: when unsure, do not spend
  });

  it("matches whole English words only", () => {
    expect(decide("yes please", "en")).toBe("yes");
    expect(decide("design it with me", "en")).toBe("design");
    expect(decide("yesterday", "en")).toBe("unclear");
    expect(decide("nothing", "en")).toBe("unclear"); // "no" inside a word does not cancel
  });

  it("ignores spaces in Thai (speech recognition adds them)", () => {
    expect(decide("ได้ เลย", "th")).toBe("yes");
    expect(decide("อืม", "th")).toBe("unclear");
  });
});

describe("posterIntent: what a spoken line means while designing", () => {
  it("creates or closes only on the exact phrases", () => {
    expect(posterIntent("สร้างโปสเตอร์เลย", "th")).toBe("create");
    expect(posterIntent("สร้าง เลย", "th")).toBe("create");
    expect(posterIntent("จบการออกแบบ", "th")).toBe("close");
    expect(posterIntent("create the poster", "en")).toBe("create");
    expect(posterIntent("stop designing", "en")).toBe("close");
  });

  it("treats everything else as an edit", () => {
    expect(posterIntent("เปลี่ยนเป็นสีแดง", "th")).toBe("edit");
    expect(posterIntent("make the title bigger", "en")).toBe("edit");
  });

  it("detects poster requests in both languages", () => {
    expect(POSTER_WORDS.test("ทำโปสเตอร์งานแข่ง")).toBe(true);
    expect(POSTER_WORDS.test("Make a Poster")).toBe(true);
    expect(POSTER_WORDS.test("write 5 captions")).toBe(false);
  });
});
