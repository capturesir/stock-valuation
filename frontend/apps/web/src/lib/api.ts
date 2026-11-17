import { createClient } from "@sv/api-client";
import { currentLocale } from "./i18n";

/** Fired when the API answers 401: the session ended or expired, so the app shows the sign-in page. */
export const UNAUTHORIZED = "sv:unauthorized";

// Same origin by default (Caddy proxies /api) and signed in with an httpOnly cookie.
// Set VITE_API_BASE to call a remote API (it must list this origin in CORS_ORIGINS).
const remote = Boolean(import.meta.env.VITE_API_BASE);
export const api = createClient({
  baseUrl: import.meta.env.VITE_API_BASE ?? "",
  credentials: remote ? "include" : "same-origin",
  getLocale: currentLocale,
  onUnauthorized: () => window.dispatchEvent(new Event(UNAUTHORIZED)),
});
