"use client";

import type { FormEvent, ReactNode } from "react";

import { FormError } from "@/components/field";
import { Button } from "@/components/ui/button";

/** One wizard screen: a heading, the questions, and a primary action pinned in reach. */
export function StepFrame({
  title,
  lead,
  children,
  action,
  busy,
  error,
  onSubmit,
  secondary,
}: {
  title: string;
  lead?: string;
  children: ReactNode;
  action: string;
  busy?: boolean;
  error?: string | null;
  onSubmit: () => void;
  secondary?: ReactNode;
}) {
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit();
  };
  return (
    <form onSubmit={submit} noValidate className="flex flex-1 flex-col">
      <div className="grid gap-1">
        <h1 className="text-4xl font-bold">{title}</h1>
        {lead && <p className="max-w-prose text-muted-foreground">{lead}</p>}
      </div>
      <div className="mt-6 grid gap-6">{children}</div>
      <div className="sticky bottom-0 mt-auto grid gap-3 bg-gradient-to-t from-background from-70% to-transparent pb-[max(1rem,env(safe-area-inset-bottom))] pt-6">
        <FormError message={error ?? null} />
        <Button type="submit" size="lg" disabled={busy} className="w-full">
          {action}
        </Button>
        {secondary}
      </div>
    </form>
  );
}
