import LegacyWorkbench from "@/components/LegacyWorkbench";
import WarehouseWorkbench from "@/components/warehouse/Workbench";
export const dynamic = "force-dynamic";
export default function Page() {
  const mode = process.env.TOUR_BACKEND_MODE ?? "legacy";
  if (mode !== "legacy" && mode !== "warehouse")
    throw new Error("Invalid TOUR_BACKEND_MODE");
  return mode === "warehouse" ? <WarehouseWorkbench /> : <LegacyWorkbench />;
}
