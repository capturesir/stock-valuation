// Generate platform files from tokens.json (no dependencies).
//   dist/tokens.css   CSS custom properties for web / PWA (light, dark, auto)
//   dist/tokens.js    ES module for React / React Native / Taro
//   dist/tokens.d.ts  types for the module above
//   dist/tokens.wxss  WeChat mini program page variables (px × 2 = rpx at 750 design width)
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const t = JSON.parse(readFileSync(join(here, "tokens.json"), "utf8"));
const out = join(here, "dist");
mkdirSync(out, { recursive: true });

const kebab = (s) => s.replace(/([a-z])([A-Z0-9])/g, "$1-$2").replace(/([0-9])([A-Za-z])/g, "$1-$2").toLowerCase();
const colorVars = (palette) =>
  Object.entries(palette).map(([k, v]) => `  --sv-${kebab(k)}: ${v};`).join("\n");

const chartVars = (theme) =>
  Object.entries(t.color.chart)
    .filter(([, v]) => typeof v === "string" && !v.startsWith("#"))
    .map(([k, v]) => `  --sv-chart-${kebab(k)}: var(--sv-${kebab(v)});`)
    .concat(t.color.chart[theme === "dark" ? "heatDark" : "heatLight"].map((c, i) => `  --sv-heat-${i}: ${c};`))
    .concat([`  --sv-heat-text: ${t.color.chart[theme === "dark" ? "heatTextDark" : "heatTextLight"]};`])
    .join("\n");

// scale keys keep digits attached ("2xl" → --sv-fs-2xl); only camelCase is split
const key = (k) => String(k).replace(/([a-z])([A-Z])/g, "$1-$2").toLowerCase();
const scale = (prefix, obj, unit = "px") =>
  Object.entries(obj).map(([k, v]) => `  --sv-${prefix}-${key(k)}: ${typeof v === "number" ? v + unit : v};`).join("\n");

const shared = [
  `  --sv-font-sans: ${t.font.sans};`,
  `  --sv-font-mono: ${t.font.mono};`,
  scale("fs", t.font.size),
  scale("fw", t.font.weight, ""),
  scale("lh", t.font.lineHeight, ""),
  scale("space", t.space),
  scale("radius", t.radius),
  scale("shadow", t.shadow, ""),
  scale("size", t.size),
  scale("motion", t.motion, ""),
].join("\n");

const css = `/* Generated from tokens.json v${t.$meta.version} — do not edit by hand. */
:root {
  color-scheme: light;
${colorVars(t.color.light)}
${chartVars("light")}
${shared}
}
:root[data-theme="dark"] {
  color-scheme: dark;
${colorVars(t.color.dark)}
${chartVars("dark")}
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
${colorVars(t.color.dark).replace(/^/gm, "  ")}
${chartVars("dark").replace(/^/gm, "  ")}
  }
}
`;
writeFileSync(join(out, "tokens.css"), css);

const js = `// Generated from tokens.json v${t.$meta.version} — do not edit by hand.
export const tokens = ${JSON.stringify(t, null, 2)};
export const light = tokens.color.light;
export const dark = tokens.color.dark;
export default tokens;
`;
writeFileSync(join(out, "tokens.js"), js);
writeFileSync(
  join(out, "tokens.d.ts"),
  `// Generated — do not edit.\ntype Tokens = ${JSON.stringify(t, null, 2).replace(/"([^"]+)":/g, '"$1":')};\nexport declare const tokens: any;\nexport declare const light: Record<string, string>;\nexport declare const dark: Record<string, string>;\nexport default tokens;\nexport type { Tokens };\n`
);

// Mini program: px → rpx (×2) for sizes; colours unchanged.
const rpx = (obj, prefix) =>
  Object.entries(obj).map(([k, v]) => `  --sv-${prefix}-${key(k)}: ${typeof v === "number" ? v * 2 + "rpx" : v};`).join("\n");
const wxss = `/* Generated from tokens.json v${t.$meta.version} — WeChat mini program. */
page {
${colorVars(t.color.light)}
${rpx(t.font.size, "fs")}
${rpx(t.space, "space")}
${rpx(t.radius, "radius")}
${rpx(t.size, "size")}
}
`;
writeFileSync(join(out, "tokens.wxss"), wxss);
// Design-system reference page: the template with the generated CSS and token JSON inlined,
// so the page is self-contained and always matches tokens.json.
const tpl = readFileSync(join(here, "design-system.template.html"), "utf8");
const page = tpl.replace("/*TOKENS*/", css).replace("/*TOKENJSON*/", JSON.stringify(t));
const docs = join(here, "..", "..", "..", "docs");
mkdirSync(docs, { recursive: true });
writeFileSync(join(docs, "design-system.html"), page);
console.log("tokens: wrote dist/tokens.css, tokens.js, tokens.d.ts, tokens.wxss and docs/design-system.html");
