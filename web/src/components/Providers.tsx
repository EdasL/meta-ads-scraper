"use client";

import type { ReactNode } from "react";
import { SessionProvider } from "next-auth/react";
import { SavedUserSync } from "./SavedUserSync";

/** Client providers mounted once at the root: next-auth session + the saved-store
 * user sync. */
export function Providers({ children }: { children: ReactNode }) {
  return (
    <SessionProvider>
      <SavedUserSync />
      {children}
    </SessionProvider>
  );
}
