import { useRegisterSW } from "virtual:pwa-register/react";
import { useI18n } from "../lib/i18n";

/** Shows a bar when a new version has been downloaded; the user decides when to reload. */
export function PwaPrompt() {
  const { t } = useI18n();
  const { needRefresh: [needRefresh], updateServiceWorker } = useRegisterSW();
  if (!needRefresh) return null;
  return (
    <div role="status" className="banner info" style={{ position: "fixed", left: 16, right: 16, bottom: 84, zIndex: 50, boxShadow: "var(--sv-shadow-popover)" }}>
      <span style={{ flex: 1 }}>{t("pwa.update")}</span>
      <button className="btn primary small" type="button" onClick={() => updateServiceWorker(true)}>{t("pwa.reload")}</button>
    </div>
  );
}
