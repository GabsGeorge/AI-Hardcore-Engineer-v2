import { useTranslation } from "react-i18next";
import { Alert, Spinner, StatusBadge } from "@/design";
import { MessageContent } from "./MessageContent";
import { MessageFeedback } from "./MessageFeedback";
import { SourceList } from "./SourceList";
import type { ChatMessage } from "./types";

/** Rendered conversation. Text content is bound via React children, never innerHTML, so a
 * model answer that contains HTML or JS stays inert (FR-012 covered on the backend by
 * tests/security/test_chat_render.py). */
export function MessageList({
  messages,
  pending,
  liveSteps,
  draft = "",
}: {
  messages: ChatMessage[];
  pending: boolean;
  liveSteps: string[];
  /** Resposta em geração, ainda não verificada pelo servidor (eventos `token`). */
  draft?: string;
}) {
  const { t } = useTranslation();
  return (
    // role="log" fica no contêiner: num <ol> ele apagaria a semântica de lista e os <li>
    // ficariam órfãos para leitores de tela (axe: listitem).
    <div role="log" aria-live="polite" aria-relevant="additions">
      <ol className="flex flex-col gap-4">
        {messages.map((message) =>
          message.role === "user" ? (
            <li key={message.id} className="flex justify-end">
              <div className="max-w-[80%] whitespace-pre-wrap break-words rounded-lg bg-surface-200 p-3 text-body text-ink-700 [overflow-wrap:anywhere]">
                {message.content}
              </div>
            </li>
          ) : message.role === "error" ? (
            <li key={message.id} className="max-w-[80%]">
              <Alert tone="danger">{message.content}</Alert>
            </li>
          ) : (
            <li key={message.id} className="flex flex-col gap-2">
              {message.agentSteps.length > 0 ? (
                <div className="flex flex-wrap gap-1">
                  {message.agentSteps.map((step) => (
                    <StatusBadge key={step}>{step}</StatusBadge>
                  ))}
                </div>
              ) : null}
              <div className="max-w-[80%] break-words rounded-lg border border-line bg-surface-100 p-3 text-body text-ink-700 shadow-1 [overflow-wrap:anywhere]">
                <MessageContent text={message.content} />
              </div>
              {message.sources.length > 0 ? <SourceList sources={message.sources} /> : null}
              <MessageFeedback traceId={message.traceId} />
            </li>
          ),
        )}
        {pending ? (
          <li className="flex flex-col gap-2">
            {liveSteps.length > 0 ? (
              <div className="flex flex-wrap gap-1">
                {liveSteps.map((step) => (
                  <StatusBadge key={step}>{step}</StatusBadge>
                ))}
              </div>
            ) : null}
            {draft ? (
              // Rascunho: o texto aparece enquanto é gerado, com o aviso de que ainda está sendo
              // verificado. Fica fora da leitura de tela (aria-hidden): quem usa leitor ouve a
              // resposta final uma vez, anunciada quando chega, e não cada pedaço.
              <>
                <div
                  aria-hidden="true"
                  data-testid="draft"
                  className="max-w-[80%] break-words rounded-lg border border-dashed border-line bg-surface-100 p-3 text-body text-ink-700 [overflow-wrap:anywhere]"
                >
                  <MessageContent text={draft} />
                </div>
                <div className="flex items-center gap-2 text-label text-ink-500">
                  <Spinner />
                  <span>{t("chat.verifying")}</span>
                </div>
              </>
            ) : (
              <div className="flex max-w-[80%] items-center gap-2 rounded-lg border border-line bg-surface-100 p-3 text-body text-ink-500 shadow-1">
                <Spinner />
                <span>{t("chat.thinking")}</span>
              </div>
            )}
          </li>
        ) : null}
      </ol>
    </div>
  );
}
