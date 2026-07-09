import { ProductShell } from "@/components/chrome";
import { auth } from "@/lib/auth";
import { getProfile } from "@/lib/users";
import { AccountCard } from "@/components/settings/AccountCard";
import { ApiKeys } from "@/components/settings/ApiKeys";
import { listKeys } from "./actions";

export default async function SettingsPage() {
  const [keys, session] = await Promise.all([listKeys(), auth()]);
  const email = session?.user?.email;
  const profile = email ? await getProfile(email) : null;
  return (
    <ProductShell>
      <main style={{ maxWidth: 860, margin: "0 auto", padding: "32px 22px 64px" }}>
        {profile && <AccountCard profile={profile} />}
        <ApiKeys initialKeys={keys} />
      </main>
    </ProductShell>
  );
}
