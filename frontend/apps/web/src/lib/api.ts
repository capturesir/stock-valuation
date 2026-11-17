import { createClient } from "@sv/api-client";
import { currentLocale } from "./i18n";
import { store } from "./storage";

// Same origin by default (Caddy proxies /api); set VITE_API_BASE to call a remote API.
export const api = createClient({
  baseUrl: import.meta.env.VITE_API_BASE ?? "",
  getToken: () => store.get("sv.token"),
  getLocale: currentLocale,
});
