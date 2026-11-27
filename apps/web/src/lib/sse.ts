/**
 * Minimal SSE client used by the chat screen.
 *
 *  - `streamPost` reads the POST stream of `/v1/chat/stream` with `fetch` (EventSource is
 *    GET-only and we need the CSRF header + JSON body). It never reconnects: a failed turn is
 *    reported to the caller, which shows the error to the merchant. An HTTP error becomes an
 *    `SseHttpError` carrying the status and, on 429, the `Retry-After` seconds.
 *  - `openGetStream` listens to `/v1/conversations/{id}/events` with the browser's EventSource.
 *    Reconnection is the browser's own: when the connection drops or the server closes the
 *    channel (it does so every ~60 s by design), EventSource reopens it by itself. Every server
 *    event carries an `id`, so an event received again after a reopen is skipped. When the
 *    browser gives up (an HTTP error such as 401, 403, 404 or 429 closes the EventSource for
 *    good), we do NOT retry: the channel stays closed and `onError` is called once.
 */

import { readCsrfCookie } from "./csrf";

export type SseHandlers = {
  onEvent?: (name: string, data: unknown) => void;
  onError?: (err: Error) => void;
  onDone?: () => void;
};

/** HTTP failure of a stream request; `retryAfterSeconds` comes from the `Retry-After` header. */
export class SseHttpError extends Error {
  constructor(
    readonly status: number,
    readonly retryAfterSeconds: number | null,
  ) {
    super(`sse_http_${status}`);
    this.name = "SseHttpError";
  }
}

/** `Retry-After` as seconds (delta form only; an HTTP-date or garbage gives null). */
export function parseRetryAfter(value: string | null): number | null {
  if (value === null) return null;
  const seconds = Number(value.trim());
  return Number.isFinite(seconds) && seconds >= 0 ? Math.ceil(seconds) : null;
}

export const CONVERSATION_EVENTS = [
  "handoff_update",
  "final_message",
  "sla_breached",
  "handoff_expired",
] as const;

export async function streamPost(
  path: string,
  body: unknown,
  handlers: SseHandlers,
  signal: AbortSignal,
): Promise<void> {
  const headers = new Headers({
    Accept: "text/event-stream",
    "Content-Type": "application/json",
  });
  const csrf = readCsrfCookie();
  if (csrf !== null) {
    headers.set("X-CSRF-Token", csrf);
  }
  const response = await fetch(path, {
    method: "POST",
    credentials: "include",
    headers,
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    const err = new SseHttpError(
      response.status,
      parseRetryAfter(response.headers.get("Retry-After")),
    );
    handlers.onError?.(err);
    throw err;
  }
  if (response.body === null) {
    throw new Error("sse_no_body");
  }
  await readLoop(response.body.getReader(), handlers, signal);
}

export function openGetStream(path: string, handlers: SseHandlers, signal: AbortSignal): void {
  if (signal.aborted) {
    return;
  }
  const seen = new Set<string>();
  const source = new EventSource(path, { withCredentials: true });

  const handle = (name: string, event: MessageEvent<string>) => {
    if (event.lastEventId) {
      const key = `${name}:${event.lastEventId}`;
      if (seen.has(key)) {
        return;
      }
      seen.add(key);
    }
    try {
      const data: unknown = event.data ? JSON.parse(event.data) : {};
      handlers.onEvent?.(name, data);
    } catch (err) {
      handlers.onError?.(err as Error);
    }
  };

  for (const name of CONVERSATION_EVENTS) {
    source.addEventListener(name, (event) => handle(name, event as MessageEvent<string>));
  }
  source.onerror = () => {
    if (signal.aborted) {
      source.close();
      return;
    }
    // CONNECTING: the browser is already reopening the channel; nothing to do.
    if (source.readyState === EventSource.CLOSED) {
      handlers.onError?.(new Error("sse_closed"));
    }
  };
  signal.addEventListener("abort", () => source.close(), { once: true });
}

// SSE allows events separated by LF-LF or CRLF-CRLF (mixed is also legal per the HTML spec).
// Starlette's EventSourceResponse emits CRLF; our parser used to look only for LF, and silently
// dropped every event. See docs/reports/e2e-browser.md achado 3.
const EVENT_SEPARATOR = /\r?\n\r?\n/;
const LINE_SEPARATOR = /\r?\n/;

async function readLoop(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  handlers: SseHandlers,
  signal: AbortSignal,
): Promise<void> {
  const decoder = new TextDecoder();
  let buffer = "";
  while (!signal.aborted) {
    const { value, done } = await reader.read();
    if (done) {
      handlers.onDone?.();
      return;
    }
    buffer += decoder.decode(value, { stream: true });
    let match = EVENT_SEPARATOR.exec(buffer);
    while (match !== null) {
      const chunk = buffer.slice(0, match.index);
      buffer = buffer.slice(match.index + match[0]!.length);
      dispatchChunk(chunk, handlers);
      match = EVENT_SEPARATOR.exec(buffer);
    }
  }
}

function dispatchChunk(chunk: string, handlers: SseHandlers): void {
  let event = "message";
  let data = "";
  for (const line of chunk.split(LINE_SEPARATOR)) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      data += line.slice(5).trim();
    }
  }
  if (data === "") {
    return;
  }
  try {
    handlers.onEvent?.(event, JSON.parse(data));
  } catch (err) {
    handlers.onError?.(err as Error);
  }
  if (event === "done") {
    handlers.onDone?.();
  }
}
