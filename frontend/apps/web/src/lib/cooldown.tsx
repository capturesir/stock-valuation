import { ApiError } from "@sv/api-client";
import { useCallback, useEffect, useState } from "react";

/** Server-side limits answer 429/503 with retry_after; count it down and keep the button disabled. */
export function useCooldown() {
  const [until, setUntil] = useState(0);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (until <= now) return;
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, [until, now]);
  const left = Math.max(0, Math.ceil((until - now) / 1000));
  /** Start a cool-down from an API error; returns true when the error was a limit. */
  const from = useCallback((e: unknown): boolean => {
    const secs = e instanceof ApiError ? Number(e.detail?.retry_after) : NaN;
    if (!Number.isFinite(secs) || secs <= 0) return false;
    setUntil(Date.now() + secs * 1000);
    setNow(Date.now());
    return true;
  }, []);
  return { left, from };
}
