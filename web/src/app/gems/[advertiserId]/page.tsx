import { AppHeader } from "@/components/AppHeader";
import { Dossier } from "@/components/dossier/Dossier";

export default async function GemPage({
  params,
}: {
  params: Promise<{ advertiserId: string }>;
}) {
  const { advertiserId } = await params;
  return (
    <>
      <AppHeader />
      <Dossier advertiserId={Number(advertiserId)} />
    </>
  );
}
