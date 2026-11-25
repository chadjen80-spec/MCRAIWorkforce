"use client";

import { CafeShell } from "@/components/cafe/CafeShell";
import { MenuEditor, OrdersBoard } from "@/components/cafe/OrdersBoard";

export default function CafeOrdersPage() {
  return (
    <CafeShell module="orders">
      {() => (
        <div className="space-y-10">
          <OrdersBoard />
          <MenuEditor />
        </div>
      )}
    </CafeShell>
  );
}
