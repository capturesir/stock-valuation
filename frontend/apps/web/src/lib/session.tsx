import { ApiError, type Prefs, type UserOut } from "@sv/api-client";
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect, type ReactNode } from "react";
import { LoginPage } from "../pages/LoginPage";
import { api, UNAUTHORIZED } from "./api";

/** The signed-in user, or null when signed out. */
export function useMe() {
  return useQuery<UserOut | null>({
    queryKey: ["me"],
    queryFn: async () => {
      try {
        return await api.me();
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 5 * 60_000,
    retry: false,
  });
}

/** Merge a preference change into the latest signed-in user and save it (no-op when signed out). */
export async function savePrefs(qc: QueryClient, patch: Partial<Prefs>): Promise<UserOut | null> {
  const me = qc.getQueryData<UserOut | null>(["me"]);
  if (!me) return null;
  qc.setQueryData(["me"], { ...me, prefs: { ...me.prefs, ...patch } });     // optimistic
  const saved = await api.updateMe({ prefs: { ...me.prefs, ...patch } as Prefs });
  qc.setQueryData(["me"], saved);
  return saved;
}

export function useIsAdmin(): boolean {
  return useMe().data?.role === "admin";
}

/** Shows the sign-in page until there is a session; drops cached data when the session ends. */
export function AuthGate({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const me = useMe();
  useEffect(() => {
    // only a session that ended mid-use: on first load /auth/me itself answers 401, and clearing
    // the cache then would refetch it and loop
    const onOut = () => {
      if (qc.getQueryData(["me"])) {
        qc.clear();
        qc.setQueryData(["me"], null);
      }
    };
    window.addEventListener(UNAUTHORIZED, onOut);
    return () => window.removeEventListener(UNAUTHORIZED, onOut);
  }, [qc]);
  if (me.isPending) return <div className="splash" aria-busy="true" />;
  // offline or API down: let the app render its cached pages instead of a sign-in form
  if (me.data === null) return <LoginPage />;
  return <>{children}</>;
}
