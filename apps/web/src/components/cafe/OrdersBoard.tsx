"use client";

// /cafe/orders: the kitchen/counter board (open orders, newest first, refreshed every 3 s) and the menu editor.
// Staff move an order placed -> preparing -> delivered, or cancel it, and mark it paid when the customer pays at the counter.

import { useState } from "react";
import { Badge, Button, Card, EmptyState, ErrorState, LoadingBlock, SectionTitle, Switch, cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { toApiError, type ApiError } from "@/lib/api";
import { formatBaht, menuApi, type CafeOrderRow } from "@/lib/cafe";
import { useApi } from "@/lib/useApi";
import { ORDER_STATUS } from "./FoodOrder";

export function OrdersBoard() {
  const { t } = useI18n();
  const [all, setAll] = useState(false);
  const orders = useApi(`cafe-orders-${all}`, (s) => menuApi.orders(all ? "all" : "open", undefined, s), { interval: 3000 });
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const act = async (id: string, fn: () => Promise<unknown>) => {
    setBusy(id);
    setError(null);
    try {
      await fn();
      orders.reload();
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section>
      <SectionTitle aside={<Switch checked={all} onChange={setAll} label={t("food.showAll")} />}>{t("food.board")}</SectionTitle>
      {error && (
        <div className="mb-3">
          <ErrorState error={error} compact />
        </div>
      )}
      {orders.loading && <LoadingBlock />}
      {orders.error && !orders.data && <ErrorState error={orders.error} onRetry={orders.reload} />}
      {orders.data && orders.data.items.length === 0 && <EmptyState title={t("food.noOpenOrders")} />}
      <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3" aria-label={t("food.board")}>
        {(orders.data?.items ?? []).map((o) => (
          <li key={o.id}>
            <OrderCard order={o} busy={busy === o.id} act={(fn) => act(o.id, fn)} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function OrderCard({ order: o, busy, act }: { order: CafeOrderRow; busy: boolean; act: (fn: () => Promise<unknown>) => void }) {
  const { t } = useI18n();
  const open = o.status === "placed" || o.status === "preparing";
  return (
    <Card className={cx("p-4", o.status === "placed" && "border-cyan/50")}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-lg font-semibold text-ink">#{o.number}</span>
        <span className="text-sm text-dim">
          {t("cafe.seat")} {o.seat_label}
        </span>
        <Badge tone={ORDER_STATUS[o.status].tone}>{t(ORDER_STATUS[o.status].key)}</Badge>
        <Badge tone={o.payment_status === "paid" ? "success" : "warn"}>{t(o.payment_status === "paid" ? "food.paid" : "food.unpaid")}</Badge>
      </div>
      <ul className="mt-2 space-y-0.5 text-sm">
        {o.lines.map((l, i) => (
          <li key={i} className="flex justify-between gap-2">
            <span className="text-ink">
              {l.name} ×{l.quantity}
            </span>
            <span className="tabular-nums text-dim">{formatBaht(l.line_total)}</span>
          </li>
        ))}
      </ul>
      {o.note && <p className="mt-1 text-sm text-warn">“{o.note}”</p>}
      <div className="mt-2 flex items-baseline justify-between border-t border-line pt-2">
        <span className="text-xs text-dim">
          {new Date(o.created_at).toLocaleTimeString()} · {o.via}
        </span>
        <span className="font-semibold tabular-nums text-cyan">
          {formatBaht(o.total)} {t("food.baht")}
        </span>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {o.status === "placed" && (
          <Button size="sm" variant="primary" loading={busy} onClick={() => act(() => menuApi.setStatus(o.id, "preparing"))}>
            {t("food.startPreparing")}
          </Button>
        )}
        {o.status === "preparing" && (
          <Button size="sm" variant="primary" loading={busy} onClick={() => act(() => menuApi.setStatus(o.id, "delivered"))}>
            {t("food.markDelivered")}
          </Button>
        )}
        {o.payment_status === "unpaid" && o.status !== "cancelled" && (
          <Button size="sm" loading={busy} onClick={() => act(() => menuApi.markPaid(o.id))}>
            {t("food.markPaid")}
          </Button>
        )}
        {open && o.payment_status === "unpaid" && (
          <Button size="sm" variant="ghost" loading={busy} onClick={() => act(() => menuApi.setStatus(o.id, "cancelled"))}>
            {t("food.cancel")}
          </Button>
        )}
      </div>
    </Card>
  );
}

const input = "mt-1 block w-full rounded-lg border border-line bg-raised px-3 py-2 text-ink";

export function MenuEditor() {
  const { t } = useI18n();
  const menu = useApi("cafe-menu-editor", (s) => menuApi.menu(true, s));
  const [catName, setCatName] = useState("");
  const [draft, setDraft] = useState({ category_id: "", name: "", aliases: "", price: "", list_price: "", is_new: false });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      menu.reload();
      return true;
    } catch (e) {
      setError(toApiError(e));
      return false;
    } finally {
      setBusy(false);
    }
  };

  if (menu.loading) return <LoadingBlock />;
  if (!menu.data) return menu.error ? <ErrorState error={menu.error} onRetry={menu.reload} /> : null;
  const cats = menu.data.categories;

  return (
    <section className="space-y-4">
      <SectionTitle>{t("food.menuEditor")}</SectionTitle>
      <p className="text-sm text-dim">{t("food.menuNote")}</p>
      {error && <ErrorState error={error} compact />}
      <Card className="p-4">
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={async (e) => {
            e.preventDefault();
            if (catName.trim() && (await run(() => menuApi.createCategory(catName.trim(), cats.length + 1)))) setCatName("");
          }}
        >
          <label className="text-sm text-dim">
            {t("food.categoryName")}
            <input className={input} value={catName} maxLength={64} onChange={(e) => setCatName(e.target.value)} />
          </label>
          <Button type="submit" loading={busy} disabled={!catName.trim()}>
            {t("food.addCategory")}
          </Button>
        </form>
      </Card>
      {cats.length > 0 && (
        <Card className="p-4">
          <form
            className="grid gap-3 sm:grid-cols-3"
            onSubmit={async (e) => {
              e.preventDefault();
              const ok = await run(() =>
                menuApi.createItem({
                  category_id: draft.category_id || cats[0].id,
                  name: draft.name.trim(),
                  aliases: draft.aliases.split(",").map((a) => a.trim()).filter(Boolean),
                  price: draft.price.trim(),
                  list_price: draft.list_price.trim() || null,
                  is_new: draft.is_new,
                }),
              );
              if (ok) setDraft({ ...draft, name: "", aliases: "", price: "", list_price: "", is_new: false });
            }}
          >
            <label className="text-sm text-dim">
              {t("food.category")}
              <select className={input} value={draft.category_id || cats[0].id} onChange={(e) => setDraft({ ...draft, category_id: e.target.value })}>
                {cats.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm text-dim">
              {t("food.itemName")}
              <input className={input} required maxLength={128} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            </label>
            <label className="text-sm text-dim">
              {t("food.aliases")}
              <input className={input} value={draft.aliases} onChange={(e) => setDraft({ ...draft, aliases: e.target.value })} />
            </label>
            <label className="text-sm text-dim">
              {t("food.price")}
              <input className={input} required inputMode="decimal" pattern="\d{1,6}(\.\d{1,2})?" value={draft.price}
                onChange={(e) => setDraft({ ...draft, price: e.target.value })} />
            </label>
            <label className="text-sm text-dim">
              {t("food.listPrice")}
              <input className={input} inputMode="decimal" pattern="\d{1,6}(\.\d{1,2})?" value={draft.list_price}
                onChange={(e) => setDraft({ ...draft, list_price: e.target.value })} />
            </label>
            <div className="flex items-end justify-between gap-2">
              <Switch checked={draft.is_new} onChange={(v) => setDraft({ ...draft, is_new: v })} label={t("food.new")} />
              <Button type="submit" variant="primary" loading={busy}>
                {t("food.addItem")}
              </Button>
            </div>
          </form>
        </Card>
      )}
      {cats.length === 0 ? (
        <EmptyState title={t("food.emptyMenu")} />
      ) : (
        cats.map((c) => (
          <div key={c.id}>
            <div className="mb-2 flex items-center gap-3">
              <h3 className={cx("font-semibold", c.active ? "text-ink" : "text-dim line-through")}>{c.name}</h3>
              <Switch checked={c.active} label={t("food.categoryShown")} onChange={(v) => void run(() => menuApi.patchCategory(c.id, { active: v }))} />
            </div>
            <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {menu.data!.items.filter((i) => i.category_id === c.id).map((i) => (
                <li key={i.id} className="flex items-center justify-between gap-2 rounded-lg border border-line bg-raised px-3 py-2 text-sm">
                  <span className="min-w-0">
                    <span className="block truncate text-ink">{i.name}</span>
                    <span className="tabular-nums text-dim">
                      {formatBaht(i.price)} {t("food.baht")}
                      {i.list_price && <s className="ml-1">{formatBaht(i.list_price)}</s>}
                    </span>
                  </span>
                  <Switch checked={i.available} label={t("food.available")} onChange={(v) => void run(() => menuApi.patchItem(i.id, { available: v }))} />
                </li>
              ))}
            </ul>
          </div>
        ))
      )}
    </section>
  );
}
