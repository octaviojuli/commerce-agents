"use client";

import { useParams } from "next/navigation";
import MemoryView from "@/components/MemoryView";
import { Top } from "@/components/ui";

export default function MemoryPage() {
  const { id } = useParams<{ id: string }>();
  return (
    <div className="app">
      <Top title="这一单记住的事" sub="需求、顾虑、说过的话、沟通习惯" back={`/deals/${id}`} />
      <MemoryView id={id} />
    </div>
  );
}
