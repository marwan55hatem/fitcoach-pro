"use client";

import { ChevronDown, Play } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { FormError } from "@/components/field";
import { Button } from "@/components/ui/button";
import type { ProgramDay } from "@/lib/api/types";
import { fill, useI18n } from "@/lib/i18n/provider";
import { useLocalePath } from "@/lib/i18n/use-locale-path";
import { cn } from "@/lib/utils";
import { nextDay } from "@/lib/workout/plan";
import { useLastSession, useOpenSession, useProgram, useStartWorkout } from "@/lib/workout/queries";

/** The active program's week: pick a day to see it, then start it. */
export function WeekView() {
  const { dict } = useI18n();
  const t = dict.train;
  const to = useLocalePath();
  const router = useRouter();
  const program = useProgram();
  const open = useOpenSession();
  const last = useLastSession();
  const start = useStartWorkout();
  const [expanded, setExpanded] = useState<string | null>(null);

  const upcoming = program.data ? nextDay(program.data, last.data?.program_day_id ?? null) : null;
  const begin = (day: ProgramDay) =>
    start.mutate(day.id, { onSuccess: () => router.push(to("/train/workout")) });

  return (
    <section className="grid gap-6">
      <h1 className="text-4xl font-bold">{t.title}</h1>

      {open.data && (
        <div className="flex items-center justify-between gap-3 rounded-2xl border border-primary/50 bg-primary/10 p-4">
          <p>{t.openSession}</p>
          <Button asChild className="shrink-0">
            <Link href={to("/train/workout")}>{t.continue}</Link>
          </Button>
        </div>
      )}

      {(program.isPending || last.isPending) && <p className="text-muted-foreground">{dict.common.loading}</p>}
      {(program.isError || last.isError) && (
        <div className="grid gap-3">
          <FormError message={dict.common.errorGeneric} />
          <Button variant="outline" className="w-fit" onClick={() => void program.refetch()}>
            {dict.common.retry}
          </Button>
        </div>
      )}
      {program.data === null && (
        <div className="grid gap-3 rounded-2xl border border-border bg-card p-5">
          <p>{t.noProgram}</p>
          <Button asChild className="w-fit">
            <Link href={to("/onboarding")}>{dict.today.finishSetup}</Link>
          </Button>
        </div>
      )}
      <FormError message={start.isError ? t.startError : null} />

      {program.data && last.isSuccess && (
        <div className="grid gap-3">
          <h2 className="text-2xl font-semibold">{t.week}</h2>
          <ol className="grid gap-3" aria-label={t.week}>
            {program.data.days.map((day) => {
              const isNext = day.id === upcoming?.id;
              const isOpen = expanded === day.id;
              return (
                <li
                  key={day.id}
                  className={cn(
                    "rounded-2xl border bg-card",
                    isNext ? "border-primary/60" : "border-border",
                  )}
                >
                  <div className="flex items-center gap-3 p-4">
                    <button
                      type="button"
                      aria-expanded={isOpen}
                      aria-controls={`day-${day.id}`}
                      onClick={() => setExpanded(isOpen ? null : day.id)}
                      className="flex min-h-11 flex-1 items-center gap-3 rounded-lg text-start focus-visible:outline-2 focus-visible:outline-ring"
                    >
                      <span className="font-heading text-3xl font-bold tabular-nums text-muted-foreground" dir="ltr">
                        {day.day_index}
                      </span>
                      <span className="grid flex-1">
                        <span className="flex items-center gap-2">
                          <span className="text-lg font-semibold" dir="auto">
                            {day.name}
                          </span>
                          {isNext && (
                            <span className="rounded-full bg-primary px-2 py-0.5 text-xs font-semibold text-primary-foreground">
                              {t.next}
                            </span>
                          )}
                        </span>
                        <span className="text-sm text-muted-foreground">
                          {fill(dict.today.dayLine, {
                            exercises: day.exercises.length,
                            minutes: day.estimated_minutes,
                          })}
                        </span>
                      </span>
                      <ChevronDown
                        className={cn("size-5 shrink-0 text-muted-foreground transition-transform", isOpen && "rotate-180")}
                        aria-hidden
                      />
                    </button>
                    {!open.data && (
                      <Button
                        variant={isNext ? "default" : "outline"}
                        className="shrink-0"
                        disabled={start.isPending}
                        onClick={() => begin(day)}
                        aria-label={`${t.start}: ${day.name}`}
                      >
                        <Play className="size-4" aria-hidden />
                        {t.start}
                      </Button>
                    )}
                  </div>
                  {isOpen && (
                    <ul id={`day-${day.id}`} className="divide-y divide-border border-t border-border px-4">
                      {day.exercises.map((ex) => (
                        <li key={ex.id} className="flex items-center justify-between gap-3 py-3">
                          <span className="grid">
                            <span dir="auto">{ex.name}</span>
                            <span className="text-xs text-muted-foreground">
                              {dict.muscles[ex.muscle_primary] ?? ex.muscle_primary}
                            </span>
                          </span>
                          <span className="shrink-0 font-heading text-lg tabular-nums text-gold" dir="ltr">
                            {fill(dict.onboarding.summary.sets, { sets: ex.sets, reps: `${ex.rep_min}–${ex.rep_max}` })}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              );
            })}
          </ol>
        </div>
      )}
    </section>
  );
}
