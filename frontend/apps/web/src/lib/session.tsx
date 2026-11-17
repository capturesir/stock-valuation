import { ApiError, type UserOut } from "@sv/api-client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
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
