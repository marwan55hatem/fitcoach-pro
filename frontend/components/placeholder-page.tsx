"use client";

import { useI18n } from "@/lib/i18n/provider";

type Section = "coach";

export function PlaceholderPage({ section }: { section: Section }) {
  const { dict } = useI18n();
  return (
    <section className="grid gap-3">
      <h1 className="text-4xl font-bold">{dict.nav[section]}</h1>
      <p className="max-w-prose text-muted-foreground">{dict.placeholders[section]}</p>
    </section>
  );
}
