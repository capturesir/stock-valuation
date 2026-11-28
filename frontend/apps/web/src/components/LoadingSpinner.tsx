import type { CSSProperties } from "react";

export function LoadingSpinner({
  label,
  size = "md",
  className = "",
  style,
}: {
  label?: string;
  size?: "sm" | "md" | "lg";
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <progress
      aria-label={label ?? "Loading"}
      className={`loading-spinner loading-spinner-${size}${className ? ` ${className}` : ""}`}
      style={style}
    />
  );
}
