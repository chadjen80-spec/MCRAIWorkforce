// Spoken-command intent for Mira: plain keyword matching in Thai and English, kept free of React so it
// can be tested on its own.

import type { Locale } from "@/i18n/dictionaries";

export const YES: Record<Locale, string[]> = { th: ["ยืนยัน", "ตกลง", "ใช่", "เริ่มเลย", "โอเค", "ได้เลย"], en: ["confirm", "yes", "go ahead", "start", "okay", "ok"] };
export const NO: Record<Locale, string[]> = { th: ["ยกเลิก", "ไม่"], en: ["cancel", "no", "stop"] };

// A request that is about a poster can be designed together with Mira, right here on her screen.
export const POSTER_WORDS = /โปสเตอร์|poster/i;
// (TOURNAMENT_WORDS, from lib/poster, start the design in the 4:5 tournament layout.)
export const DESIGN: Record<Locale, string[]> = { th: ["ออกแบบ", "โปสเตอร์"], en: ["design", "poster"] };
// While designing: these words create the poster or close the design; anything else is an edit.
export const CREATE: Record<Locale, string[]> = { th: ["สร้างโปสเตอร์", "สร้างเลย", "สร้างได้เลย"], en: ["create the poster", "create it"] };
export const CLOSE: Record<Locale, string[]> = { th: ["จบการออกแบบ", "เลิกออกแบบ", "ปิดการออกแบบ"], en: ["stop designing", "close the design"] };

export function posterIntent(answer: string, locale: Locale): "create" | "close" | "edit" {
  const text = answer.toLowerCase().replace(/\s+/g, locale === "th" ? "" : " ");
  const has = (words: string[]) => words.some((w) => text.includes(locale === "th" ? w.replace(/\s+/g, "") : w));
  if (has(CLOSE[locale])) return "close";
  if (has(CREATE[locale])) return "create";
  return "edit";
}

export function decide(answer: string, locale: Locale): "yes" | "no" | "design" | "unclear" {
  const text = answer.toLowerCase();
  const has = (words: string[]) =>
    locale === "th" ? words.some((w) => text.replace(/\s+/g, "").includes(w)) : words.some((w) => new RegExp(`\\b${w}\\b`).test(text));
  if (has(NO[locale])) return "no"; // checked first so "ไม่ใช่" / "no, cancel" never start a job
  if (has(DESIGN[locale])) return "design";
  if (has(YES[locale])) return "yes";
  return "unclear";
}

export type ConfirmAction = "submit" | "cancel" | "design" | "ask_again";

/**
 * What Mira does with the spoken answer to "shall I start this job?". A paid job starts only on an
 * explicit yes; "design" only applies to poster requests; anything unclear is asked again.
 */
export function confirmAction(answer: string, locale: Locale, command: string): ConfirmAction {
  const verdict = decide(answer, locale);
  if (verdict === "design") return POSTER_WORDS.test(command) ? "design" : "ask_again";
  if (verdict === "yes") return "submit";
  if (verdict === "no") return "cancel";
  return "ask_again";
}

// Saying just a page name opens that page of Sales & Finance (every tab, Thai or English).
// Only the bare name, optionally with words such as "open / show / go to / เปิด / ไปที่ / หน้า", counts: a longer
// sentence ("สร้างใบเสนอราคาให้ลูกค้า ABC", "ใบเสนอราคา PDF สำหรับแคมเปญ") keeps going to the usual flows.
export interface QtcPage {
  path: string;
  name: "overview" | "quotations" | "newQuotation" | "orders" | "invoices" | "newInvoice" | "payments" | "customers" | "newCustomer" | "services" | "newService";
}

// `create`: where a "want to make one" request goes (the new-quotation and new-invoice forms).
const QTC_PAGES: { page: QtcPage; create?: QtcPage; words: string[] }[] = [
  { page: { path: "/qtc/quotations", name: "quotations" }, create: { path: "/qtc/quotations/new", name: "newQuotation" }, words: ["ใบเสนอราคา", "quotation", "quotations", "quote", "quotes"] },
  { page: { path: "/qtc/orders", name: "orders" }, words: ["ใบสั่งขาย", "คำสั่งขาย", "ออเดอร์", "order", "orders", "salesorder", "salesorders"] },
  { page: { path: "/qtc/invoices", name: "invoices" }, create: { path: "/qtc/invoices/new", name: "newInvoice" }, words: ["ใบแจ้งหนี้", "invoice", "invoices"] },
  { page: { path: "/qtc/payments", name: "payments" }, words: ["การรับชำระ", "รับชำระ", "การชำระเงิน", "ชำระเงิน", "payment", "payments"] },
  {
    page: { path: "/qtc/customers", name: "customers" },
    create: { path: "/qtc/customers?new=1", name: "newCustomer" },
    words: ["รายชื่อลูกค้า", "ลูกค้า", "customer", "customers", "client", "clients"],
  },
  {
    page: { path: "/qtc/services", name: "services" },
    create: { path: "/qtc/services?new=1", name: "newService" },
    words: ["บริการและราคา", "รายการราคา", "ราคาบริการ", "บริการ", "ราคา", "service", "services", "pricelist", "pricelists", "price", "prices", "pricing", "ratecard", "ratecards"],
  },
  // Last, so a page name above always wins; "ภาพรวม" alone is the app's main dashboard, not this one.
  {
    page: { path: "/qtc", name: "overview" },
    words: ["งานขายและการเงิน", "ขายและการเงิน", "ภาพรวมงานขาย", "ภาพรวมการขาย", "แดชบอร์ดการขาย", "salesandfinance", "salesfinance", "salesdashboard", "salesoverview"],
  },
];
// "I want to do the quotations" / "ต้องการสร้างใบแจ้งหนี้" open the matching "new" form where there is one.
const NAV_FILLER_EN = ["go to", "take me to", "would like to", "like to", "want to", "i want", "please", "open", "show", "see", "view",
  "create", "make", "do", "new", "add", "and", "go", "the", "a", "an", "to", "i", "d", "page", "list", "all", "my", "me"];
const NAV_FILLER_TH = ["ต้องการ", "อยาก", "สร้าง", "ทำ", "เพิ่ม", "ฉัน", "ผม", "เรา", "ขอดู", "เปิด", "ไปที่", "ไป", "ดู", "หน้า", "รายการ",
  "ทั้งหมด", "ใหม่", "ให้หน่อย", "หน่อย", "ค่ะ", "คะ", "ครับ", "นะ", "ที่"];

// Words that mean "I want to make one" rather than "show me the list".
const CREATE_WORDS = /ต้องการ|อยาก|สร้าง|ทำ|ใหม่|เพิ่ม|\b(want|would like|like to|create|make|do|new|add)\b/i;

export function qtcPageFor(text: string): QtcPage | null {
  const wantsNew = CREATE_WORDS.test(text);
  let s = text.toLowerCase().replace(/[.,!?;:"'“”()&]/g, " ");
  for (const w of NAV_FILLER_EN) s = s.replace(new RegExp(`\\b${w}\\b`, "g"), " ");
  s = s.replace(/\s+/g, "");
  // Thai filler words only around the name, so the name itself is never altered
  for (const { page, create, words } of QTC_PAGES) {
    for (const word of words) {
      const i = s.indexOf(word);
      if (i < 0) continue;
      let rest = s.slice(0, i) + " " + s.slice(i + word.length);
      for (const f of NAV_FILLER_TH) rest = rest.split(f).join("");
      if (rest.trim() === "") return wantsNew && create ? create : page;
    }
  }
  return null;
}
