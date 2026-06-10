import { Suspense } from "react";
import { ProductShell } from "@/components/chrome";
import { DiscoverFeed } from "@/components/discover/DiscoverFeed";

export default function DiscoverPage() {
  return (
    <ProductShell>
      {/* DiscoverFeed reads useSearchParams (the filter set lives in the URL),
          which requires a Suspense boundary to keep the shell prerenderable. */}
      <Suspense>
        <DiscoverFeed />
      </Suspense>
    </ProductShell>
  );
}
