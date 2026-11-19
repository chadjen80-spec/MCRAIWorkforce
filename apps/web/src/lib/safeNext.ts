/** Where to go after login: only same-site relative paths, never back to /login or the API. */
export function safeNext(next: string | null): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return "/assistant";
  if (next.startsWith("/login") || next.startsWith("/api/")) return "/assistant";
  return next;
}
