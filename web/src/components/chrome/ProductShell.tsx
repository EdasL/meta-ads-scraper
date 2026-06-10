import { AppHeader } from "@/components/AppHeader";
import { Backdrop } from "./Backdrop";

/**
 * Shared chrome for the logged-in product (/discover, /saved, /gems/*):
 * light "paper" theme, pale fingerprint watermark, sticky app header.
 * Signed-out pages (landing, login) keep the dark backdrop from the root
 * layout; this shell's own backdrop paints over it for the product.
 */
export function ProductShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="theme-light" style={{ minHeight: "100dvh", color: "var(--ink)" }}>
      <Backdrop variant="light" />
      <AppHeader />
      {children}
    </div>
  );
}
