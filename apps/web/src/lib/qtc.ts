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
