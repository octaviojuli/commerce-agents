import { notFound } from "next/navigation";
import LegacyShowcase from "@/components/LegacyShowcase";
export const dynamic = "force-dynamic";
export default function Page() {
  if (process.env.TOUR_BACKEND_MODE === "warehouse") notFound();
  return <LegacyShowcase />;
}
