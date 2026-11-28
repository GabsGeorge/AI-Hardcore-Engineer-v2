import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  FormField,
  Input,
  Label,
  LoadError,
  Spinner,
  StatusBadge,
  useToast,
} from "@/design";
import { api, isApiError } from "@/lib/api";
import { TicketContext } from "./TicketContext";
import type { HandoffRow } from "./types";

type QueueResponse = { items: HandoffRow[] };

export function TicketPage() {
  const { t } = useTranslation();
  const { ticket_id } = useParams<{ ticket_id: string }>();
  const navigate = useNavigate();
  const toast = useToast();
  const client = useQueryClient();

  // We pull the ticket from the cached queue to avoid an extra endpoint. The spec promises an
  // operator opens the queue first, so this is always warm; we fall back to the API by key.
  const list = useQuery<QueueResponse>({
    queryKey: ["handoffs", "awaiting_human"],
    queryFn: () => api.get<QueueResponse>("/v1/handoffs?status=awaiting_human&limit=100"),
    enabled: Boolean(ticket_id),
  });
  const ticket = list.data?.items.find((row) => row.id === ticket_id);

  const [reply, setReply] = useState("");
  const [decision, setDecision] = useState<"approve" | "reject" | null>(null);
  const [missingDecision, setMissingDecision] = useState(false);

  const mutation = useMutation({
    mutationFn: async () => {
      const body: Record<string, string> = { reply };
      if (ticket?.pending_action !== null && decision !== null) {
        body.action_decision = decision;
      }
      return api.post(`/v1/handoffs/${ticket_id}/reply`, body);
    },
    onSuccess: () => {
      toast(t("ticket.sent"), "success");
      void client.invalidateQueries({ queryKey: ["handoffs", "awaiting_human"] });
      navigate("/queue");
    },
    onError: (err) => {
      if (!isApiError(err)) {
        toast(t("ticket.sendError"), "danger");
        return;
      }
      if (err.status === 409) {
        toast(t("ticket.alreadyClosed"), "warning");
        void client.invalidateQueries({ queryKey: ["handoffs", "awaiting_human"] });
        navigate("/queue");
      } else if (err.status === 422 && err.code === "validation_error") {
        setMissingDecision(true);
      } else {
        toast(t("ticket.sendError"), "danger");
      }
    },
  });

  if (list.isLoading) {
    return (
      <div className="flex items-center gap-2 text-ink-500">
        <Spinner /> <span>{t("app.loading")}</span>
      </div>
    );
  }
  if (list.isError && list.data === undefined) {
    return <LoadError onRetry={() => void list.refetch()} retrying={list.isFetching} />;
  }
  if (ticket === undefined) {
    // The queue loaded and this ticket is not in it: answered, closed, or a wrong address.
    return <Alert tone="info">{t("ticket.notInQueue")}</Alert>;
  }

  const requiresDecision = ticket.pending_action !== null;

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (reply.trim().length === 0) {
      return;
    }
    if (requiresDecision && decision === null) {
      setMissingDecision(true);
      return;
    }
    setMissingDecision(false);
    mutation.mutate();
  }

  return (
    <div className="flex flex-col gap-4">
      <header className="flex items-center gap-2">
        <h1 className="text-heading text-ink-900">{t("ticket.title")}</h1>
        <StatusBadge tone={ticket.priority === "high" ? "danger" : "neutral"}>
          {ticket.priority === "high" ? t("queue.priorityHigh") : t("queue.priorityNormal")}
        </StatusBadge>
      </header>

      <Card>
        <Label>{t("ticket.summary")}</Label>
        <p className="mt-1 text-body text-ink-700">{ticket.summary}</p>
      </Card>

      <TicketContext context={ticket.context} />

      {requiresDecision ? (
        <Card>
          <Label>{t("ticket.pendingAction")}</Label>
          <p className="mt-1 text-body text-ink-700">
            {t("ticket.pendingActionBody", { type: ticket.pending_action?.type ?? "" })}
          </p>
          <fieldset className="mt-3 flex gap-4">
            <legend className="sr-only">{t("ticket.decisionLegend")}</legend>
            <label className="flex items-center gap-2 text-body">
              <input
                type="radio"
                name="decision"
                value="approve"
                checked={decision === "approve"}
                onChange={() => setDecision("approve")}
              />
              {t("ticket.approve")}
            </label>
            <label className="flex items-center gap-2 text-body">
              <input
                type="radio"
                name="decision"
                value="reject"
                checked={decision === "reject"}
                onChange={() => setDecision("reject")}
              />
              {t("ticket.reject")}
            </label>
          </fieldset>
          {missingDecision ? (
            <p className="mt-2 text-label text-state-danger" role="alert">
              {t("ticket.missingDecision")}
            </p>
          ) : null}
        </Card>
      ) : null}

      <form onSubmit={onSubmit} className="flex flex-col gap-3">
        <FormField label={t("ticket.replyLabel")} required>
          {({ input }) => (
            <Input
              id={input}
              value={reply}
              onChange={(event) => setReply(event.target.value)}
              placeholder={t("ticket.replyPlaceholder")}
            />
          )}
        </FormField>
        <Button type="submit" loading={mutation.isPending} disabled={reply.trim().length === 0}>
          {t("ticket.send")}
        </Button>
      </form>
    </div>
  );
}
