import { useTranslation } from "react-i18next";
import { useRouteError } from "react-router-dom";
import { Button } from "@/design";

// The wording each browser uses when a lazily loaded page file cannot be fetched (Chrome,
// Firefox, Safari). After a deploy the old file names no longer exist on the server, so a tab
// that was already open fails exactly here.
const CHUNK_LOAD =
  /dynamically imported module|importing a module script failed|failed to fetch dynamically/i;

export function isChunkLoadError(error: unknown): boolean {
  return error instanceof Error && CHUNK_LOAD.test(error.message);
}

/**
 * What the user sees when a page throws while rendering or its file fails to load. Without it
 * React Router shows its own developer screen, in English and with no way out.
 *
 * The error detail stays in the console: the screen names the problem and the recovery only.
 * `fullPage` is for errors outside the app layout; inside it the header and navigation stay.
 */
export function RouteError({
  fullPage = false,
  reload = () => window.location.reload(),
}: {
  fullPage?: boolean;
  reload?: () => void;
}) {
  const { t } = useTranslation();
  const error = useRouteError();
  const outdated = isChunkLoadError(error);

  const content = (
    <div role="alert" className="flex max-w-md flex-col items-center gap-4 text-center">
      <h1 className="text-display text-ink-900">
        {t(outdated ? "routeError.outdatedTitle" : "routeError.title")}
      </h1>
      <p className="text-body text-ink-500">
        {t(outdated ? "routeError.outdatedBody" : "routeError.body")}
      </p>
      <div className="flex flex-wrap items-center justify-center gap-4">
        <Button onClick={reload}>{t("routeError.reload")}</Button>
        {/* A plain link on purpose: a full page load also fetches the current files. */}
        <a
          href="/"
          className="rounded-md px-1 py-2 text-label text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-support"
        >
          {t("routeError.home")}
        </a>
      </div>
    </div>
  );

  if (fullPage) {
    return (
      <main className="mx-auto flex min-h-screen flex-col items-center justify-center p-8">
        {content}
      </main>
    );
  }
  return <div className="flex flex-col items-center py-12">{content}</div>;
}
