import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";
import { getProfile } from "@/lib/users";
import { AppHeader } from "@/components/AppHeader";
import { Backdrop } from "./Backdrop";

/**
 * Shared chrome for the logged-in product (/discover, /saved, /gems/*):
 * light "paper" theme, pale fingerprint watermark, sticky app header.
 * Also the onboarding gate: a signed-in user who has not finished (or
 * skipped) first-run onboarding is redirected to /onboarding — which renders
 * WITHOUT this shell, so there is no redirect loop. The profile read fails
 * open: if /me is unreachable the app renders normally.
 */
export async function ProductShell({ children }: { children: React.ReactNode }) {
  const session = await auth();
  const email = session?.user?.email;
  if (email) {
    const me = await getProfile(email);
    if (me && me.onboarded_at === null) redirect("/onboarding");
  }
  return (
    <div className="theme-light" style={{ minHeight: "100dvh", color: "var(--ink)" }}>
      <Backdrop variant="light" />
      <AppHeader />
      {children}
    </div>
  );
}
