"use client";

import { useEffect } from "react";
import { useSession } from "next-auth/react";
import { setUser } from "@/lib/saved";
import { setSavedAdsUser } from "@/lib/savedAds";

/**
 * Pushes the logged-in user's email into the saved stores (gems and ads) so
 * live-mode reads/writes are keyed per user. Renders nothing.
 */
export function SavedUserSync() {
  const { data } = useSession();
  const email = data?.user?.email ?? null;
  useEffect(() => {
    setUser(email);
    setSavedAdsUser(email);
  }, [email]);
  return null;
}
