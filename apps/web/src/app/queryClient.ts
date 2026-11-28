import { QueryCache, QueryClient } from "@tanstack/react-query";
import { isApiError } from "@/lib/api";

/** Set when a data request came back 401: the guard sends the user to the login with the
 * "your session expired" notice, and the login clears it. */
export const SESSION_EXPIRED_KEY = ["sessionExpired"] as const;

/**
 * The app's QueryClient.
 *
 * A 401 on any data query means the session ended (expired, or closed somewhere else). Without
 * this, the screen would say "could not load, try again" and the retry would fail forever: the
 * operator queue refetches every 30 s, so a tab left open always gets there. Clearing the session
 * makes the AuthGuard redirect to the login and come back to the same page afterwards.
 */
export function createQueryClient(): QueryClient {
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) => {
        if (isApiError(error) && error.status === 401 && query.queryKey[0] !== "session") {
          client.setQueryData(SESSION_EXPIRED_KEY, true);
          client.setQueryData(["session"], null);
        }
      },
    }),
    defaultOptions: {
      queries: {
        retry: false,
        refetchOnWindowFocus: false,
      },
    },
  });
  return client;
}
