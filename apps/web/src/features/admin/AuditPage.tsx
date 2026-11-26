import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Card, EmptyState, FormField, Input, Spinner, StatusBadge } from "@/design";
import { api } from "@/lib/api";

const PAGE_SIZE = 50;

type AuditEvent = {
  id: number;
  ts: string;
  actor_id: string;
  actor_role: string;
  action: string;
  target: string;
  outcome: "allowed" | "denied" | "error";
  trace_id: string;
};

export function AuditPage() {
  const { t } = useTranslation();
  const [action, setAction] = useState("");
  const [since, setSince] = useState("");

  const query = useInfiniteQuery({
    queryKey: ["admin", "audit", action, since],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams();
      if (action.trim()) params.set("action", action.trim());
      if (since) params.set("since", new Date(since).toISOString());
      if (pageParam !== undefined) params.set("before_id", String(pageParam));
      params.set("limit", String(PAGE_SIZE));
      return api.get<AuditEvent[]>(`/v1/admin/audit?${params.toString()}`);
    },
    // Keyset: the next page starts below the last id shown; a short page is the last one.
    getNextPageParam: (last) => (last.length === PAGE_SIZE ? last[last.length - 1]?.id : undefined),
  });
  const rows = query.data?.pages.flat() ?? [];
  const actor = (row: AuditEvent) =>
    row.actor_id.startsWith("deleted:") ? t("admin.audit.pseudonymised") : row.actor_id;
  const outcome = (row: AuditEvent) => (
    <StatusBadge
      tone={row.outcome === "allowed" ? "success" : row.outcome === "denied" ? "warning" : "danger"}
    >
      {t(`admin.audit.outcome.${row.outcome}`, { defaultValue: row.outcome })}
    </StatusBadge>
  );

  return (
    <section className="flex flex-col gap-4">
      <header>
        <h1 className="text-display text-ink-900">{t("admin.audit.title")}</h1>
      </header>

      <Card className="grid gap-3 sm:grid-cols-2">
        <FormField label={t("admin.audit.filterAction")}>
          {({ input }) => (
            <Input
              id={input}
              placeholder="login_ok"
              value={action}
              onChange={(e) => setAction(e.target.value)}
            />
          )}
        </FormField>
        <FormField label={t("admin.audit.filterSince")}>
          {({ input }) => (
            <Input
              id={input}
              type="datetime-local"
              value={since}
              onChange={(e) => setSince(e.target.value)}
            />
          )}
        </FormField>
      </Card>

      {query.isLoading ? (
        <div className="flex items-center gap-2 text-ink-500">
          <Spinner /> <span>{t("app.loading")}</span>
        </div>
      ) : rows.length === 0 ? (
        <EmptyState title={t("admin.audit.emptyTitle")} description={t("admin.audit.emptyBody")} />
      ) : (
        <>
          {/* Below 640 px one card per event: the five columns did not fit (QA-065). */}
          <ul className="flex flex-col gap-2 sm:hidden">
            {rows.map((row) => (
              <li key={row.id}>
                <Card className="flex flex-col gap-1 text-label">
                  <div className="flex items-center justify-between gap-2">
                    <span className="tabular-nums text-ink-500">
                      {new Date(row.ts).toLocaleString()}
                    </span>
                    {outcome(row)}
                  </div>
                  <span className="font-semibold text-ink-700">{row.action}</span>
                  <span className="break-all text-ink-500">{actor(row)}</span>
                  <span className="break-all text-ink-500">{row.target}</span>
                </Card>
              </li>
            ))}
          </ul>
          <div className="hidden overflow-x-auto sm:block">
            <table className="w-full text-left text-label">
              <thead className="text-ink-500">
                <tr>
                  <th scope="col" className="p-2">
                    {t("admin.audit.columnTs")}
                  </th>
                  <th scope="col" className="p-2">
                    {t("admin.audit.columnActor")}
                  </th>
                  <th scope="col" className="p-2">
                    {t("admin.audit.columnAction")}
                  </th>
                  <th scope="col" className="p-2">
                    {t("admin.audit.columnTarget")}
                  </th>
                  <th scope="col" className="p-2">
                    {t("admin.audit.columnOutcome")}
                  </th>
                </tr>
              </thead>
              <tbody className="text-ink-700">
                {rows.map((row) => (
                  <tr key={row.id} className="border-t border-line">
                    <td className="p-2 tabular-nums" style={{ fontVariantNumeric: "tabular-nums" }}>
                      {new Date(row.ts).toLocaleString()}
                    </td>
                    <td className="break-all p-2">{actor(row)}</td>
                    <td className="p-2">{row.action}</td>
                    <td className="break-all p-2">{row.target}</td>
                    <td className="p-2">{outcome(row)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {query.hasNextPage ? (
            <div>
              <Button
                intent="secondary"
                loading={query.isFetchingNextPage}
                onClick={() => void query.fetchNextPage()}
              >
                {t("admin.audit.loadMore")}
              </Button>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
