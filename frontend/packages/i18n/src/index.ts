// Framework-free i18n shared by every frontend (web/PWA, React Native, Taro mini program).
// Add a language: copy locales/zh-Hant.json, translate the values, register it below.
import en from "./locales/en.json";
import zhHans from "./locales/zh-Hans.json";
import zhHant from "./locales/zh-Hant.json";

export type Locale = "zh-Hant" | "zh-Hans" | "en";
export type MessageKey = keyof typeof zhHant;

export const LOCALES: { id: Locale; label: string; intl: string }[] = [
  { id: "zh-Hant", label: "繁體中文", intl: "zh-Hant-HK" },
  { id: "zh-Hans", label: "简体中文", intl: "zh-Hans-CN" },
  { id: "en", label: "English", intl: "en-US" },
];
export const DEFAULT_LOCALE: Locale = "zh-Hant";

const catalogs: Record<Locale, Record<string, string>> = { "zh-Hant": zhHant, "zh-Hans": zhHans, en };

/** Map browser/OS language tags to a supported locale (zh-TW/HK/MO → zh-Hant, zh-CN/SG → zh-Hans). */
export function detectLocale(tags: readonly string[] | undefined): Locale {
  for (const raw of tags ?? []) {
    const tag = raw.toLowerCase();
    if (tag.startsWith("zh-hant") || ["zh-tw", "zh-hk", "zh-mo"].includes(tag)) return "zh-Hant";
    if (tag.startsWith("zh-hans") || ["zh-cn", "zh-sg", "zh"].includes(tag)) return "zh-Hans";
    if (tag.startsWith("en")) return "en";
  }
  return DEFAULT_LOCALE;
}

export type Vars = Record<string, string | number | null | undefined>;
export type Translate = (key: MessageKey | string, vars?: Vars) => string;

/** t("run.startWithCost", { cost: "US$0.02" }); falls back to zh-Hant, then to the key itself. */
export function createT(locale: Locale): Translate {
  const cat = catalogs[locale] ?? catalogs[DEFAULT_LOCALE];
  return (key, vars) => {
    const text = cat[key] ?? catalogs[DEFAULT_LOCALE][key] ?? key;
    return vars ? text.replace(/\{(\w+)\}/g, (_, k) => (vars[k] ?? "").toString()) : text;
  };
}

export function intlTag(locale: Locale): string {
  return LOCALES.find((l) => l.id === locale)?.intl ?? "zh-Hant-HK";
}

// ------------------------------------------------------------------ formats (design system: 文案與數字格式)

const SYMBOLS: Record<string, string> = { HKD: "HK$", USD: "US$", JPY: "¥", CNY: "RMB ", TWD: "NT$", KRW: "₩", EUR: "€", GBP: "£" };
const MINUS = "−";

export function createFormat(locale: Locale) {
  const tag = intlTag(locale);
  const num = (v: number, digits = 2) =>
    new Intl.NumberFormat(tag, { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(v);
  const sign = (v: number, s: string) => (v > 0 ? `+${s}` : v < 0 ? `${MINUS}${s.replace("-", "")}` : s);
  return {
    /** HK$22.72 · ¥3,410 (JPY/KRW without decimals) */
    money(v: number | null | undefined, ccy?: string | null): string {
      if (v == null || !Number.isFinite(v)) return "—";
      const c = (ccy ?? "").toUpperCase();
      const digits = c === "JPY" || c === "KRW" ? 0 : 2;
      const s = num(Math.abs(v), digits);
      return `${v < 0 ? MINUS : ""}${SYMBOLS[c] ?? (c ? c + " " : "")}${s}`;
    },
    /** 38.40 */
    value(v: number | null | undefined, digits = 2): string {
      return v == null || !Number.isFinite(v) ? "—" : num(v, digits);
    },
    /** +56.3% · −9.6% (ratio in, signed out) */
    pct(ratio: number | null | undefined, digits = 1, signed = true): string {
      if (ratio == null || !Number.isFinite(ratio)) return "—";
      const s = `${num(Math.abs(ratio) * 100, digits)}%`;
      return signed ? sign(ratio, s) : `${ratio < 0 ? MINUS : ""}${s}`;
    },
    /** +0.90 */
    delta(v: number | null | undefined, digits = 2): string {
      return v == null || !Number.isFinite(v) ? "—" : sign(v, num(Math.abs(v), digits));
    },
    /** US$0.021 under a dollar, US$1.23 above */
    usd(v: number | null | undefined): string {
      if (v == null || !Number.isFinite(v)) return "—";
      return `US$${num(v, v !== 0 && Math.abs(v) < 1 ? 3 : 2)}`;
    },
    /** 12.3k · 1.03M */
    tokens(n: number | null | undefined): string {
      if (n == null) return "—";
      if (n >= 1e6) return `${num(n / 1e6, 2)}M`;
      if (n >= 1e3) return `${num(n / 1e3, 1)}k`;
      return String(n);
    },
    /** 2026-11-17 14:30 (ISO in, local wall time out) */
    datetime(iso: string | null | undefined): string {
      if (!iso) return "—";
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return iso;
      const p = (x: number) => String(x).padStart(2, "0");
      return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
    },
    duration(seconds: number): string {
      const m = Math.floor(seconds / 60), s = Math.floor(seconds % 60);
      return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    },
  };
}
export type Format = ReturnType<typeof createFormat>;
