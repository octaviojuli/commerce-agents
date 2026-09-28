"use client";
import { useState, type ReactNode } from "react";
import { Sheet } from "./mobile";
export default function CopilotFormSheet({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>{title}</button>
      {open && (
        <Sheet title={title} close={() => setOpen(false)}>
          {children}
        </Sheet>
      )}
    </>
  );
}
