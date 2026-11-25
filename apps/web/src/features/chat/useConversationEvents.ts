import { useEffect, useRef } from "react";
import { openGetStream } from "@/lib/sse";
import type { HandoffRef } from "./types";

export type ConversationEventHandlers = {
  /** Resposta do atendente (ou o encerramento do ticket) chegou pelo canal. */
  onFinalMessage: (content: string) => void;
  onHandoffUpdate: (handoff: HandoffRef) => void;
  onSlaBreached: () => void;
  onExpired: (ticketId: string | null) => void;
};

/**
 * Escuta `/v1/conversations/{id}/events` enquanto o handoff está em `awaiting_human` (FR-010,
 * T065). A conexão fecha quando o componente sai da tela, quando a conversa muda e quando o
 * handoff deixa de estar em `awaiting_human` (o efeito depende só do id e do status).
 */
export function useConversationEvents(
  conversationId: string | undefined,
  handoffStatus: HandoffRef["status"],
  handlers: ConversationEventHandlers,
): void {
  // Os handlers mudam a cada render; a conexão não pode reabrir por causa disso.
  const latest = useRef(handlers);
  latest.current = handlers;

  useEffect(() => {
    if (handoffStatus !== "awaiting_human" || !conversationId) {
      return;
    }
    const controller = new AbortController();
    openGetStream(
      `/v1/conversations/${encodeURIComponent(conversationId)}/events`,
      {
        onEvent: (name, data) => {
          const payload = (data ?? {}) as Record<string, unknown>;
          if (name === "final_message") {
            latest.current.onFinalMessage(String(payload["content"] ?? ""));
          } else if (name === "handoff_update") {
            latest.current.onHandoffUpdate({
              status: (payload["status"] as HandoffRef["status"]) ?? "none",
              ticket_id: (payload["ticket_id"] as string | null) ?? null,
            });
          } else if (name === "sla_breached") {
            latest.current.onSlaBreached();
          } else if (name === "handoff_expired") {
            latest.current.onExpired((payload["ticket_id"] as string | null) ?? null);
          }
        },
      },
      controller.signal,
    );
    return () => controller.abort();
  }, [conversationId, handoffStatus]);
}
