import SharedPlan from "./shared-plan";
export const metadata = {
  title: "旅行方案",
  robots: { index: false, follow: false },
  referrer: "no-referrer",
};
export default function Page() {
  return <SharedPlan />;
}
