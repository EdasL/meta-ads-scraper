import { Suspense } from "react";
import { ProductShell } from "@/components/chrome";
import { HuntView } from "@/components/hunt/HuntView";

export default function HuntPage() {
  return (
    <ProductShell>
      {/* HuntView is a client root (thread state + live polling); the Suspense
          boundary mirrors the other product pages and keeps the shell
          prerenderable. */}
      <Suspense>
        <HuntView />
      </Suspense>
    </ProductShell>
  );
}
