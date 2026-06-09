import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";
import { Hero } from "@/components/landing/Hero";

export default async function HomePage() {
  const session = await auth();
  if (session?.user) redirect("/discover");
  return <Hero />;
}
