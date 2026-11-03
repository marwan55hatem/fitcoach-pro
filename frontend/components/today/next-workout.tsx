"use client";

import { Play } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { FormError } from "@/components/field";
import { Button } from "@/components/ui/button";
import { fill, useI18n } from "@/lib/i18n/provider";
import { useLocalePath } from "@/lib/i18n/use-locale-path";
import { nextDay } from "@/lib/workout/plan";
import { useLastSession, useOpenSession, useProgram, useStartWorkout } from "@/lib/workout/queries";

/** The workout in progress, or the next day of the program with a start button. */
export function NextWorkout() {
  const { dict } = useI18n();
  const t = dict.today;
  const to = useLocalePath();
  const router = useRouter();
  const program = useProgram();
  const open = useOpenSession();
  const last = useLastSession();
  const start = useStartWorkout();

  if (program.isPending || open.isPending || last.isPending) {
    return <div className="h-40 animate-pulse rounded-2xl bg-card" />;
  }
  if (program.isError || open.isError || last.isError) {
    return <FormError message={dict.common.errorGeneric} />;
  }
  if (open.data) {
    const done = open.data.sets.filter((s) => s.done).length;
    return (
      <section className="grid gap-3 rounded-2xl border border-primary/60 bg-primary/10 p-5" aria-labelledby="next-title">
        <h2 id="next-title" className="text-sm font-medium text-primary">
          {t.inProgress}
        </h2>
        <p className="text-3xl font-bold" dir="auto">
          {open.data.day_name ?? dict.train.workoutTitle}
        </p>
        <p className="text-sm text-muted-foreground">
          {fill(t.inProgressLine, { done, planned: open.data.sets.length })}
        </p>
        <Button asChild size="lg" className="w-fit">
          <Link href={to("/train/workout")}>{t.continue}</Link>
        </Button>
      </section>
    );
  }
  if (!program.data) {
    return (
      <div className="grid gap-3 rounded-2xl border border-border bg-card p-5">
        <p>{t.noProgram}</p>
        <Button asChild className="w-fit">
          <Link href={to("/onboarding")}>{t.finishSetup}</Link>
        </Button>
      </div>
    );
  }
  const day = nextDay(program.data, last.data?.program_day_id ?? null);
  if (!day) return null;
  return (
    <section className="grid gap-3 rounded-2xl border border-border bg-card p-5" aria-labelledby="next-title">
      <h2 id="next-title" className="text-sm font-medium text-muted-foreground">
        {t.nextWorkout}
      </h2>
      <p className="text-3xl font-bold" dir="auto">
        {day.name}
      </p>
      <p className="text-sm text-muted-foreground">
        {fill(t.dayLine, { exercises: day.exercises.length, minutes: day.estimated_minutes })}
      </p>
      <ul className="flex flex-wrap gap-1.5" aria-label={day.name}>
        {day.exercises.map((ex) => (
          <li key={ex.id} className="rounded-full bg-muted px-3 py-1 text-sm text-muted-foreground" dir="auto">
            {ex.name}
          </li>
        ))}
      </ul>
      <FormError message={start.isError ? dict.train.startError : null} />
      <Button
        size="lg"
        className="w-fit"
        disabled={start.isPending}
        onClick={() => start.mutate(day.id, { onSuccess: () => router.push(to("/train/workout")) })}
      >
        <Play className="size-4" aria-hidden />
        {t.startWorkout}
      </Button>
    </section>
  );
}
