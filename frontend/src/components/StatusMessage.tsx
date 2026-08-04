import type { ReactNode } from "react";

export function StatusMessage({
  children,
  tone = "info",
}: {
  children: ReactNode;
  tone?: "info" | "success" | "error";
}) {
  return (
    <p className={`status-message status-message--${tone}`} role={tone === "error" ? "alert" : "status"}>
      {children}
    </p>
  );
}
