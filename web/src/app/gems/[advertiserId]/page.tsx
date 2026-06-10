import { ProductShell } from "@/components/chrome";
import { GemDetail } from "@/components/detail/GemDetail";

export default async function GemPage({
  params,
}: {
  params: Promise<{ advertiserId: string }>;
}) {
  const { advertiserId } = await params;
  return (
    <ProductShell>
      <GemDetail advertiserId={Number(advertiserId)} />
    </ProductShell>
  );
}
