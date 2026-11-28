import { useQueryClient } from "@tanstack/react-query";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Alert, Spinner } from "@/design";
import { SESSION_EXPIRED_KEY } from "./queryClient";
import { homeFor, useSession, type Role } from "./session";

export function AuthGuard() {
  const { t } = useTranslation();
  const session = useSession();
  const location = useLocation();
  const client = useQueryClient();
  if (session.isLoading) {
    return <CenteredSpinner label={t("app.loading")} />;
  }
  if (!session.data) {
    const next = `${location.pathname}${location.search}`;
    // A session that ended while the page was open (a data request came back 401) gets the
    // "your session expired" notice on the login; a first visit does not.
    const expired = client.getQueryData(SESSION_EXPIRED_KEY) === true ? "&expired=1" : "";
    return <Navigate to={`/login?next=${encodeURIComponent(next)}${expired}`} replace />;
  }
  return <Outlet />;
}

/** `/`: each role lands on its own start page (needs a session; else the login). */
export function HomeRedirect() {
  const { t } = useTranslation();
  const session = useSession();
  if (session.isLoading) {
    return <CenteredSpinner label={t("app.loading")} />;
  }
  return <Navigate to={session.data ? homeFor(session.data.role) : "/login"} replace />;
}

export function RoleGuard({ allow }: { allow: Role[] }) {
  const { t } = useTranslation();
  const session = useSession();
  if (session.isLoading) {
    return <CenteredSpinner label={t("app.loading")} />;
  }
  if (!session.data) {
    return <Navigate to="/login" replace />;
  }
  if (!allow.includes(session.data.role)) {
    return (
      <main className="mx-auto max-w-md p-8">
        <Alert tone="danger" title={t("accessDenied.title")}>
          {t("accessDenied.body")}
        </Alert>
      </main>
    );
  }
  return <Outlet />;
}

function CenteredSpinner({ label }: { label: string }) {
  return (
    <div className="flex min-h-screen items-center justify-center text-ink-500">
      <div className="flex items-center gap-2">
        <Spinner />
        <span>{label}</span>
      </div>
    </div>
  );
}
