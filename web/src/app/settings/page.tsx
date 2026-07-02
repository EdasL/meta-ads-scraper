import { ProductShell } from "@/components/chrome";
import { ApiKeys } from "@/components/settings/ApiKeys";
import { listKeys } from "./actions";

export default async function SettingsPage() {
  const keys = await listKeys();
  return (
    <ProductShell>
      <main style={{ maxWidth: 860, margin: "0 auto", padding: "32px 22px 64px" }}>
        <ApiKeys initialKeys={keys} />
      </main>
    </ProductShell>
  );
}
