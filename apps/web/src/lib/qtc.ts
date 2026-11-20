// Quote-to-Cash API types and helpers. Amounts arrive as exact decimal strings and are formatted as
// strings: they are never converted to JavaScript numbers, so no binary rounding can creep in.

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface Contact {
  id: string;
  customer_id: string;
  name: string;
  title: string;
  email: string;
  phone: string;
  line_id: string;
  is_primary: boolean;
  consent_email: boolean;
  consent_line: boolean;
}

export interface Customer {
  id: string;
  code: string;
  name: string;
  legal_name: string;
  customer_type: "company" | "individual";
  tax_id: string | null;
  branch: string;
  address: string;
  email: string;
  phone: string;
  payment_terms_days: number;
  notes: string;
  status: "active" | "archived";
  created_at: string;
  updated_at: string;
  contacts: Contact[];
}

export interface Service {
  id: string;
  code: string;
  name: string;
  name_en: string;
  description: string;
  category: string;
  unit: string;
  keywords: string[];
  vat_exempt: boolean;
  status: "active" | "archived";
  default_price: string | null;
}

export interface PriceItem {
  service_id: string;
  service_code: string;
  service_name: string;
  unit: string;
  unit_price: string;
  notes: string;
}

export interface PriceList {
  id: string;
  name: string;
  currency: string;
  prices_include_vat: boolean;
  vat_rate: string;
  valid_from: string | null;
  valid_to: string | null;
  status: "draft" | "active" | "archived";
  is_default: boolean;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string;
  items: PriceItem[];
}

const DECIMAL = /^-?\d+(\.\d+)?$/;

/** "45000.0000" -> "45,000.00" (2 places, half up, done on the digits; never via Number). */
export function formatAmount(value: string | null | undefined, places = 2): string {
  if (value === null || value === undefined || !DECIMAL.test(value)) return "–";
  const negative = value.startsWith("-");
  const [intRaw, fracRaw = ""] = (negative ? value.slice(1) : value).split(".");
  let digits = (intRaw.replace(/^0+(?=\d)/, "") + fracRaw.padEnd(places + 1, "0").slice(0, places + 1)).split("").map(Number);
  // round half up on the digit after `places`
  const roundUp = digits.pop()! >= 5;
  if (roundUp) {
    let i = digits.length - 1;
    while (i >= 0) {
      if (digits[i] === 9) {
        digits[i] = 0;
        i--;
      } else {
        digits[i]++;
        break;
      }
    }
    if (i < 0) digits = [1, ...digits];
  }
  const all = digits.join("");
  const int = all.slice(0, all.length - places) || "0";
  const frac = places ? all.slice(all.length - places) : "";
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const zero = /^0+$/.test(int + frac);
  return `${negative && !zero ? "-" : ""}${grouped}${places ? `.${frac}` : ""}`;
}

/** "0.0700" -> "7%" */
export function formatRate(rate: string): string {
  if (!DECIMAL.test(rate)) return "–";
  const [i, f = ""] = rate.split(".");
  const shifted = `${i}${f.padEnd(2, "0").slice(0, 2)}.${f.slice(2)}`.replace(/^0+(?=\d)/, "");
  return `${shifted.replace(/\.?0+$/, "")}%`;
}

/** Thai tax ID check digit (same rule as the server, for instant feedback). */
export function thaiTaxIdValid(raw: string): boolean {
  const id = raw.replace(/[\s-]/g, "");
  if (!/^\d{13}$/.test(id)) return false;
  let sum = 0;
  for (let i = 0; i < 12; i++) sum += Number(id[i]) * (13 - i);
  return (11 - (sum % 11)) % 10 === Number(id[12]);
}

/** A plain decimal string a user typed ("45,000.50" -> "45000.50"), or null if it is not a valid amount. */
export function parseAmountInput(raw: string): string | null {
  const v = raw.replace(/[,\s฿]/g, "");
  return /^\d+(\.\d{1,4})?$/.test(v) ? v : null;
}

// ---- quotations (Phase B) ------------------------------------------------------------------------
export type QuotationStatus = "draft" | "pending_approval" | "approved" | "sent" | "accepted" | "declined" | "cancelled" | "expired";

export interface QuotationLine {
  position: number;
  service_id: string | null;
  description: string;
  unit: string;
  quantity: string;
  unit_price: string | null;
  price_source: "price_list" | "manual" | "missing";
  discount_percent: string;
  discount_amount: string;
  vat_exempt: boolean;
  line_net: string;
  line_vat: string;
  line_total: string;
}

export interface Quotation {
  id: string;
  number: string;
  revision: number;
  status: QuotationStatus;
  customer: { id: string; code: string; name: string };
  contact_id: string | null;
  price_list_id: string;
  title: string;
  brief: string;
  prices_include_vat: boolean;
  vat_rate: string;
  issue_date: string;
  valid_until: string;
  notes: string;
  terms: string;
  subtotal: string;
  discount_total: string;
  vat_total: string;
  grand_total: string;
  amount_in_words: string;
  missing_prices: number;
  created_by: string;
  approved_by: string | null;
  approved_at: string | null;
  sent_at: string | null;
  sent_method: string | null;
  accepted_at: string | null;
  accepted_revision: number | null;
  acceptance: Record<string, string | null> | null;
  lines: QuotationLine[];
  matched: { service_id: string; code: string; name: string; matched: string }[];
}

/** One editable line in the quotation form (strings as typed; validated with parseAmountInput). */
export interface LineDraft {
  key: string;
  service_id: string | null;
  description: string;
  unit: string;
  quantity: string;
  unit_price: string;
  discount_percent: string;
}

/** Request body for lines: empty price -> null (the approved price list prices it, or a person must). */
export function linesBody(lines: LineDraft[]): Record<string, unknown>[] {
  return lines.map((l) => ({
    service_id: l.service_id,
    description: l.description.trim(),
    unit: l.unit.trim(),
    quantity: parseQuantity(l.quantity) ?? "1",
    unit_price: l.unit_price.trim() === "" ? null : parseAmountInput(l.unit_price),
    discount_percent: parseQuantity(l.discount_percent) ?? "0",
  }));
}

export function parseQuantity(raw: string): string | null {
  const v = raw.replace(/[,\s]/g, "");
  return /^\d+(\.\d{1,3})?$/.test(v) && !/^0+(\.0+)?$/.test(v) ? v : v === "0" ? "0" : null;
}

/** "10.000" -> "10", "1.500" -> "1.5", "10" stays "10" (only the decimal part is trimmed). */
export function trimZeros(v: string): string {
  return v.includes(".") ? v.replace(/0+$/, "").replace(/\.$/, "") : v;
}

export const lineFromQuotation = (l: QuotationLine): LineDraft => ({
  key: `${l.position}-${l.service_id ?? l.description}`,
  service_id: l.service_id,
  description: l.description,
  unit: l.unit,
  quantity: trimZeros(l.quantity),
  // keep a price only when a person typed it; a price-list price is looked up again on save
  unit_price: l.price_source === "manual" && l.unit_price ? formatAmount(l.unit_price).replace(/,/g, "") : "",
  discount_percent: trimZeros(l.discount_percent),
});

// ---- orders, invoices, payments (Phase C) -----------------------------------------------------------
export interface Milestone {
  id: string;
  position: number;
  kind: "deposit" | "installment" | "final" | "delivery";
  description: string;
  due_date: string | null;
  amount_net: string;
  amount_vat: string;
  amount_total: string;
  status: "pending" | "invoiced" | "done" | "cancelled";
  invoice_id: string | null;
}

export interface Order {
  id: string;
  number: string;
  status: "open" | "completed" | "cancelled";
  quotation_id: string;
  quotation_number: string;
  quotation_revision: number;
  customer: { id: string; code: string; name: string };
  grand_total: string;
  vat_total: string;
  invoiced_total: string;
  paid_total: string;
  created_at: string;
  lines: { position: number; description: string; unit: string; quantity: string; line_total: string }[];
  milestones: Milestone[];
}

export type InvoiceStatus = "draft" | "issued" | "partially_paid" | "paid" | "cancelled" | "overdue";

export interface Invoice {
  id: string;
  number: string | null;
  status: InvoiceStatus;
  order_id: string;
  order_number: string;
  customer: { id: string; code: string; name: string };
  issue_date: string | null;
  due_date: string | null;
  vat_rate: string;
  subtotal: string;
  vat_total: string;
  total: string;
  paid_amount: string;
  balance: string;
  pending_payments: string;
  amount_in_words: string;
  lines: { position: number; description: string; quantity: string; unit: string; line_total: string }[];
  allocations: { payment_id: string; payment_number: string; amount: string; status: string; received_on: string }[];
}

export interface Payment {
  id: string;
  number: string;
  status: "pending_confirmation" | "confirmed" | "rejected";
  customer: { id: string; code: string; name: string };
  amount: string;
  allocated: string;
  unallocated: string;
  received_on: string;
  method: string;
  reference: string;
  has_evidence: boolean;
  evidence_name: string | null;
  recorded_by: string;
  confirmed_by: string | null;
  rejected_reason: string | null;
  allocations: { invoice_id: string; invoice_number: string | null; amount: string }[];
}

/** A billing schedule typed as percentages; valid only when they add up to exactly 100 (checked on the digits). */
export function percentsTotal(values: string[]): string | null {
  let hundredths = 0;
  for (const v of values) {
    if (!/^\d{1,3}(\.\d{1,2})?$/.test(v.trim())) return null;
    const [i, f = ""] = v.trim().split(".");
    hundredths += Number(i) * 100 + Number(f.padEnd(2, "0"));
  }
  return `${Math.floor(hundredths / 100)}.${String(hundredths % 100).padStart(2, "0")}`;
}
