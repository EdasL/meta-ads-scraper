import { AppHeader } from "@/components/AppHeader";
import { GemDetail } from "@/components/detail/GemDetail";

export default async function GemPage({
  params,
}: {
  params: Promise<{ advertiserId: string }>;
}) {
  const { advertiserId } = await params;
  return (
    <>
      <AppHeader />
      <GemDetail advertiserId={Number(advertiserId)} />
    </>
  );
}
