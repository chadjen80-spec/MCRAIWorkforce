"use client";

import { CafeShell } from "@/components/cafe/CafeShell";
import { DevicesBoard } from "@/components/cafe/DevicesBoard";

export default function CafeDevicesPage() {
  return <CafeShell module="devices">{() => <DevicesBoard />}</CafeShell>;
}
