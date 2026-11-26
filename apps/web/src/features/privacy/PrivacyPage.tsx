import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { Alert, Button, Card, FormField, Input, PasswordInput, useToast } from "@/design";
import { api, isApiError } from "@/lib/api";

type DeleteDataResponse = {
  deleted: { conversations: number; feedback: number; audit_events_pseudonymized: number };
};

/**
 * FR-016 / FR-016a (T082): two separate actions. Deleting the conversations keeps the account and
 * the session; deleting the account also needs the current password, ends the session and
 * frees the e-mail. Each action runs only after the confirmation word is typed.
 */
export function PrivacyPage() {
  const { t } = useTranslation();
  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <header>
        <h1 className="text-heading text-ink-900">{t("privacy.title")}</h1>
        <p className="mt-1 text-body text-ink-500">{t("privacy.body")}</p>
      </header>
      <DeleteConversations />
      <DeleteAccount />
    </div>
  );
}

function confirmationWord(language: string): "EXCLUIR" | "DELETE" {
  return language.startsWith("en") ? "DELETE" : "EXCLUIR";
}

function DeleteConversations() {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const client = useQueryClient();
  const word = confirmationWord(i18n.language);
  const [typed, setTyped] = useState("");
  const mutation = useMutation({
    mutationFn: () => api.delete<DeleteDataResponse>("/v1/me/data"),
    onSuccess: () => {
      setTyped("");
      toast(t("privacy.deleted"), "success");
      void client.invalidateQueries({ queryKey: ["me", "conversations"] });
    },
    onError: () => toast(t("privacy.deleteError"), "danger"),
  });
  const summary = mutation.data?.deleted;
  return (
    <Card>
      <section aria-labelledby="privacy-convs">
        <h2 id="privacy-convs" className="text-heading text-ink-900">
          {t("privacy.deleteConvsTitle")}
        </h2>
        <p className="mt-1 text-body text-ink-500">{t("privacy.deleteConvsBody")}</p>
        <form
          className="mt-4 flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (typed === word) {
              mutation.mutate();
            }
          }}
        >
          <FormField
            label={t("privacy.confirmLabel", { word })}
            help={t("privacy.confirmHelp", { word })}
          >
            {(ids) => (
              <Input
                id={ids.input}
                aria-describedby={ids.help}
                value={typed}
                autoComplete="off"
                onChange={(e) => setTyped(e.target.value)}
              />
            )}
          </FormField>
          <div>
            <Button type="submit" intent="primary" disabled={typed !== word || mutation.isPending}>
              {t("privacy.deleteConvsAction")}
            </Button>
          </div>
        </form>
        {summary ? (
          <Alert tone="success" title={t("privacy.deletedSummary")} className="mt-4">
            <ul>
              <li>
                {summary.conversations} {t("privacy.summary.conversations")}
              </li>
              <li>
                {summary.feedback} {t("privacy.summary.feedback")}
              </li>
              <li>
                {summary.audit_events_pseudonymized}{" "}
                {t("privacy.summary.audit_events_pseudonymized")}
              </li>
            </ul>
          </Alert>
        ) : null}
      </section>
    </Card>
  );
}

function DeleteAccount() {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const client = useQueryClient();
  const navigate = useNavigate();
  const word = confirmationWord(i18n.language);
  const [typed, setTyped] = useState("");
  const [password, setPassword] = useState("");
  const [badPassword, setBadPassword] = useState(false);
  const mutation = useMutation({
    mutationFn: () => api.delete<void>("/v1/me/account", { password, confirmation: word }),
    onSuccess: () => {
      toast(t("privacy.accountDeleted"), "success");
      client.clear();
      client.setQueryData(["session"], null);
      navigate("/login", { replace: true });
    },
    onError: (err) => {
      if (isApiError(err) && err.status === 401) {
        setBadPassword(true);
        return;
      }
      toast(t("privacy.deleteError"), "danger");
    },
  });
  const ready = typed === word && password.length > 0 && !mutation.isPending;
  return (
    <Card>
      <section aria-labelledby="privacy-account">
        <h2 id="privacy-account" className="text-heading text-ink-900">
          {t("privacy.deleteAccountTitle")}
        </h2>
        <p className="mt-1 text-body text-ink-500">{t("privacy.deleteAccountBody")}</p>
        <form
          className="mt-4 flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            setBadPassword(false);
            if (ready) {
              mutation.mutate();
            }
          }}
        >
          <FormField
            label={t("privacy.currentPassword")}
            required
            error={badPassword ? t("privacy.badPassword") : undefined}
          >
            {(ids) => (
              <PasswordInput
                id={ids.input}
                aria-describedby={badPassword ? ids.error : undefined}
                aria-invalid={badPassword}
                invalid={badPassword}
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            )}
          </FormField>
          <FormField
            label={t("privacy.confirmLabel", { word })}
            help={t("privacy.confirmHelp", { word })}
          >
            {(ids) => (
              <Input
                id={ids.input}
                aria-describedby={ids.help}
                value={typed}
                autoComplete="off"
                onChange={(e) => setTyped(e.target.value)}
              />
            )}
          </FormField>
          <div>
            <Button type="submit" intent="primary" disabled={!ready}>
              {t("privacy.deleteAccountAction")}
            </Button>
          </div>
        </form>
      </section>
    </Card>
  );
}
