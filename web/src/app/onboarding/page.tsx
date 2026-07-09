import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";
import { getProfile } from "@/lib/users";
import { Backdrop } from "@/components/chrome/Backdrop";
import { OnboardingFlow } from "@/components/onboarding/OnboardingFlow";

export default async function OnboardingPage() {
  const session = await auth();
  const email = session?.user?.email;
  if (!email) redirect("/login");
  const me = await getProfile(email);
  if (me && me.onboarded_at !== null) redirect("/discover");

  return (
    <div className="theme-light" style={{ minHeight: "100dvh", color: "var(--ink)" }}>
      <Backdrop variant="light" />
      <OnboardingFlow />
    </div>
  );
}
