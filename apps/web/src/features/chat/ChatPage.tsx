import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { Button, Input, useLiveAnnouncer } from "@/design";
import { api } from "@/lib/api";
import { streamPost } from "@/lib/sse";
import { HandoffBanner } from "./HandoffBanner";
import { MessageList } from "./MessageList";
import type { ChatMessage, ChatResponseBody, HandoffRef } from "./types";
import { useConversationEvents } from "./useConversationEvents";

/** Mesmo limite do backend (ChatRequest.message, max_length=4000). */
export const MAX_MESSAGE_CHARS = 4000;

/** Espera padrão quando o 429 vem sem `Retry-After` utilizável. */
const DEFAULT_RETRY_SECONDS = 60;

/** HTTP status of a failed stream (`SseHttpError` carries it as `sse_http_<status>`). */
function streamStatus(err: unknown): number | undefined {
  const match = err instanceof Error ? /^sse_http_(\d{3})$/.exec(err.message) : null;
  return match ? Number(match[1]) : undefined;
}

function retryAfterSeconds(err: unknown): number {
  const value = (err as { retryAfterSeconds?: unknown } | null)?.retryAfterSeconds;
  return typeof value === "number" && value > 0 ? value : DEFAULT_RETRY_SECONDS;
}

type ConversationLoad = {
  conversation_id: string;
  handoff: HandoffRef;
  messages: {
    role: "user" | "assistant";
    content: string;
    trace_id?: string | null;
    sources?: ChatResponseBody["sources"];
  }[];
};

type ChatErrorKey =
  | "chat.errors.interrupted"
  | "chat.errors.generic"
  | "chat.errors.notLinked"
  | "chat.errors.invalid";

export function ChatPage() {
  const { t } = useTranslation();
  const announce = useLiveAnnouncer();
  const params = useParams<{ id?: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pending, setPending] = useState(false);
  const [liveSteps, setLiveSteps] = useState<string[]>([]);
  const [handoff, setHandoff] = useState<HandoffRef>({ status: "none", ticket_id: null });
  const [conversationId, setConversationId] = useState<string | undefined>(params.id);
  const [retryAt, setRetryAt] = useState<number | undefined>(undefined);
  const [retrySeconds, setRetrySeconds] = useState(DEFAULT_RETRY_SECONDS);
  const [slaBreached, setSlaBreached] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // Resposta do atendente em tempo real (FR-010, T065): enquanto o handoff espera um humano, o
  // canal de eventos traz a resposta, a mudança de status, o SLA estourado e a expiração.
  useConversationEvents(conversationId, handoff.status, {
    onFinalMessage: (content) => {
      setMessages((previous) => [
        ...previous,
        {
          id: `f-${Date.now()}`,
          role: "assistant",
          content,
          agentSteps: [],
          sources: [],
          traceId: "",
        },
      ]);
      announce(content);
      setHandoff((current) => ({ ...current, status: "resolved" }));
      setSlaBreached(false);
    },
    onHandoffUpdate: (next) => {
      setHandoff((current) => ({ ...next, ticket_id: next.ticket_id ?? current.ticket_id }));
    },
    onSlaBreached: () => setSlaBreached(true),
    onExpired: (ticketId) => {
      setHandoff((current) => ({ status: "expired", ticket_id: ticketId ?? current.ticket_id }));
      setSlaBreached(false);
    },
  });
  const bottomRef = useRef<HTMLDivElement | null>(null);

  // Rola para o fim sempre que msg nova chega ou passos do agente aparecem.
  // Resolve o P1 do e2e-register-flow: o composer sumia abaixo do viewport.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, liveSteps, pending]);

  // Hidrata a conversa ao abrir pela URL /chat/:id. Resolve o P0 "conversa abre vazia"
  // do e2e-register-flow (T020). Troca de id (nav entre conversas) tambem rehidrata.
  useEffect(() => {
    setSlaBreached(false);
    if (!params.id) {
      setMessages([]);
      setHandoff({ status: "none", ticket_id: null });
      setConversationId(undefined);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const data = await api.get<ConversationLoad>(`/v1/conversations/${params.id}`);
        if (cancelled) return;
        setConversationId(data.conversation_id);
        setHandoff(data.handoff);
        setMessages(
          data.messages.map((m, i) =>
            m.role === "assistant"
              ? {
                  id: `h-${params.id}-${i}`,
                  role: "assistant" as const,
                  content: m.content,
                  agentSteps: [],
                  sources: m.sources ?? [],
                  traceId: m.trace_id ?? `h-${params.id}-${i}`,
                }
              : { id: `h-${params.id}-${i}`, role: "user" as const, content: m.content },
          ),
        );
      } catch {
        if (!cancelled) {
          setMessages([]);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // T031: location.key muda a cada clique no NavLink mesmo para o mesmo /chat,
    // entao esse useEffect dispara e reseta o estado quando o lojista clica
    // "Conversar" durante um handoff ativo. Antes o composer ficava preso.
  }, [params.id, location.key]);

  const handoffLocked = handoff.status === "awaiting_human";
  const canSubmit = input.trim().length > 0 && !pending && retryAt === undefined && !handoffLocked;

  const send = useCallback(
    async (text: string) => {
      const userMessage: ChatMessage = {
        id: `u-${Date.now()}`,
        role: "user",
        content: text,
      };
      setMessages((previous) => [...previous, userMessage]);
      setPending(true);
      setLiveSteps([]);

      const controller = new AbortController();
      abortRef.current = controller;
      // Sem o evento `done` não há resposta: o servidor pode fechar o fluxo no meio ou devolver
      // erro. Antes isso só desligava o "pensando" e a pergunta ficava sem resposta nem aviso.
      let completed = false;
      const showError = (key: ChatErrorKey) => {
        setMessages((previous) => [
          ...previous,
          { id: `e-${Date.now()}`, role: "error", content: t(key) },
        ]);
      };
      try {
        await streamPost(
          "/v1/chat/stream",
          { message: text, conversation_id: conversationId },
          {
            onEvent: (name, data) => {
              if (name === "agent_step") {
                const node = (data as { node?: string }).node ?? "";
                setLiveSteps((steps) => (steps.includes(node) ? steps : [...steps, node]));
              } else if (name === "handoff") {
                const payload = data as HandoffRef;
                setHandoff(payload);
              } else if (name === "done") {
                completed = true;
                const final = data as ChatResponseBody;
                setConversationId(final.conversation_id);
                setHandoff(final.handoff);
                // Suprime bolha vazia em handoff awaiting_human: o HandoffBanner ja comunica
                // "aguardando atendente". Antes a UI pintava um cartao em branco (P0 do
                // e2e-register-flow 3b/3c).
                const hasText = (final.response ?? "").trim().length > 0;
                const isAwaitingHuman = final.handoff.status === "awaiting_human";
                if (hasText || !isAwaitingHuman) {
                  setMessages((previous) => [
                    ...previous,
                    {
                      id: `a-${final.trace_id}`,
                      role: "assistant",
                      content: final.response,
                      agentSteps: final.route,
                      sources: final.sources,
                      traceId: final.trace_id,
                    },
                  ]);
                  announce(final.response);
                }
                setPending(false);
                setLiveSteps([]);
              }
            },
            onError: () => {
              setPending(false);
              setLiveSteps([]);
            },
            onDone: () => {
              setPending(false);
            },
          },
          controller.signal,
        );
        if (!completed && !controller.signal.aborted) {
          showError("chat.errors.interrupted");
        }
      } catch (err) {
        setPending(false);
        setLiveSteps([]);
        if (controller.signal.aborted) {
          return;
        }
        if (streamStatus(err) === 401) {
          // Sessão expirada ou encerrada em outro lugar: volta ao login e retorna para cá
          // depois, em vez de repetir "tente de novo" a cada envio (QA-040).
          const next = encodeURIComponent(`${location.pathname}${location.search}`);
          navigate(`/login?next=${next}&expired=1`, { replace: true });
          return;
        }
        if (streamStatus(err) === 409) {
          showError("chat.errors.notLinked");
          return;
        }
        if (streamStatus(err) === 422) {
          showError("chat.errors.invalid");
          return;
        }
        if (err instanceof Error && err.message === "sse_http_429") {
          // Espera o que o servidor mandou em Retry-After; 60 s só quando o header falta.
          const seconds = retryAfterSeconds(err);
          setRetrySeconds(seconds);
          setRetryAt(Date.now() + seconds * 1000);
          window.setTimeout(() => setRetryAt(undefined), seconds * 1000);
          return;
        }
        showError(completed ? "chat.errors.interrupted" : "chat.errors.generic");
      }
    },
    [announce, conversationId, location.pathname, location.search, navigate, t],
  );

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }
    const text = input.trim();
    setInput("");
    void send(text);
  }

  useEffect(() => () => abortRef.current?.abort(), []);

  // O campo fica desabilitado enquanto a resposta chega, e o foco cai no <body>; devolve o foco
  // ao campo quando a resposta termina, para quem usa teclado seguir digitando (QA-071).
  const wasPending = useRef(false);
  useEffect(() => {
    if (wasPending.current && !pending) {
      document.getElementById("chat-input")?.focus();
    }
    wasPending.current = pending;
  }, [pending]);

  return (
    <div className="flex flex-col gap-6">
      <HandoffBanner handoff={handoff} slaBreached={slaBreached} />
      <MessageList messages={messages} pending={pending} liveSteps={liveSteps} />
      <form
        onSubmit={onSubmit}
        className="sticky bottom-0 flex items-end gap-2 border-t border-line bg-surface-100 pb-2 pt-4"
      >
        <label className="sr-only" htmlFor="chat-input">
          {t("chat.inputLabel")}
        </label>
        <Input
          id="chat-input"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          maxLength={MAX_MESSAGE_CHARS}
          aria-describedby="chat-input-limit"
          placeholder={
            handoffLocked ? t("chat.handoffLockPlaceholder") : t("chat.inputPlaceholder")
          }
          disabled={pending || retryAt !== undefined || handoffLocked}
          aria-busy={pending || undefined}
          className={handoffLocked ? "cursor-not-allowed bg-surface-200 text-ink-400" : undefined}
        />
        <Button type="submit" loading={pending} disabled={!canSubmit}>
          {t("chat.send")}
        </Button>
      </form>
      <p id="chat-input-limit" className="sr-only">
        {t("chat.maxLength", { max: MAX_MESSAGE_CHARS })}
      </p>
      {input.length >= MAX_MESSAGE_CHARS ? (
        <p className="text-label text-state-warning" role="status">
          {t("chat.maxLength", { max: MAX_MESSAGE_CHARS })}
        </p>
      ) : null}
      {retryAt !== undefined ? (
        <p className="text-label text-state-warning" role="status">
          {t("chat.rateLimited", { seconds: retrySeconds })}
        </p>
      ) : null}
      {handoffLocked ? (
        <p className="text-label text-ink-500" role="status">
          {t("chat.handoffLockHint")}
        </p>
      ) : null}
      <div ref={bottomRef} aria-hidden="true" />
    </div>
  );
}
