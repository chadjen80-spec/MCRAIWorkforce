// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { I18nProvider } from "@/i18n/I18nProvider";
import { dictionaries } from "@/i18n/dictionaries";
import { type LineDraft, type Quotation, type QuotationLine, type Service, lineFromQuotation, linesBody, parseQuantity, trimZeros } from "@/lib/qtc";
import { LineEditor, Totals, linesValid, newLine } from "./QuotationParts";

const th = dictionaries.th;
afterEach(cleanup);
const wrap = (ui: React.ReactNode) => render(<I18nProvider>{ui}</I18nProvider>);

const priced = { id: "s1", code: "PROD", name: "งานโปรดักชั่น", unit: "งาน", default_price: "45000.0000" } as Service;
const unpriced = { id: "s2", code: "DRONE", name: "ถ่ายโดรน", unit: "งาน", default_price: null } as Service;

function Editor({ initial }: { initial: LineDraft[] }) {
  const [lines, setLines] = useState(initial);
  return (
    <>
      <LineEditor lines={lines} onChange={setLines} services={[priced, unpriced]} />
      <output data-testid="body">{JSON.stringify(linesBody(lines))}</output>
    </>
  );
}

describe("quotation line helpers", () => {
  it("sends an empty price as null so only the approved price list (or a person) prices it", () => {
    const body = linesBody([{ ...newLine(priced), quantity: "2", unit_price: "" }, { ...newLine(), description: "ค่าเดินทาง", unit_price: "1,500.50" }]);
    expect(body[0]).toMatchObject({ service_id: "s1", quantity: "2", unit_price: null });
    expect(body[1]).toMatchObject({ service_id: null, description: "ค่าเดินทาง", unit_price: "1500.50" });
  });

  it("validates quantities and typed prices", () => {
    expect(parseQuantity("1.5")).toBe("1.5");
    expect(parseQuantity("1.2345")).toBeNull();
    expect(linesValid([{ ...newLine(priced), quantity: "0" }])).toBe(false);
    expect(linesValid([{ ...newLine(), description: "" }])).toBe(false);
    expect(linesValid([{ ...newLine(priced), unit_price: "abc" }])).toBe(false);
    expect(linesValid([{ ...newLine(priced) }])).toBe(true);
  });

  it("trims only the decimal zeros and keeps typed prices when editing", () => {
    expect(trimZeros("10.000")).toBe("10");
    expect(trimZeros("1.500")).toBe("1.5");
    expect(trimZeros("10")).toBe("10");
    const line = { position: 1, service_id: "s1", description: "x", unit: "งาน", quantity: "2.000", unit_price: "40000.0000",
      price_source: "manual", discount_percent: "10.0000" } as QuotationLine;
    expect(lineFromQuotation(line)).toMatchObject({ quantity: "2", unit_price: "40000.00", discount_percent: "10" });
    expect(lineFromQuotation({ ...line, price_source: "price_list" }).unit_price).toBe(""); // looked up again on save
  });
});

describe("LineEditor", () => {
  it("shows the approved price, flags a missing one, and marks a typed price as entered by a person", () => {
    wrap(<Editor initial={[newLine(priced), newLine(unpriced)]} />);
    expect(screen.getByText(th["qtc.q.fromList"].replace("{price}", "45,000.00"))).toBeTruthy();
    expect(screen.getByText(th["qtc.q.needsPrice"])).toBeTruthy();
    const prices = screen.getAllByLabelText(th["qtc.q.price"]);
    fireEvent.change(prices[1], { target: { value: "12000" } });
    expect(screen.getAllByText(th["qtc.q.source.manual"])).toHaveLength(1);
    expect(JSON.parse(screen.getByTestId("body").textContent!)[1].unit_price).toBe("12000");
  });

  it("adds catalogue services and other items, and removes lines", () => {
    wrap(<Editor initial={[]} />);
    expect(screen.getByText(th["qtc.q.noLinesHint"])).toBeTruthy();
    fireEvent.change(screen.getByLabelText(th["qtc.q.addService"]), { target: { value: "s1" } });
    fireEvent.click(screen.getByRole("button", { name: th["qtc.q.addCustom"] }));
    expect(JSON.parse(screen.getByTestId("body").textContent!).map((l: { service_id: string | null }) => l.service_id)).toEqual(["s1", null]);
    fireEvent.click(screen.getAllByRole("button", { name: th["qtc.q.remove"] })[0]);
    expect(JSON.parse(screen.getByTestId("body").textContent!)).toHaveLength(1);
  });
});

describe("Totals", () => {
  it("shows the server's totals and the amount in Thai words", () => {
    const q = { subtotal: "45000.50", discount_total: "0.00", vat_total: "3150.04", grand_total: "48150.54", vat_rate: "0.0700",
      amount_in_words: "สี่หมื่นแปดพันหนึ่งร้อยห้าสิบบาทห้าสิบสี่สตางค์", prices_include_vat: false } as Quotation;
    wrap(<Totals q={q} />);
    expect(screen.getByText("฿48,150.54")).toBeTruthy();
    expect(screen.getByText("3,150.04")).toBeTruthy();
    expect(screen.getByText(/สี่หมื่นแปดพัน/)).toBeTruthy();
    expect(screen.getByText(th["qtc.q.vat"].replace("{rate}", "7%"))).toBeTruthy();
  });
});
