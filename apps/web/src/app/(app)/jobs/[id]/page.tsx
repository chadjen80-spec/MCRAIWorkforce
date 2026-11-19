import { Suspense } from "react";
import { JobMonitor } from "./JobMonitor";

export default function JobPage() {
  return (
    <Suspense fallback={null}>
      <JobMonitor />
    </Suspense>
  );
}
