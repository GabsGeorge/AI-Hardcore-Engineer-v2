import { useTranslation } from "react-i18next";
import { Card, Label } from "@/design";

type ContextMessage = { role: "merchant" | "assistant"; content: string };
type TicketContextData = {
  merchant?: { id?: string; name?: string | null };
  recent_messages?: ContextMessage[];
  route?: string[];
};

/** Name of the establishment (or its id) for the queue row; empty for old tickets. */
export function merchantLabel(context: Record<string, unknown>): string {
  const merchant = (context as TicketContextData).merchant;
  return merchant?.name || merchant?.id || "";
}

/**
 * What the operator needs to pick the ticket up (QA-022): who the merchant is and the last
 * messages of the conversation. The internal route stays available, collapsed. Messages are
 * rendered as plain text (no markdown, no links): they are merchant input.
 */
export function TicketContext({ context }: { context: Record<string, unknown> }) {
  const { t } = useTranslation();
  const data = context as TicketContextData;
  const messages = Array.isArray(data.recent_messages) ? data.recent_messages : [];
  return (
    <Card>
      <Label>{t("ticket.merchant")}</Label>
      <p className="mt-1 text-body text-ink-700">
        {merchantLabel(context) || t("ticket.merchantUnknown")}
        {data.merchant?.name && data.merchant.id ? (
          <span className="ml-2 text-label text-ink-400">({data.merchant.id})</span>
        ) : null}
      </p>

      <Label className="mt-4">{t("ticket.conversation")}</Label>
      {messages.length === 0 ? (
        <p className="mt-1 text-body text-ink-500">{t("ticket.noMessages")}</p>
      ) : (
        <ol className="mt-1 flex flex-col gap-2">
          {messages.map((m, i) => (
            <li key={i} className="rounded-md bg-surface-200 px-3 py-2">
              <span className="text-label font-semibold text-ink-500">
                {m.role === "merchant" ? t("ticket.fromMerchant") : t("ticket.fromAssistant")}
              </span>
              <p className="whitespace-pre-wrap break-words text-body text-ink-700">{m.content}</p>
            </li>
          ))}
        </ol>
      )}

      {Array.isArray(data.route) && data.route.length > 0 ? (
        <details className="mt-4">
          <summary className="cursor-pointer text-label text-ink-500">{t("ticket.route")}</summary>
          <p className="mt-1 text-label text-ink-500">{data.route.join(" → ")}</p>
        </details>
      ) : null}
    </Card>
  );
}
