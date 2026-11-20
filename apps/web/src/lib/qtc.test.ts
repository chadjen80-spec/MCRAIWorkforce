import { describe, expect, it } from "vitest";
import { formatAmount, formatRate, parseAmountInput, thaiTaxIdValid } from "./qtc";

describe("formatAmount: exact decimal strings, never floats", () => {
  it("groups thousands and rounds half up on the digits", () => {
    expect(formatAmount("45000.0000")).toBe("45,000.00");
    expect(formatAmount("1234567.895")).toBe("1,234,567.90");
    expect(formatAmount("2.675")).toBe("2.68"); // Number(2.675).toFixed(2) gives "2.67"
    expect(formatAmount("999.995")).toBe("1,000.00");
    expect(formatAmount("0.1")).toBe("0.10");
    expect(formatAmount("-1500.5")).toBe("-1,500.50");
    expect(formatAmount("-0.001")).toBe("0.00");
  });

  it("shows a dash for missing or malformed values", () => {
    expect(formatAmount(null)).toBe("–");
    expect(formatAmount("1e5")).toBe("–");
    expect(formatAmount("abc")).toBe("–");
  });
});

describe("other helpers", () => {
  it("formats VAT rates", () => {
    expect(formatRate("0.0700")).toBe("7%");
    expect(formatRate("0.0750")).toBe("7.5%");
    expect(formatRate("0.0000")).toBe("0%");
  });

  it("checks Thai tax IDs like the server", () => {
    expect(thaiTaxIdValid("0105536000313")).toBe(true);
    expect(thaiTaxIdValid("0-1055-36000-31-3")).toBe(true);
    expect(thaiTaxIdValid("0105536000314")).toBe(false);
    expect(thaiTaxIdValid("12345")).toBe(false);
  });

  it("accepts typed amounts with commas or a baht sign", () => {
    expect(parseAmountInput("45,000.50")).toBe("45000.50");
    expect(parseAmountInput("฿ 1200")).toBe("1200");
    expect(parseAmountInput("1.23456")).toBeNull();
    expect(parseAmountInput("-5")).toBeNull();
    expect(parseAmountInput("")).toBeNull();
  });
});
