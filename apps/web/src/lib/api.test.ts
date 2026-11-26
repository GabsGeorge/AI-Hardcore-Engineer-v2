import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./api";

type Call = { url: string; init: RequestInit };

function mockFetch(response: Response): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return response;
    }),
  );
  return calls;
}

const header = (call: Call, name: string) => new Headers(call.init.headers).get(name);

describe("api.ts (C3)", () => {
  beforeEach(() => {
    document.cookie = "csrf=token-123; path=/";
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    document.cookie = "csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
  });

  it("mutação envia o X-CSRF-Token do cookie e manda o cookie de sessão", async () => {
    const calls = mockFetch(Response.json({ ok: true }));
    await api.post("/v1/x", { a: 1 });
    expect(header(calls[0]!, "X-CSRF-Token")).toBe("token-123");
    expect(header(calls[0]!, "Content-Type")).toBe("application/json");
    expect(calls[0]!.init.credentials).toBe("include");
  });

  it("init.headers do chamador não apaga o X-CSRF-Token", async () => {
    const calls = mockFetch(Response.json({}));
    await api.post("/v1/x", { a: 1 }, { headers: { "Idempotency-Key": "k1" } });
    expect(header(calls[0]!, "X-CSRF-Token")).toBe("token-123");
    expect(header(calls[0]!, "Idempotency-Key")).toBe("k1");
  });

  it("GET não envia o token de CSRF", async () => {
    const calls = mockFetch(Response.json({}));
    await api.get("/v1/x");
    expect(header(calls[0]!, "X-CSRF-Token")).toBeNull();
  });

  it("401 vira ApiError com o código do corpo", async () => {
    mockFetch(
      Response.json(
        { error: { code: "unauthorized", message: "Sessão expirada." } },
        { status: 401 },
      ),
    );
    const err = await api.get("/v1/auth/me").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(401);
    expect((err as ApiError).code).toBe("unauthorized");
  });

  it("204 devolve undefined sem tentar ler JSON", async () => {
    mockFetch(new Response(null, { status: 204 }));
    await expect(api.delete("/v1/x")).resolves.toBeUndefined();
  });

  it("429 carrega o Retry-After em segundos", async () => {
    mockFetch(new Response("{}", { status: 429, headers: { "Retry-After": "42" } }));
    const err = (await api.post("/v1/auth/register", {}).catch((e: unknown) => e)) as ApiError;
    expect(err.status).toBe(429);
    expect(err.retryAfterSeconds).toBe(42);
  });

  it("Retry-After em formato de data não vira NaN", async () => {
    mockFetch(
      new Response("{}", {
        status: 429,
        headers: { "Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT" },
      }),
    );
    const err = (await api.get("/v1/x").catch((e: unknown) => e)) as ApiError;
    expect(err.retryAfterSeconds).toBeUndefined();
  });
});
