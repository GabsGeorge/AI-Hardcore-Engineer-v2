import { useTranslation } from "react-i18next";
import { Alert } from "./Alert";
import { Button } from "./Button";

/**
 * A load that failed is not an empty result: "no tickets waiting" and "could not reach the
 * server" must never look the same on screen.
 *
 * - default: nothing could be loaded. Says so (role="alert") and offers the retry.
 * - `stale`: an earlier answer is still on screen and the refresh failed. The data stays, with a
 *   warning that it may be out of date.
 */
export function LoadError({
  onRetry,
  retrying = false,
  stale = false,
}: {
  onRetry: () => void;
  retrying?: boolean;
  stale?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <Alert
      tone={stale ? "warning" : "danger"}
      title={t(stale ? "loadError.staleTitle" : "loadError.title")}
    >
      <p>{t(stale ? "loadError.staleBody" : "loadError.body")}</p>
      <Button intent="secondary" size="sm" className="mt-3" loading={retrying} onClick={onRetry}>
        {t("loadError.retry")}
      </Button>
    </Alert>
  );
}
