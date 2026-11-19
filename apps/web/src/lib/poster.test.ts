import { describe, expect, it } from "vitest";
import {
  EMPTY_POSTER,
  MAX_PRESENTERS,
  MAX_SPONSORS,
  cleanSpec,
  mergeDraft,
  withEventLogo,
  withGameLogo,
  withPresenters,
  withSponsors,
  withTemplate,
} from "./poster";
import type { PosterSpec } from "./types";

const base: PosterSpec = { ...EMPTY_POSTER, title: "Surat Esports" };
const defaults = { prize_label: "Prize", badge_text: "Free", cta_text: "Join", col1: "Rules", col2: "Match" };

describe("mergeDraft", () => {
  it("keeps fields Mira did not send and always shows two headline inputs", () => {
    const next = mergeDraft({ ...base, venue: "Hall 1" }, { headline: ["Final"] });
    expect(next.venue).toBe("Hall 1");
    expect(next.headline).toEqual(["Final", ""]);
  });
});

describe("cleanSpec", () => {
  it("drops blank headline, category and schedule entries", () => {
    const spec = cleanSpec({
      ...base,
      headline: ["  Round 8 ", ""],
      category_lines: ["Open", " "],
      schedule: [
        { label: "Match", big: "8-9", lines: ["Aug", ""] },
        { label: "", big: "", lines: [] },
      ],
    });
    expect(spec.headline).toEqual(["Round 8"]);
    expect(spec.category_lines).toEqual(["Open"]);
    expect(spec.schedule).toEqual([{ label: "Match", big: "8-9", lines: ["Aug"] }]);
  });
});

describe("logo placement", () => {
  it("adds sponsors once, at most 8, never the event or presenter logo", () => {
    const spec = { ...base, event_logo_id: "ev", presenter_logo_ids: ["p1"] };
    const ids = ["s1", "s1", "ev", "p1", ...Array.from({ length: 10 }, (_, i) => `x${i}`)];
    const next = withSponsors(spec, ids);
    expect(next.sponsor_logo_ids[0]).toBe("s1");
    expect(new Set(next.sponsor_logo_ids).size).toBe(next.sponsor_logo_ids.length);
    expect(next.sponsor_logo_ids).not.toContain("ev");
    expect(next.sponsor_logo_ids).not.toContain("p1");
    expect(next.sponsor_logo_ids.length).toBe(MAX_SPONSORS);
  });

  it("moves a sponsor to the presenter row and caps presenters", () => {
    const spec = { ...base, sponsor_logo_ids: ["a", "b"], presenter_label: "" };
    const next = withPresenters(spec, ["b", ...Array.from({ length: 9 }, (_, i) => `p${i}`)]);
    expect(next.sponsor_logo_ids).toEqual(["a"]);
    expect(next.presenter_logo_ids.length).toBe(MAX_PRESENTERS);
    expect(next.presenter_label).toBe(EMPTY_POSTER.presenter_label); // filled in when empty
  });

  it("an event or game logo is taken out of the logo rows", () => {
    const spec = { ...base, sponsor_logo_ids: ["x"], presenter_logo_ids: ["y"] };
    expect(withEventLogo(spec, "x").sponsor_logo_ids).toEqual([]);
    const game = withGameLogo({ ...spec, event_logo_id: "y" }, "y");
    expect(game.game_logo_id).toBe("y");
    expect(game.event_logo_id).toBeNull();
    expect(game.presenter_logo_ids).toEqual([]);
  });
});

describe("withTemplate", () => {
  it("fills the fixed tournament wording only where empty", () => {
    const next = withTemplate({ ...base, prize_label: "Total prize" }, "tournament", defaults);
    expect(next.template).toBe("tournament");
    expect(next.prize_label).toBe("Total prize");
    expect(next.badge_text).toBe("Free");
    expect(next.schedule?.map((s) => s.label)).toEqual(["Rules", "Match"]);
  });

  it("switching back keeps everything", () => {
    const t = withTemplate(base, "tournament", defaults);
    expect(withTemplate(t, "esports", defaults)).toEqual({ ...t, template: "esports" });
  });
});
