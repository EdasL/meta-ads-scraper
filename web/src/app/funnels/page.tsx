import { Suspense } from "react";
import { ProductShell } from "@/components/chrome";
import { FunnelsFeed } from "@/components/funnels/FunnelsFeed";

export default function FunnelsPage() {
  return (
    <ProductShell>
      {/* FunnelsFeed reads useSearchParams (filters live in the URL), which
          requires a Suspense boundary to keep the shell prerenderable. */}
      <Suspense>
        <FunnelsFeed />
      </Suspense>
    </ProductShell>
  );
}
