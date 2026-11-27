/** Double-submit CSRF: the API sets a readable `csrf` cookie, and every mutation echoes it in
 * the `X-CSRF-Token` header. One reader for `api.ts` and `sse.ts`. */
export function readCsrfCookie(): string | null {
  const match = document.cookie.match(/(?:^|;\s*)csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1]!) : null;
}
