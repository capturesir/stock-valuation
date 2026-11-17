import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "./api";
import { useMe } from "./session";
import { store } from "./storage";

/** light (default, as in the design), dark, or follow the operating system. */
export type Theme = "light" | "dark" | "system";
export const THEMES: Theme[] = ["light", "dark", "system"];
const KEY = "sv.theme";
const DEFAULT: Theme = "light";

/** Same logic as the inline script in index.html, which runs before React to avoid a flash. */
export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
}

const isDark = (theme: Theme) =>
  theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);

interface Ctx { theme: Theme; setTheme: (t: Theme) => void; dark: boolean; version: number }
const ThemeCtx = createContext<Ctx | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, set] = useState<Theme>(() => (THEMES.includes(store.get(KEY) as Theme) ? (store.get(KEY) as Theme) : DEFAULT));
  const [dark, setDark] = useState(() => isDark(theme));
  const [version, setVersion] = useState(0);
  const me = useMe().data;

  // the account's choice wins when signing in on another device
  useEffect(() => {
    const saved = me?.prefs?.theme as Theme | null | undefined;
    if (saved && THEMES.includes(saved) && saved !== theme) set(saved);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me?.id, me?.prefs?.theme]);

  useEffect(() => {
    applyTheme(theme);
    store.set(KEY, theme);
    setDark(isDark(theme));
    setVersion((v) => v + 1);
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => { setDark(mq.matches); setVersion((v) => v + 1); };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);

  const setTheme = useCallback((t: Theme) => {
    set(t);
    if (me) void api.updateMe({ prefs: { ...me.prefs, theme: t } }).catch(() => { /* kept locally anyway */ });
  }, [me]);

  const value = useMemo(() => ({ theme, setTheme, dark, version }), [theme, setTheme, dark, version]);
  return <ThemeCtx.Provider value={value}>{children}</ThemeCtx.Provider>;
}

export function useTheme(): Ctx {
  const v = useContext(ThemeCtx);
  if (!v) throw new Error("useTheme outside ThemeProvider");
  return v;
}
