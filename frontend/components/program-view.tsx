"use client";

import type { Program } from "@/lib/api/types";
import { fill, useI18n } from "@/lib/i18n/provider";

/** The week as a list of days; each day lists its exercises with sets and reps. */
export function ProgramView({ program }: { program: Program }) {
  const { dict } = useI18n();
  const t = dict.onboarding.summary;
  return (
    <ol className="grid gap-4" aria-label={t.title}>
      {program.days.map((day) => (
        <li key={day.id} className="rounded-2xl border border-border bg-card p-4">
          <div className="flex items-baseline justify-between gap-3">
            <h3 className="text-2xl font-semibold" dir="auto">
              {day.name}
            </h3>
            <span className="shrink-0 text-sm text-muted-foreground">
              {fill(t.minutes, { minutes: day.estimated_minutes })}
            </span>
          </div>
          {day.rationale && (
            <p className="mt-1 text-sm text-muted-foreground" dir="auto">
              {day.rationale}
            </p>
          )}
          <ul className="mt-3 divide-y divide-border">
            {day.exercises.map((ex) => (
              <li key={ex.id} className="flex items-center justify-between gap-3 py-2">
                <span dir="auto">{ex.name}</span>
                <span className="shrink-0 font-heading text-lg tabular-nums text-gold" dir="ltr">
                  {fill(t.sets, { sets: ex.sets, reps: `${ex.rep_min}–${ex.rep_max}` })}
                </span>
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ol>
  );
}
