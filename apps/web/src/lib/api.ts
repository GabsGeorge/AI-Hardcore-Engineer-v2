/**
 * Typed fetch wrapper for /v1/*.
 *
 * - credentials: "include" so the session cookie goes along; the SPA runs behind the same
 *   origin as the API (nginx proxy), so the browser attaches it without extra config.
 * - On every mutating request we read the `csrf` cookie and echo it in `X-CSRF-Token`.
 * - 401 bubbles as `ApiError("unauthorized")`; the AuthGuard reacts to it.
 */

import { readCsrfCookie } from "./csrf";

const JSON_HEADERS = { "Content-Type": "application/json", Accept: "application/json" };

export type ApiErrorBody = {
  error?: {
    code?: string;
    message?: string;
    details?: { loc?: (string | number)[]; type?: string }[];
  };
  trace_id?: string;
};

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
    public body: ApiErrorBody | null,
    public retryAfterSeconds?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function parseError(response: Response): Promise<ApiError> {
  let body: ApiErrorBody | null = null;
  try {
    body = (await response.json()) as ApiErrorBody;
  } catch {
    body = null;
  }
  const code = body?.error?.code ?? `http_${response.status}`;
  const message = body?.error?.message ?? response.statusText;
  // Só a forma em segundos; uma data HTTP (ou lixo) vira `undefined`, nunca NaN.
  const retrySeconds = Number(response.headers.get("Retry-After")?.trim() ?? "");
  const retryAfterSeconds =
    response.headers.has("Retry-After") && Number.isFinite(retrySeconds) && retrySeconds >= 0
      ? Math.ceil(retrySeconds)
      : undefined;
  return new ApiError(code, message, response.status, body, retryAfterSeconds);
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  init?: RequestInit,
): Promise<T> {
  const headers = new Headers(init?.headers);
  if (body !== undefined) {
    for (const [key, value] of Object.entries(JSON_HEADERS)) {
      headers.set(key, value);
    }
  } else {
    headers.set("Accept", "application/json");
  }
  if (method !== "GET" && method !== "HEAD") {
    const token = readCsrfCookie();
    if (token !== null) {
      headers.set("X-CSRF-Token", token);
    }
  }
  // `init` primeiro: um `init.headers` do chamador já foi mesclado acima e não pode apagar o
  // X-CSRF-Token nem o Content-Type montados aqui.
  const response = await fetch(path, {
    ...init,
    method,
    credentials: "include",
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  if (response.status === 204 || response.status === 205) {
    return undefined as T;
  }
  const contentType = response.headers.get("Content-Type") ?? "";
  if (contentType.startsWith("application/json")) {
    return (await response.json()) as T;
  }
  return (await response.text()) as unknown as T;
}

export const api = {
  get<T>(path: string, init?: RequestInit) {
    return request<T>("GET", path, undefined, init);
  },
  post<T>(path: string, body?: unknown, init?: RequestInit) {
    return request<T>("POST", path, body, init);
  },
  put<T>(path: string, body?: unknown, init?: RequestInit) {
    return request<T>("PUT", path, body, init);
  },
  patch<T>(path: string, body?: unknown, init?: RequestInit) {
    return request<T>("PATCH", path, body, init);
  },
  delete<T>(path: string, body?: unknown, init?: RequestInit) {
    return request<T>("DELETE", path, body, init);
  },
};

/** True when a 422 points at this body field (e.g. `email`), so the form can mark it. */
export function isFieldError(err: unknown, field: string): boolean {
  return (
    isApiError(err) &&
    err.status === 422 &&
    (err.body?.error?.details ?? []).some((d) => (d.loc ?? []).includes(field))
  );
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}
