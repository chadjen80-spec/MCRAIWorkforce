import { describe, expect, it } from "vitest";
import { safeNext } from "./safeNext";
import { safeUpstreamPath } from "./server";

describe("safeNext: where login may send you", () => {
  it("allows same-site paths", () => {
    expect(safeNext("/jobs/123")).toBe("/jobs/123");
    expect(safeNext("/posters?tab=library")).toBe("/posters?tab=library");
  });

  it("blocks open redirects and loops", () => {
    expect(safeNext(null)).toBe("/assistant");
    expect(safeNext("https://evil.example")).toBe("/assistant");
    expect(safeNext("//evil.example")).toBe("/assistant");
    expect(safeNext("/\\evil.example")).toBe("/assistant");
    expect(safeNext("/login?next=/jobs")).toBe("/assistant");
    expect(safeNext("/api/proxy/v1/jobs")).toBe("/assistant");
  });
});

describe("safeUpstreamPath: what the proxy may forward to the API", () => {
  it("joins normal segments", () => {
    expect(safeUpstreamPath(["v1", "jobs", "abc"])).toBe("v1/jobs/abc");
    expect(safeUpstreamPath(["v1", "posters", "เทส"])).toBe("v1/posters/%E0%B9%80%E0%B8%97%E0%B8%AA");
  });

  it("rejects anything that could escape the prefix", () => {
    expect(safeUpstreamPath([])).toBeNull();
    expect(safeUpstreamPath(["v1", ".."])).toBeNull();
    expect(safeUpstreamPath(["v1", "."])).toBeNull();
    expect(safeUpstreamPath(["v1", ""])).toBeNull();
    expect(safeUpstreamPath(["v1", "a/b"])).toBeNull();
    expect(safeUpstreamPath(["v1", "a\\b"])).toBeNull();
    expect(safeUpstreamPath(["v1", "jobs?x=1"])).toBeNull();
    expect(safeUpstreamPath(["v1", "jobs#x"])).toBeNull();
  });
});
