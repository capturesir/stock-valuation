import "@sv/tokens/tokens.css";
import "./styles.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { I18nProvider, useI18n } from "./lib/i18n";
import { AuthGate } from "./lib/session";
import { AccountPage } from "./pages/AccountPage";
import { AdminPage } from "./pages/AdminPage";
import { SettingsPage } from "./pages/SettingsPage";
import { StockPage } from "./pages/StockPage";
import { WatchlistPage } from "./pages/WatchlistPage";

const qc = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, refetchOnWindowFocus: true, retry: 1, networkMode: "offlineFirst" } },
});

function NotFound() {
  const { t } = useI18n();
  return <main className="main"><p>404 · <Link to="/">{t("nav.watchlist")}</Link></p></main>;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <I18nProvider>
        <BrowserRouter>
          <AuthGate>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<WatchlistPage />} />
              <Route path="stock/:ticker" element={<StockPage />} />
              <Route path="stock/:ticker/:tab" element={<StockPage />} />
              <Route path="settings" element={<SettingsPage />} />
              <Route path="account" element={<AccountPage />} />
              <Route path="admin" element={<AdminPage />} />
              <Route path="*" element={<NotFound />} />
            </Route>
          </Routes>
          </AuthGate>
        </BrowserRouter>
      </I18nProvider>
    </QueryClientProvider>
  </StrictMode>,
);
