"use client";

import { CafeShell, ModuleOff } from "@/components/cafe/CafeShell";

// Designed, not built yet (see docs/MCR_SMART_CAFE_PRD.md). Even if the flag is switched on, nothing here pretends to be live.
export default function CafeReportsPage() {
  return <CafeShell module="reports">{() => <ModuleOff module="reports" />}</CafeShell>;
}
