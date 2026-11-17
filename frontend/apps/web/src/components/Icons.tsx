// Inline stroke icons (design system: no emoji, decorative icons are aria-hidden).
type P = { size?: number };
const S = ({ size = 18, children }: P & { children: React.ReactNode }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
);
export const Logo = ({ size = 28 }: P) => (
  <svg width={size} height={size} viewBox="0 0 28 28" aria-hidden="true">
    <rect width="28" height="28" rx="7" fill="var(--sv-primary)" />
    <polyline points="7,19 12,14 16,17 21,9" fill="none" stroke="var(--sv-on-primary)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);
export const Search = (p: P) => <S {...p}><circle cx="11" cy="11" r="7" /><line x1="16.5" y1="16.5" x2="21" y2="21" /></S>;
export const Refresh = (p: P) => <S {...p}><path d="M21 12a9 9 0 1 1-3-6.7" /><polyline points="21 3 21 9 15 9" /></S>;
export const Close = (p: P) => <S {...p}><line x1="6" y1="6" x2="18" y2="18" /><line x1="18" y1="6" x2="6" y2="18" /></S>;
export const Plus = (p: P) => <S {...p}><line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" /></S>;
export const List = (p: P) => <S {...p}><line x1="8" y1="6" x2="21" y2="6" /><line x1="8" y1="12" x2="21" y2="12" /><line x1="8" y1="18" x2="21" y2="18" /><circle cx="4" cy="6" r="1" /><circle cx="4" cy="12" r="1" /><circle cx="4" cy="18" r="1" /></S>;
export const Person = (p: P) => <S {...p}><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></S>;
export const Trend = (p: P) => <S {...p}><polyline points="3 17 9 11 13 15 21 7" /></S>;
export const Gear = (p: P) => <S {...p}><circle cx="12" cy="12" r="3" /><path d="M19 12a7 7 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7 7 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2 1.2L10 21h4l.5-2.6a7 7 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2z" /></S>;

/** Checklist state icons (design system: 工作清單狀態). */
export function StepIcon({ status }: { status: string }) {
  const c = { width: 22, height: 22, viewBox: "0 0 24 24", role: "img" as const, "aria-label": status, style: { flex: "none" } };
  switch (status) {
    case "done":
      return <svg {...c}><circle cx="12" cy="12" r="11" fill="var(--sv-success)" /><polyline points="7 12.5 10.5 16 17 9" fill="none" stroke="var(--sv-card)" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" /></svg>;
    case "cached":
      return <svg {...c}><circle cx="12" cy="12" r="11" fill="var(--sv-success-weak)" /><path d="M16.5 9.5a5 5 0 1 0 .5 4" fill="none" stroke="var(--sv-success)" strokeWidth="2" strokeLinecap="round" /><polyline points="17 6.5 17 10 13.5 10" fill="none" stroke="var(--sv-success)" strokeWidth="2" strokeLinecap="round" /></svg>;
    case "active":
      return <svg {...c}><circle cx="12" cy="12" r="9" fill="none" stroke="var(--sv-primary-band)" strokeWidth="3" /><path className="spin" d="M12 3a9 9 0 0 1 9 9" fill="none" stroke="var(--sv-primary)" strokeWidth="3" strokeLinecap="round" /></svg>;
    case "failed":
    case "cancelled":
      return <svg {...c}><circle cx="12" cy="12" r="11" fill="var(--sv-danger-weak)" /><line x1="8.5" y1="8.5" x2="15.5" y2="15.5" stroke="var(--sv-danger)" strokeWidth="2.2" strokeLinecap="round" /><line x1="15.5" y1="8.5" x2="8.5" y2="15.5" stroke="var(--sv-danger)" strokeWidth="2.2" strokeLinecap="round" /></svg>;
    case "skipped":
      return <svg {...c}><circle cx="12" cy="12" r="9" fill="none" stroke="var(--sv-ink-300)" strokeWidth="2" strokeDasharray="3 3" /><line x1="8" y1="12" x2="16" y2="12" stroke="var(--sv-ink-400)" strokeWidth="2" strokeLinecap="round" /></svg>;
    default:
      return <svg {...c}><circle cx="12" cy="12" r="9" fill="none" stroke="var(--sv-ink-300)" strokeWidth="2" /></svg>;
  }
}
