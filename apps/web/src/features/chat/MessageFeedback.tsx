import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/design";
import { api } from "@/lib/api";

/** A real trace id (32 hex chars). Operator replies and old local ids carry none. */
export function isRateable(traceId: string): boolean {
  return /^[0-9a-f]{32}$/.test(traceId);
}

/**
 * FR-015: the merchant rates each answer (useful / not useful). The server accepts only traces
 * of answers this merchant received (QA-024), so the id comes from the answer itself.
 */
export function MessageFeedback({ traceId }: { traceId: string }) {
  const { t } = useTranslation();
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  if (!isRateable(traceId)) {
    return null;
  }
  async function rate(rating: 1 | -1) {
    setState("sending");
    try {
      await api.post("/v1/feedback", { trace_id: traceId, rating });
      setState("sent");
    } catch {
      setState("error");
    }
  }
  if (state === "sent") {
    return (
      <p className="text-label text-ink-500" role="status">
        {t("chat.feedback.thanks")}
      </p>
    );
  }
  return (
    <div className="flex items-center gap-2" role="group" aria-label={t("chat.feedback.label")}>
      <Button intent="ghost" size="sm" disabled={state === "sending"} onClick={() => void rate(1)}>
        {t("chat.feedback.up")}
      </Button>
      <Button intent="ghost" size="sm" disabled={state === "sending"} onClick={() => void rate(-1)}>
        {t("chat.feedback.down")}
      </Button>
      {state === "error" ? (
        <span className="text-label text-state-danger" role="status">
          {t("chat.feedback.error")}
        </span>
      ) : null}
    </div>
  );
}
