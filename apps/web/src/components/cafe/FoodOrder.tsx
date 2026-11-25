"use client";

// Food & drink ordering on the seat screen: search and categories on the left, the menu in the middle, the cart on the
// right. The cart total shown here is only a preview; the server prices every line from the menu when the order is
// placed and its total is the one shown afterwards. Payment is at the counter (no payment provider is connected).

import { useMemo, useState } from "react";
import { Badge, Button, ErrorState, cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/dictionaries";
import { newIdempotencyKey, toApiError, type ApiError } from "@/lib/api";
import { formatBaht, menuApi, sumBaht, type CafeOrderRow, type MenuItem } from "@/lib/cafe";
import { useApi } from "@/lib/useApi";

const glow = "shadow-[0_0_24px_-6px_rgba(34,211,238,0.55)]";
const MAX_QTY = 20;

export function FoodOrder({ seatId, seatLabel, onOrdered }: { seatId: string; seatLabel: string; onOrdered?: (o: CafeOrderRow) => void }) {
  const { t } = useI18n();
  const menu = useApi("seat-menu", (s) => menuApi.menu(false, s), { interval: 30000 });
  const [category, setCategory] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [cart, setCart] = useState<Record<string, number>>({});
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [placed, setPlaced] = useState<CafeOrderRow | null>(null);
  const [key, setKey] = useState(newIdempotencyKey);

  const items = useMemo(() => menu.data?.items ?? [], [menu.data]);
  const byId = useMemo(() => new Map(items.map((i) => [i.id, i])), [items]);
  const shown = items.filter((i) =>
    query.trim() ? `${i.name} ${i.aliases.join(" ")}`.toLowerCase().includes(query.trim().toLowerCase()) : !category || i.category_id === category,
  );
  const lines = Object.entries(cart)
    .map(([id, quantity]) => ({ item: byId.get(id), quantity }))
    .filter((l): l is { item: MenuItem; quantity: number } => Boolean(l.item));
  const preview = sumBaht(lines.map((l) => ({ price: l.item.price, quantity: l.quantity })));

  const change = (id: string, delta: number) => {
    setPlaced(null);
    setCart((c) => {
      const next = Math.max(0, Math.min(MAX_QTY, (c[id] ?? 0) + delta));
      const copy = { ...c };
      if (next === 0) delete copy[id];
      else copy[id] = next;
      return copy;
    });
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const order = await menuApi.placeOrder(seatId, lines.map((l) => ({ item_id: l.item.id, quantity: l.quantity })), note, key);
      setPlaced(order);
      setCart({});
      setNote("");
      setKey(newIdempotencyKey()); // the next order gets a new key; a retry of this one reused the same key
      onOrdered?.(order);
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setBusy(false);
    }
  };

  if (menu.error && !menu.data) return <ErrorState error={menu.error} onRetry={menu.reload} />;
  if (!menu.data) return <p className="p-6 text-sm text-dim">{t("common.loading")}</p>;
  if (items.length === 0) return <p className="rounded-xl border border-dashed border-line p-8 text-center text-sm text-dim">{t("food.emptyMenu")}</p>;

  return (
    <div className="grid gap-3 lg:grid-cols-[160px_1fr_280px]">
      {/* categories */}
      <nav aria-label={t("food.categories")} className="space-y-1">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("food.search")}
          aria-label={t("food.search")}
          className="mb-2 w-full rounded-lg border border-line bg-raised px-2 py-1.5 text-sm text-ink"
        />
        {[{ id: null as string | null, name: t("food.all") }, ...menu.data.categories].map((c) => (
          <button
            key={c.id ?? "all"}
            type="button"
            onClick={() => {
              setCategory(c.id);
              setQuery("");
            }}
            aria-pressed={category === c.id && !query}
            className={cx(
              "block w-full rounded-lg px-3 py-2 text-left text-sm font-medium transition",
              category === c.id && !query ? `bg-brand text-white ${glow}` : "text-dim hover:bg-raised hover:text-ink",
            )}
          >
            {c.name}
          </button>
        ))}
      </nav>

      {/* menu */}
      <ul className="grid max-h-[60vh] grid-cols-2 gap-2 overflow-auto pr-1 sm:grid-cols-3" aria-label={t("food.menu")}>
        {shown.map((item) => (
          <li key={item.id}>
            <button
              type="button"
              disabled={!item.available}
              onClick={() => change(item.id, 1)}
              aria-label={`${item.name} ${formatBaht(item.price)} ${t("food.baht")}`}
              className={cx(
                "flex h-full w-full flex-col overflow-hidden rounded-xl border text-left transition",
                item.available ? "border-line bg-[#0b1324] hover:border-cyan" : "border-line bg-raised opacity-50",
              )}
            >
              <span className="relative flex h-20 items-center justify-center bg-gradient-to-br from-brand/30 to-cyan/10 text-2xl font-bold text-cyan/80">
                {item.name.slice(0, 2)}
                <span className="absolute bottom-1 left-1 flex gap-1">
                  {item.list_price && <Badge tone="danger">{t("food.sale")}</Badge>}
                  {item.is_new && <Badge tone="info">{t("food.new")}</Badge>}
                </span>
                {(cart[item.id] ?? 0) > 0 && (
                  <span className="absolute right-1 top-1 rounded-full bg-cyan px-2 text-xs font-bold text-bg">{cart[item.id]}</span>
                )}
              </span>
              <span className="flex flex-1 flex-col justify-between gap-1 p-2">
                <span className="text-sm text-ink">{item.name}</span>
                <span className="flex items-baseline gap-2">
                  <span className={cx("font-semibold tabular-nums", item.list_price ? "text-bad" : "text-ink")}>
                    {formatBaht(item.price)} {t("food.baht")}
                  </span>
                  {item.list_price && <s className="text-xs text-dim">{formatBaht(item.list_price)}</s>}
                </span>
                {!item.available && <span className="text-xs text-warn">{t("food.soldOut")}</span>}
              </span>
            </button>
          </li>
        ))}
      </ul>

      {/* cart */}
      <aside aria-label={t("food.cart")} className="flex flex-col rounded-xl border border-cyan/30 bg-[#070c17] p-3">
        <p className="text-sm font-semibold text-ink">
          {t("food.cartFor", { seat: seatLabel })}
        </p>
        {lines.length === 0 ? (
          <p className="my-6 text-center text-sm text-dim">{placed ? "" : t("food.cartEmpty")}</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {lines.map(({ item, quantity }) => (
              <li key={item.id} className="rounded-lg bg-raised px-2 py-1.5 text-sm">
                <div className="flex justify-between gap-2">
                  <span className="text-ink">{item.name}</span>
                  <button type="button" onClick={() => change(item.id, -quantity)} className="text-xs text-dim hover:text-bad">
                    {t("food.remove")}
                  </button>
                </div>
                <div className="mt-1 flex items-center gap-2">
                  <button type="button" aria-label={t("food.less", { item: item.name })} onClick={() => change(item.id, -1)}
                    className="size-6 rounded-full bg-line text-ink">−</button>
                  <span className="w-5 text-center tabular-nums">{quantity}</span>
                  <button type="button" aria-label={t("food.more", { item: item.name })} onClick={() => change(item.id, 1)}
                    disabled={quantity >= MAX_QTY} className="size-6 rounded-full bg-line text-ink">+</button>
                  <span className="ml-auto tabular-nums text-ink">
                    {formatBaht(sumBaht([{ price: item.price, quantity }]))} {t("food.baht")}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
        <div className="mt-auto space-y-2 pt-3">
          <div className="flex items-baseline justify-between border-t border-line pt-2">
            <span className="text-sm text-dim">{t("food.total")}</span>
            <span className="text-xl font-semibold tabular-nums text-cyan">{formatBaht(preview)} {t("food.baht")}</span>
          </div>
          <input
            value={note}
            onChange={(e) => setNote(e.target.value.slice(0, 50))}
            maxLength={50}
            placeholder={t("food.note")}
            aria-label={t("food.note")}
            className="w-full rounded-lg border border-line bg-raised px-2 py-1.5 text-sm text-ink"
          />
          <p className="text-xs text-dim">{t("food.payAtCounter")}</p>
          <Button variant="primary" className="w-full" disabled={lines.length === 0} loading={busy} onClick={submit}>
            {t("food.placeOrder")}
          </Button>
          {error && <ErrorState error={error} compact />}
          {placed && (
            <p role="status" className="rounded-lg border border-ok/40 bg-ok/10 p-2 text-sm text-ink">
              {t("food.placed", { number: String(placed.number), total: formatBaht(placed.total) })}
            </p>
          )}
        </div>
      </aside>
    </div>
  );
}

export const ORDER_STATUS: Record<CafeOrderRow["status"], { key: MessageKey; tone: "neutral" | "info" | "success" | "danger" }> = {
  placed: { key: "food.status.placed", tone: "info" },
  preparing: { key: "food.status.preparing", tone: "neutral" },
  delivered: { key: "food.status.delivered", tone: "success" },
  cancelled: { key: "food.status.cancelled", tone: "danger" },
};

/** One seat's orders, newest first, with their real status and payment state. */
export function SeatOrders({ seatId }: { seatId: string }) {
  const { t } = useI18n();
  const orders = useApi(`seat-orders-${seatId}`, (s) => menuApi.orders("all", seatId, s), { interval: 5000 });
  if (orders.error && !orders.data) return <ErrorState error={orders.error} onRetry={orders.reload} />;
  const rows = orders.data?.items ?? [];
  if (rows.length === 0) return <p className="rounded-xl border border-dashed border-line p-8 text-center text-sm text-dim">{t("food.noOrders")}</p>;
  return (
    <ul className="space-y-2">
      {rows.map((o) => (
        <li key={o.id} className="rounded-xl border border-line bg-[#0b1324] p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold text-ink">#{o.number}</span>
            <Badge tone={ORDER_STATUS[o.status].tone}>{t(ORDER_STATUS[o.status].key)}</Badge>
            <Badge tone={o.payment_status === "paid" ? "success" : "warn"}>{t(o.payment_status === "paid" ? "food.paid" : "food.unpaid")}</Badge>
            <span className="ml-auto font-semibold tabular-nums text-cyan">{formatBaht(o.total)} {t("food.baht")}</span>
          </div>
          <p className="mt-1 text-dim">{o.lines.map((l) => `${l.name} ×${l.quantity}`).join(", ")}</p>
        </li>
      ))}
    </ul>
  );
}
