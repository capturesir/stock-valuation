import type { BrandingOut } from "@sv/api-client";
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Logo } from "../components/Icons";
import { api } from "./api";
import { useI18n } from "./i18n";

/** Public branding (works before sign-in). Falls back to the built-in name and icon. */
export function useBranding() {
  const { t } = useI18n();
  const q = useQuery<BrandingOut>({ queryKey: ["branding"], queryFn: api.branding, staleTime: 10 * 60_000 });
  const name = q.data?.name ?? t("app.name");
  useEffect(() => {
    document.title = name;
    const icon = q.data?.logo_light_url;
    const link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
    if (link) {
      link.href = icon ? api.assetUrl(icon) : "/icon.svg";
      if (icon) link.removeAttribute("type");          // PNG or SVG: let the browser sniff it
      else link.type = "image/svg+xml";
    }
  }, [name, q.data?.logo_light_url]);
  return { name, light: q.data?.logo_light_url ?? null, dark: q.data?.logo_dark_url ?? null };
}

/** Logo for the current theme: the light one, the dark one under a dark theme, or the built-in mark. */
export function BrandMark({ size = 28 }: { size?: number }) {
  const { light, dark } = useBranding();
  if (!light && !dark) return <Logo size={size} />;
  return (
    <>
      <img className="brand-logo light" src={api.assetUrl((light ?? dark)!)} alt="" height={size} />
      <img className="brand-logo dark" src={api.assetUrl((dark ?? light)!)} alt="" height={size} />
    </>
  );
}
