import { useQuery } from "@tanstack/react-query";
import { api, isApiError } from "@/lib/api";

export type Role = "merchant" | "operator" | "admin";

export type SessionAccount = {
  id: string;
  role: Role;
  display_name: string;
};

/** Where each role starts: the merchant chats, the operator works the queue, the admin
 * administers. Sending everyone to /chat showed "access denied" to operators and admins. */
export function homeFor(role: Role): string {
  return role === "merchant" ? "/chat" : role === "operator" ? "/queue" : "/admin";
}

/**
 * One query per app lifetime: the server identifies the account from the HttpOnly cookie,
 * so the SPA never carries identity in state. On 401 we return `null` so guards redirect.
 */
export function useSession() {
  return useQuery({
    queryKey: ["session"],
    staleTime: 60_000,
    retry: false,
    queryFn: async (): Promise<SessionAccount | null> => {
      try {
        return await api.get<SessionAccount>("/v1/auth/me");
      } catch (err) {
        if (isApiError(err) && err.status === 401) {
          return null;
        }
        throw err;
      }
    },
  });
}
