import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeEventSource } from "@/test/fakeEventSource";
import { openGetStream, parseRetryAfter, SseHttpError, streamPost } from "./sse";

describe("openGetStream", () => {
  beforeEach(() => {
    FakeEventSource.reset();
    vi.stubGlobal("EventSource", FakeEventSource);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("entrega os eventos do canal e descarta o que volta repetido após reconexão", () => {
    const onEvent = vi.fn();
    openGetStream("/v1/conversations/c1/events", { onEvent }, new AbortController().signal);
    const source = FakeEventSource.last();
    expect(source.url).toBe("/v1/conversations/c1/events");
    expect(source.withCredentials).toBe(true);

    source.emit("final_message", { content: "Oi" }, "final_message:2");
    source.emit("final_message", { content: "Oi" }, "final_message:2"); // reenvio
    source.emit(
      "handoff_update",
      { status: "resolved", ticket_id: "T1" },
      "handoff_update:T1:resolved",
    );

    expect(onEvent.mock.calls).toEqual([
      ["final_message", { content: "Oi" }],
      ["handoff_update", { status: "resolved", ticket_id: "T1" }],
    ]);
  });

  it("deixa o navegador reconectar sozinho (CONNECTING) sem erro nem timer", () => {
    vi.useFakeTimers();
    const onError = vi.fn();
    openGetStream("/x", { onError }, new AbortController().signal);
    const source = FakeEventSource.last();
    source.fail(FakeEventSource.CONNECTING);
    vi.runAllTimers();
    expect(onError).not.toHaveBeenCalled();
    expect(source.closed).toBe(false);
    expect(FakeEventSource.instances).toHaveLength(1); // nenhuma conexão manual extra
    vi.useRealTimers();
  });

  it("não reconecta quando o navegador desiste (HTTP 401/403 fecha o EventSource)", () => {
    vi.useFakeTimers();
    const onError = vi.fn();
    openGetStream("/x", { onError }, new AbortController().signal);
    FakeEventSource.last().fail(FakeEventSource.CLOSED);
    vi.runAllTimers();
    expect(onError).toHaveBeenCalledTimes(1);
    expect(FakeEventSource.instances).toHaveLength(1);
    vi.useRealTimers();
  });

  it("fecha a conexão quando o sinal é abortado", () => {
    const controller = new AbortController();
    openGetStream("/x", {}, controller.signal);
    const source = FakeEventSource.last();
    controller.abort();
    expect(source.closed).toBe(true);
  });

  it("não abre nada com o sinal já abortado", () => {
    const controller = new AbortController();
    controller.abort();
    openGetStream("/x", {}, controller.signal);
    expect(FakeEventSource.instances).toHaveLength(0);
  });
});

describe("streamPost: erros HTTP", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("429 carrega o Retry-After em segundos", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 429, headers: { "Retry-After": "17" } })),
    );
    const onError = vi.fn();
    const error = await streamPost("/v1/chat/stream", {}, { onError }, new AbortController().signal)
      .then(() => null)
      .catch((err: unknown) => err);
    expect(error).toBeInstanceOf(SseHttpError);
    expect((error as SseHttpError).status).toBe(429);
    expect((error as SseHttpError).retryAfterSeconds).toBe(17);
    expect((error as Error).message).toBe("sse_http_429");
    expect(onError).toHaveBeenCalledWith(error);
  });

  it("sem Retry-After o valor fica nulo (a tela usa o padrão)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 429 })),
    );
    const error = await streamPost("/x", {}, {}, new AbortController().signal).catch(
      (err: unknown) => err,
    );
    expect((error as SseHttpError).retryAfterSeconds).toBeNull();
  });
});

describe("parseRetryAfter", () => {
  it.each([
    ["30", 30],
    [" 5 ", 5],
    ["1.2", 2],
    ["0", 0],
    ["-1", null],
    ["Wed, 21 Oct 2026 07:28:00 GMT", null],
    [null, null],
  ])("%s → %s", (value, expected) => {
    expect(parseRetryAfter(value)).toBe(expected);
  });
});

describe("streamPost: parser do fluxo (C3)", () => {
  afterEach(() => vi.unstubAllGlobals());

  function streamOf(chunks: string[]): Response {
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    });
    return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
  }

  async function collect(chunks: string[]) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => streamOf(chunks)),
    );
    const events: [string, unknown][] = [];
    const onDone = vi.fn();
    await streamPost(
      "/v1/chat/stream",
      {},
      { onEvent: (name, data) => events.push([name, data]), onDone },
      new AbortController().signal,
    );
    return { events, onDone };
  }

  const STEP = { node: "supervisor" };
  const DONE = { response: "ok", trace_id: "t1" };

  it("separador LF", async () => {
    const { events } = await collect([
      `event: agent_step\ndata: ${JSON.stringify(STEP)}\n\nevent: done\ndata: ${JSON.stringify(DONE)}\n\n`,
    ]);
    expect(events).toEqual([
      ["agent_step", STEP],
      ["done", DONE],
    ]);
  });

  it("separador CRLF (o que o Starlette envia)", async () => {
    const { events } = await collect([
      `event: agent_step\r\ndata: ${JSON.stringify(STEP)}\r\n\r\nevent: done\r\ndata: ${JSON.stringify(DONE)}\r\n\r\n`,
    ]);
    expect(events.map(([name]) => name)).toEqual(["agent_step", "done"]);
  });

  it("separadores misturados", async () => {
    const { events } = await collect([
      `event: agent_step\r\ndata: ${JSON.stringify(STEP)}\n\r\nevent: done\ndata: ${JSON.stringify(DONE)}\r\n\n`,
    ]);
    expect(events.map(([name]) => name)).toEqual(["agent_step", "done"]);
  });

  it("evento partido entre chunks", async () => {
    const whole = `event: done\r\ndata: ${JSON.stringify(DONE)}\r\n\r\n`;
    const { events, onDone } = await collect([
      whole.slice(0, 7),
      whole.slice(7, 30),
      whole.slice(30),
    ]);
    expect(events).toEqual([["done", DONE]]);
    expect(onDone).toHaveBeenCalled();
  });
});
