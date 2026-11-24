import { describe, expect, it } from "vitest";
import { POSTER_WORDS, cafePageFor, confirmAction, decide, posterIntent, qtcPageFor } from "./intent";

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

describe("confirmAction: Mira only starts a paid job on an explicit yes", () => {
  const job = "ทำภาพโปรโมต 3 ภาพ";
  const poster = "ทำโปสเตอร์งานแข่ง";

  it("submits only on yes", () => {
    expect(confirmAction("ยืนยันค่ะ", "th", job)).toBe("submit");
    expect(confirmAction("yes, go ahead", "en", job)).toBe("submit");
  });

  it("cancels on no, even when a yes word is inside the answer", () => {
    expect(confirmAction("ไม่ใช่", "th", job)).toBe("cancel");
    expect(confirmAction("no, don't start", "en", job)).toBe("cancel");
  });

  it("offers design only for poster requests; otherwise asks again (never starts the job)", () => {
    expect(confirmAction("ออกแบบ", "th", poster)).toBe("design");
    expect(confirmAction("design it", "en", job)).toBe("ask_again");
  });

  it("asks again when the answer is unclear, empty or noise", () => {
    for (const heard of ["", "อืม", "what?", "hmm maybe later"]) {
      expect(confirmAction(heard, heard.match(/[a-z]/) ? "en" : "th", job)).toBe("ask_again");
    }
  });
});

describe("qtcPageFor: saying a page name opens that Sales & Finance page", () => {
  it("opens Quotations and Invoices for the bare name, in Thai or English", () => {
    for (const text of ["ใบเสนอราคา", "ใบเสนอราคาค่ะ", "เปิดหน้าใบเสนอราคา", "ไปที่ใบเสนอราคา", "Quotations", "quotation.", "Open quotations", "show me the quotes"]) {
      expect(qtcPageFor(text)?.path, text).toBe("/qtc/quotations");
    }
    for (const text of ["ใบแจ้งหนี้", "ขอดูใบแจ้งหนี้ทั้งหมด", "หน้า ใบแจ้งหนี้ ครับ", "Invoice", "Invoices!", "go to the invoices page"]) {
      expect(qtcPageFor(text)?.path, text).toBe("/qtc/invoices");
    }
  });

  it("opens the page when you say you want to make one", () => {
    for (const text of ["ต้องการสร้างใบเสนอราคา", "อยากทำใบเสนอราคาค่ะ", "Want to do the Quotations", "I want to create a quotation", "new quotation"]) {
      expect(qtcPageFor(text)?.path, text).toBe("/qtc/quotations/new"); // the new-quotation form
    }
    for (const text of ["ใบเสนอราคา", "เปิดหน้าใบเสนอราคา", "show me the quotes"]) {
      expect(qtcPageFor(text)?.path, text).toBe("/qtc/quotations"); // the list
    }
    for (const text of ["ต้องการสร้างใบแจ้งหนี้", "ผมอยากทำใบแจ้งหนี้ใหม่", "Want to do the Invoice", "I'd like to make an invoice"]) {
      expect(qtcPageFor(text)?.path, text).toBe("/qtc/invoices/new"); // the new-invoice form
    }
    expect(qtcPageFor("ใบแจ้งหนี้")?.path).toBe("/qtc/invoices"); // the list
  });

  it("opens Orders and Payments too", () => {
    for (const text of ["ใบสั่งขาย", "เปิดหน้าใบสั่งขาย", "ขอดูออเดอร์ทั้งหมด", "Orders", "sales orders", "show me the orders"]) {
      expect(qtcPageFor(text)?.path, text).toBe("/qtc/orders");
    }
    for (const text of ["การรับชำระ", "รับชำระ", "ไปที่หน้าการชำระเงิน", "Payments", "payment.", "open payments"]) {
      expect(qtcPageFor(text)?.path, text).toBe("/qtc/payments");
    }
  });

  it("covers every Sales & Finance page", () => {
    const cases: [string, string][] = [
      ["งานขายและการเงิน", "/qtc"],
      ["Sales & Finance", "/qtc"],
      ["ภาพรวมงานขาย", "/qtc"],
      ["ลูกค้า", "/qtc/customers"],
      ["เปิดหน้ารายชื่อลูกค้า", "/qtc/customers"],
      ["Customers", "/qtc/customers"],
      ["เพิ่มลูกค้า", "/qtc/customers?new=1"],
      ["add a customer", "/qtc/customers?new=1"],
      ["บริการและราคา", "/qtc/services"],
      ["รายการราคา", "/qtc/services"],
      ["price list", "/qtc/services"],
      ["เพิ่มบริการใหม่", "/qtc/services?new=1"],
      ["ใบเสนอราคา", "/qtc/quotations"], // "ราคา" inside it never opens Services
    ];
    for (const [text, path] of cases) expect(qtcPageFor(text)?.path, text).toBe(path);
    expect(qtcPageFor("ภาพรวม")).toBeNull(); // that is the app's main dashboard
  });

  it("leaves sentences to Mira's other flows", () => {
    for (const text of [
      "สร้างใบเสนอราคางาน Production ให้ลูกค้า ABC", // a draft quotation (Sales & Finance agent)
      "ตรวจสอบใบเสนอราคาที่ยังไม่อนุมัติ", // a question about quotations
      "ทำใบเสนอราคา PDF สำหรับแคมเปญโฆษณา", // a creative document job
      "เตรียมข้อความติดตามเงินสำหรับ Invoice ที่เกินกำหนด",
      "Create a quotation for customer ABC",
      "ทำโปสเตอร์โปรโมชันร้านกาแฟ",
      "Prepare payment reminders for overdue invoices",
      "ลูกค้ารายไหนยังไม่ชำระเงิน",
      "สร้างใบสั่งขายจากใบเสนอราคา QT-2026-0001",
      "ลูกค้ารายไหนยังไม่จ่ายเงิน",
      "ราคาของบริการถ่ายโดรนเท่าไหร่",
    ]) {
      expect(qtcPageFor(text), text).toBeNull();
    }
  });
});

describe("cafePageFor: saying a Smart Café page name opens that page", () => {
  const cases: [string, string][] = [
    ["ร้านเกม", "/cafe"],
    ["เปิดหน้าร้านเกม", "/cafe"],
    ["Smart Café", "/cafe"],
    ["open the cafe", "/cafe"],
    ["เครื่องลูก", "/cafe/devices"],
    ["ไปที่หน้าเครื่องลูก", "/cafe/devices"],
    ["ขอดูที่นั่งทั้งหมดค่ะ", "/cafe/devices"],
    ["open the cafe devices page", "/cafe/devices"],
    ["รอบเล่น", "/cafe/sessions"],
    ["sessions", "/cafe/sessions"],
    ["คลังเกม", "/cafe/games"],
    ["เปิดหน้าเกม", "/cafe/games"],
    ["show me the games", "/cafe/games"],
    ["ออเดอร์อาหาร", "/cafe/orders"],
    ["งานพิมพ์", "/cafe/printing"],
    ["สมาชิก", "/cafe/members"],
    ["รายงานร้านเกม", "/cafe/reports"],
    ["ตั้งค่าร้านเกม", "/cafe/settings"],
    ["cafe settings", "/cafe/settings"],
    // spellings that speech-to-text produces
    ["เปิดหน้าร้านเกมส์", "/cafe"],
    ["เปิดคาเฟ", "/cafe"],
    ["คลังเกมส์", "/cafe/games"],
  ];
  it.each(cases)("%s -> %s", (text, path) => {
    expect(cafePageFor(text)?.path).toBe(path);
  });

  it("leaves actions and longer sentences to the café assistant and Mira's other flows", () => {
    for (const text of ["เปิด Steam ที่เครื่อง 12", "เครื่อง 12 เหลือเวลาเท่าไหร่", "สั่งอาหาร", "เรียกพนักงาน", "ทำโปสเตอร์ร้านเกมลดราคา", "อยากเล่นเกม Valorant"]) {
      expect(cafePageFor(text), text).toBeNull();
    }
  });

  it("keeps Sales & Finance page names with Sales & Finance", () => {
    for (const text of ["ออเดอร์", "orders", "ใบสั่งขาย"]) {
      expect(cafePageFor(text), text).toBeNull();
      expect(qtcPageFor(text)?.path, text).toBe("/qtc/orders");
    }
  });
});
