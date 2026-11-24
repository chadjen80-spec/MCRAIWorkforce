"use client";

import { CafeShell, ModuleOff } from "@/components/cafe/CafeShell";

// Designed, not built yet (see docs/MCR_SMART_CAFE_PRD.md). Even if the flag is switched on, nothing here pretends to be live.
export default function CafePrintingPage() {
  return <CafeShell module="printing">{() => <ModuleOff module="printing" />}</CafeShell>;
}
