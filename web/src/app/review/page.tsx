import { Suspense } from "react";
import { ProductShell } from "@/components/chrome";
import { ReviewDeck } from "@/components/review/ReviewDeck";

export default function ReviewPage() {
  return (
    <ProductShell>
      {/* ReviewDeck reads session + fetches queue client-side;
          Suspense keeps the shell prerenderable. */}
      <Suspense>
        <ReviewDeck />
      </Suspense>
    </ProductShell>
  );
}
