import { createFormat, createT, detectLocale, type Format, type Locale, type Translate } from "@sv/i18n";
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { store } from "./storage";

interface I18n { locale: Locale; setLocale: (l: Locale) => void; t: Translate; f: Format }
const Ctx = createContext<I18n | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocale] = useState<Locale>(
    () => (store.get("sv.locale") as Locale | null) ?? detectLocale(navigator.languages),
  );
  useEffect(() => {
    store.set("sv.locale", locale);
    document.documentElement.lang = locale;      // document.title follows branding (lib/branding.tsx)
  }, [locale]);
  const value = useMemo(() => ({ locale, setLocale, t: createT(locale), f: createFormat(locale) }), [locale]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useI18n(): I18n {
  const v = useContext(Ctx);
  if (!v) throw new Error("useI18n outside I18nProvider");
  return v;
}

/** Current locale for code outside React (the API client's Accept-Language header). */
export const currentLocale = () => (store.get("sv.locale") as Locale | null) ?? detectLocale(navigator.languages);
